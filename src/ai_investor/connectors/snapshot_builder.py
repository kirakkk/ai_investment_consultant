"""Snapshot Builder — assemble connector outputs into pipeline-ready DataFrame.

Calls each DataConnector method, joins the results, fills gaps,
computes cross-sectional metrics, and produces a 37-column Polars
DataFrame suitable for the strategy kernel pipeline.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from ai_investor.connectors.protocol import DataConnector

logger = logging.getLogger(__name__)

# All metrics expected by the pipeline
REQUIRED_COLUMNS = [
    "ticker", "name", "exchange", "board", "security_type",
    "industry_l1", "industry_l2", "industry_anchor",
    "listed_trading_days", "adv20_amount_cny", "total_equity",
    "has_latest_financials", "has_standard_unqualified_audit",
    "is_st", "is_delisting", "is_suspended",
    "roe_ttm", "gross_margin_stability_12q", "asset_turnover_delta",
    "cfo_to_net_profit_ttm", "fcf_ttm_margin", "net_debt_to_ebitda",
    "receivable_inventory_anomaly",
    "revenue_yoy_acceleration", "profit_yoy_acceleration",
    "operating_margin_delta", "industry_regime_strength",
    "revenue_cagr_3y", "profit_cagr_3y",
    "pe_pctile_in_industry", "pb_pctile_in_industry",
    "ev_ebitda_pctile_in_industry",
    "eps_yield", "bps_yield", "cf_yield", "peg_ratio",
    "size_bucket",
    "rs_60d", "ma_structure", "turnover_support",
    "risk_goodwill_high", "risk_equity_pledge_high",
    "risk_regulatory_probe", "risk_major_reduction",
    "risk_material_negative_announcement",
]


def build_snapshot(
    connector: DataConnector,
    *,
    max_tickers: int | None = None,
    save_path: Path | None = None,
) -> pl.DataFrame:
    """Build a complete PIT snapshot from a DataConnector.

    Args:
        connector: DataConnector implementation to fetch data from.
        max_tickers: Optional limit on number of tickers to process
                     (useful for testing).
        save_path: Optional path to save the snapshot as Parquet.

    Returns:
        Polars DataFrame with all 37 required columns.
    """
    from datetime import date

    cache_dir = Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    def _cached_fetch(name: str, fetch_fn, *args):
        """Fetch with daily Parquet caching."""
        cache_path = cache_dir / f"{name}_{today}.parquet"
        if cache_path.exists():
            logger.info(f"  [cache hit] Loading {name} from {cache_path}")
            return pl.read_parquet(cache_path)
        result = fetch_fn(*args)
        try:
            result.write_parquet(cache_path)
            logger.info(f"  [cache save] Saved {name} to {cache_path}")
        except Exception as e:
            logger.warning(f"  [cache save failed] {name}: {e}")
        return result

    # Step 1: Get stock list
    logger.info("=== Building PIT Snapshot ===")
    stock_list = _cached_fetch("stock_list", connector.fetch_stock_list)
    tickers = stock_list["ticker"].to_list()

    if max_tickers:
        tickers = tickers[:max_tickers]
        stock_list = stock_list.filter(pl.col("ticker").is_in(tickers))
        logger.info(f"Limited to {max_tickers} tickers for testing")

    logger.info(f"Total tickers: {len(tickers)}")

    # Step 2: Fetch all data sources (with caching)
    suffix = f"_{max_tickers}" if max_tickers else "_full"

    logger.info("Fetching risk flags...")
    risk_flags = _cached_fetch(f"risk_flags{suffix}", connector.fetch_risk_flags, tickers)

    logger.info("Fetching industry classification...")
    industry = _cached_fetch(f"industry{suffix}", connector.fetch_industry, tickers)

    logger.info("Fetching financials...")
    financials = _cached_fetch(f"financials{suffix}", connector.fetch_financials, tickers)

    logger.info("Fetching market data...")
    market_data = _cached_fetch(f"market_data{suffix}", connector.fetch_market_data, tickers)

    logger.info("Fetching valuation data...")
    valuation = _cached_fetch(f"valuation{suffix}", connector.fetch_valuation, tickers)

    # Step 3: Join all data sources on ticker
    logger.info("Joining data sources...")
    df = stock_list

    for source_df in [risk_flags, industry, financials, market_data, valuation]:
        # Only join columns not already present (except ticker)
        existing_cols = set(df.columns)
        new_cols = [c for c in source_df.columns if c not in existing_cols or c == "ticker"]
        if len(new_cols) > 1:  # More than just "ticker"
            df = df.join(
                source_df.select(new_cols),
                on="ticker",
                how="left",
            )

    # Step 4: Ensure all required columns exist
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            # Add missing column with null
            if col in ("is_st", "is_delisting", "is_suspended",
                       "has_latest_financials", "has_standard_unqualified_audit",
                       "risk_goodwill_high", "risk_equity_pledge_high",
                       "risk_regulatory_probe", "risk_major_reduction",
                       "risk_material_negative_announcement"):
                df = df.with_columns(pl.lit(False).alias(col))
            elif col in ("listed_trading_days",):
                df = df.with_columns(pl.lit(0).alias(col))
            else:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

    # Step 5: Compute cross-sectional metrics (valuation percentiles, regime)
    df = _compute_cross_sectional_metrics(df)

    # Step 6: Select only required columns in order
    df = df.select(REQUIRED_COLUMNS)

    logger.info(f"Snapshot shape: {df.height} rows x {df.width} columns")
    _log_completeness(df)

    # Step 7: Save if path provided
    if save_path:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(save_path)
        logger.info(f"Saved snapshot to {save_path}")

        # Also save a CSV for inspection
        csv_path = save_path.with_suffix(".csv")
        df.write_csv(csv_path)
        logger.info(f"Saved CSV copy to {csv_path}")

    return df


def _compute_cross_sectional_metrics(df: pl.DataFrame) -> pl.DataFrame:
    """Compute metrics that require cross-sectional (all tickers) context.

    - pe_pctile_in_industry: percentile rank of PE within industry_l1 group
    - pb_pctile_in_industry: percentile rank of PB within industry_l1 group
    - ev_ebitda_pctile_in_industry: proxied by PE percentile (MVP2 simplification)
    - industry_regime_strength: relative RS60d ranking within industry vs market
    """
    # --- Compute raw valuation metrics (PE/PB) ---
    # We use close price from market_data and eps_ttm/bps from financials
    if all(c in df.columns for c in ["close", "eps_ttm", "bps"]):
        df = df.with_columns(
            pl.when(pl.col("eps_ttm").is_not_null() & (pl.col("eps_ttm") > 0))
            .then(pl.col("close") / pl.col("eps_ttm"))
            .otherwise(None)
            .alias("pe_ttm_raw"),

            pl.when(pl.col("bps").is_not_null() & (pl.col("bps") > 0))
            .then(pl.col("close") / pl.col("bps"))
            .otherwise(None)
            .alias("pb_raw"),
        )

    # Calculate Valuation Yields (E/P, B/P, CF/P)
    if "pe_ttm_raw" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("pe_ttm_raw").is_not_null() & (pl.col("pe_ttm_raw") > 0))
            .then(1.0 / pl.col("pe_ttm_raw"))
            .otherwise(None)
            .alias("eps_yield")
        )
    else:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("eps_yield"))

    if "pb_raw" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("pb_raw").is_not_null() & (pl.col("pb_raw") > 0))
            .then(1.0 / pl.col("pb_raw"))
            .otherwise(None)
            .alias("bps_yield")
        )
    else:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("bps_yield"))

    if "eps_yield" in df.columns and "cfo_to_net_profit_ttm" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("eps_yield").is_not_null() & pl.col("cfo_to_net_profit_ttm").is_not_null())
            .then(pl.col("eps_yield") * pl.col("cfo_to_net_profit_ttm"))
            .otherwise(None)
            .alias("cf_yield")
        )
    else:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("cf_yield"))

    # Calculate PEG ratio
    # Provide a reasonable floor for profit CAGR (e.g. 1%) to avoid div by zero or negative PEG
    if "pe_ttm_raw" in df.columns and "profit_cagr_3y" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("pe_ttm_raw").is_not_null() & pl.col("profit_cagr_3y").is_not_null())
            .then(pl.col("pe_ttm_raw") / (pl.max_horizontal(pl.col("profit_cagr_3y"), 0.01) * 100.0))
            .otherwise(None)
            .alias("peg_ratio")
        )
    else:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("peg_ratio"))

    # --- Valuation percentiles by industry ---
    has_pe = "pe_ttm_raw" in df.columns
    has_pb = "pb_raw" in df.columns

    if has_pe and "industry_l1" in df.columns:
        # Compute PE percentile within industry_l1 group
        df = df.with_columns(
            pl.col("pe_ttm_raw")
            .rank(method="average")
            .over("industry_l1")
            .truediv(pl.col("pe_ttm_raw").count().over("industry_l1"))
            .alias("pe_pctile_in_industry")
        )
    if has_pe and "industry_l1" in df.columns:
        # Use PE percentile as EV/EBITDA proxy
        df = df.with_columns(
            pl.col("pe_pctile_in_industry").alias("ev_ebitda_pctile_in_industry")
        )

    if has_pb and "industry_l1" in df.columns:
        df = df.with_columns(
            pl.col("pb_raw")
            .rank(method="average")
            .over("industry_l1")
            .truediv(pl.col("pb_raw").count().over("industry_l1"))
            .alias("pb_pctile_in_industry")
        )

    # --- Size Bucket (Neutralization) ---
    # Use total_equity or close * shares if available. We will use total_equity as a proxy for size.
    if "total_equity" in df.columns:
        df = df.with_columns(
            pl.col("total_equity").rank(method="average", descending=True).alias("_rank_tmp")
        )
        total_valid = df.filter(pl.col("total_equity").is_not_null()).height
        df = df.with_columns(
            pl.when(pl.col("_rank_tmp") <= total_valid * 0.2).then(pl.lit("large"))
            .when(pl.col("_rank_tmp") <= total_valid * 0.5).then(pl.lit("medium"))
            .otherwise(pl.lit("small"))
            .alias("size_bucket")
        ).drop("_rank_tmp")
    else:
        df = df.with_columns(pl.lit("medium").alias("size_bucket"))

    # --- Industry regime strength ---
    # Use median RS 60d within industry vs overall market median
    if "rs_60d" in df.columns and "industry_l1" in df.columns:
        market_median = df["rs_60d"].median()
        if market_median is not None and market_median > 0:
            df = df.with_columns(
                (pl.col("rs_60d").median().over("industry_l1") / market_median)
                .clip(0.0, 2.0)
                .truediv(2.0)
                .alias("industry_regime_strength")
            )

    # Drop raw columns not in REQUIRED_COLUMNS
    drop_cols = [c for c in ["pe_ttm_raw", "pb_raw", "market_cap"] if c in df.columns]
    if drop_cols:
        df = df.drop(drop_cols)

    return df


def _log_completeness(df: pl.DataFrame) -> None:
    """Log data completeness summary."""
    total = df.height
    for col in df.columns:
        null_count = df[col].null_count()
        pct = (1 - null_count / total) * 100 if total > 0 else 0
        if null_count > 0:
            logger.info(f"  {col}: {pct:.0f}% complete ({null_count} nulls)")


def compute_source_hash(df: pl.DataFrame) -> str:
    """Compute a hash of the snapshot for audit trail."""
    raw = df.to_pandas().to_csv(index=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
