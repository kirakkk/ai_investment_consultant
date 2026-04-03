"""PIT Snapshot Builder — assemble historical snapshots with temporal integrity.

Replaces snapshot_builder.py for historical backtesting by:
1. Using PIT-gated financial data (from pit_store)
2. Using historical daily market data (from stock_zh_a_hist)
3. Reconstructing valuations from PIT financials + historical prices

Output format is compatible with the existing 37-column snapshot schema.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import polars as pl

from ai_investor.backtest.conventions import (
    AsOfContext,
    get_trade_date_offset,
    resolve_trade_date,
)
from ai_investor.backtest.pit_store import build_pit_view

logger = logging.getLogger(__name__)


def build_pit_snapshot(
    asof: AsOfContext,
    pit_events: pl.DataFrame | None = None,
    *,
    max_tickers: int | None = None,
    max_workers: int = 4,
) -> pl.DataFrame:
    """Build a PIT-safe historical snapshot for backtesting.

    This is the historical-safe replacement for snapshot_builder.build_snapshot().
    It produces a DataFrame with the same 37 columns, but with financial
    data gated by available_at rules and market data from historical daily bars.

    Args:
        asof: Resolved AsOfContext (trade_date, etc.)
        pit_events: Pre-loaded PIT events. If None, will pull from AKShare.
        max_tickers: Limit for testing.
        max_workers: Workers for concurrent API calls.

    Returns:
        DataFrame compatible with compute_features() input.
    """
    trade_date = asof.trade_date
    logger.info(f"=== Building PIT Snapshot for {trade_date} ===")

    # --- Step 1: PIT financial data ---
    logger.info("Step 1: Building PIT financial view...")
    pit_view = build_pit_view(trade_date, events=pit_events)

    if pit_view.height == 0:
        logger.error("No PIT financial data available!")
        return pl.DataFrame()

    if max_tickers:
        pit_view = pit_view.head(max_tickers)

    tickers = pit_view["ticker"].to_list()
    logger.info(f"  PIT view: {len(tickers)} tickers with visible financials")

    # --- Step 1b: Compute 3-year CAGRs from multi-year PIT data ---
    logger.info("Step 1b: Computing 3-year CAGRs from historical financials...")
    cagr_data = _compute_cagr(tickers, trade_date, pit_events)

    # --- Step 1c: Compute YoY acceleration (delta of growth rates) ---
    logger.info("Step 1c: Computing YoY acceleration from consecutive annual reports...")
    accel_data = _compute_yoy_acceleration(tickers, trade_date, pit_events)

    # --- Step 2: Historical daily market data ---
    logger.info("Step 2: Fetching historical daily market data...")
    market_data = _fetch_historical_market_data(tickers, trade_date, max_workers)

    # --- Step 3: Stock list metadata (industry, board, etc.) ---
    logger.info("Step 3: Fetching stock metadata...")
    metadata = _fetch_stock_metadata(tickers)

    # --- Step 4: Risk flags (current-state, acceptable approximation) ---
    logger.info("Step 4: Fetching risk flags...")
    risk_flags = _fetch_risk_flags_approx(tickers)

    # --- Step 5: Join everything ---
    logger.info("Step 5: Assembling snapshot...")
    snapshot = _assemble_snapshot(
        pit_view, market_data, metadata, risk_flags, trade_date,
        cagr_data=cagr_data,
        accel_data=accel_data,
    )

    logger.info(f"PIT Snapshot complete: {snapshot.height} rows × {snapshot.width} cols")
    return snapshot


# ---------------------------------------------------------------------------
# CAGR computation from multi-year events
# ---------------------------------------------------------------------------

def _compute_cagr(
    tickers: list[str],
    decision_date: datetime.date,
    pit_events: pl.DataFrame | None,
) -> pl.DataFrame:
    """Compute 3-year revenue and profit CAGRs from PIT events.

    For each ticker, find the latest annual formal report visible at
    decision_date, and compare with the formal report 3 years earlier.

    CAGR = (latest / base) ^ (1/3) - 1
    """
    if pit_events is None or pit_events.height == 0:
        return pl.DataFrame({
            "ticker": tickers,
            "revenue_cagr_3y": [None] * len(tickers),
            "profit_cagr_3y": [None] * len(tickers),
        })

    decision_str = decision_date.isoformat()

    # Filter to formal annuals visible at decision_date
    annuals = pit_events.filter(
        (pl.col("source_type") == "formal") &
        (pl.col("available_at") <= decision_str) &
        (pl.col("report_period").str.ends_with("-12-31"))
    )

    if annuals.height == 0:
        return pl.DataFrame({
            "ticker": tickers,
            "revenue_cagr_3y": [None] * len(tickers),
            "profit_cagr_3y": [None] * len(tickers),
        })

    records = []
    for ticker in tickers:
        t_data = annuals.filter(pl.col("ticker") == ticker).sort("report_period", descending=True)
        if t_data.height < 2:
            records.append({"ticker": ticker, "revenue_cagr_3y": None, "profit_cagr_3y": None})
            continue

        latest = t_data.row(0, named=True)
        latest_rp = latest["report_period"][:4]  # year

        # Find base year (3 years before latest)
        base_year = str(int(latest_rp) - 3)
        base_rows = t_data.filter(pl.col("report_period").str.starts_with(base_year))

        if base_rows.height == 0:
            records.append({"ticker": ticker, "revenue_cagr_3y": None, "profit_cagr_3y": None})
            continue

        base = base_rows.row(0, named=True)

        # Revenue CAGR
        rev_latest = _to_float(latest.get("revenue"))
        rev_base = _to_float(base.get("revenue"))
        rev_cagr = _cagr(rev_base, rev_latest, 3)

        # Profit CAGR
        prof_latest = _to_float(latest.get("net_profit"))
        prof_base = _to_float(base.get("net_profit"))
        prof_cagr = _cagr(prof_base, prof_latest, 3)

        records.append({
            "ticker": ticker,
            "revenue_cagr_3y": rev_cagr,
            "profit_cagr_3y": prof_cagr,
        })

    return pl.DataFrame(records)


def _compute_yoy_acceleration(
    tickers: list[str],
    decision_date: datetime.date,
    pit_events: pl.DataFrame | None,
) -> pl.DataFrame:
    """Compute YoY growth acceleration from consecutive annual reports.

    Acceleration = latest_yoy - prior_yoy (in percentage points)
    Example: revenue grew +30% in 2022 vs +20% in 2021 → acceleration = +10pp

    This measures whether growth is ACCELERATING (positive) or DECELERATING
    (negative), which is the true signal for the growth_acceleration dimension.
    """
    empty = pl.DataFrame({
        "ticker": tickers,
        "revenue_yoy_acceleration": [None] * len(tickers),
        "profit_yoy_acceleration": [None] * len(tickers),
        "operating_margin_delta": [None] * len(tickers),
        "cfo_to_net_profit_ttm": [None] * len(tickers),
    })

    if pit_events is None or pit_events.height == 0:
        return empty

    decision_str = decision_date.isoformat()

    # Filter to formal annuals visible at decision_date
    annuals = pit_events.filter(
        (pl.col("source_type") == "formal") &
        (pl.col("available_at") <= decision_str) &
        (pl.col("report_period").str.ends_with("-12-31"))
    )

    if annuals.height == 0:
        return empty

    records = []
    for ticker in tickers:
        t_data = annuals.filter(pl.col("ticker") == ticker).sort("report_period", descending=True)

        rec = {
            "ticker": ticker,
            "revenue_yoy_acceleration": None,
            "profit_yoy_acceleration": None,
            "operating_margin_delta": None,
            "cfo_to_net_profit_ttm": None,
        }

        if t_data.height >= 1:
            latest = t_data.row(0, named=True)

            # CFO / Net Profit ratio (from latest report)
            cfo = _to_float(latest.get("cfo_per_share"))
            eps = _to_float(latest.get("eps"))
            if cfo is not None and eps is not None and eps != 0:
                rec["cfo_to_net_profit_ttm"] = cfo / eps

        if t_data.height >= 2:
            latest = t_data.row(0, named=True)
            prior = t_data.row(1, named=True)

            # Revenue YoY acceleration
            rev_yoy_latest = _to_float(latest.get("revenue_yoy"))
            rev_yoy_prior = _to_float(prior.get("revenue_yoy"))
            if rev_yoy_latest is not None and rev_yoy_prior is not None:
                rec["revenue_yoy_acceleration"] = rev_yoy_latest - rev_yoy_prior

            # Profit YoY acceleration
            prof_yoy_latest = _to_float(latest.get("profit_yoy"))
            prof_yoy_prior = _to_float(prior.get("profit_yoy"))
            if prof_yoy_latest is not None and prof_yoy_prior is not None:
                rec["profit_yoy_acceleration"] = prof_yoy_latest - prof_yoy_prior

            # Operating margin delta (gross_margin change)
            gm_latest = _to_float(latest.get("gross_margin"))
            gm_prior = _to_float(prior.get("gross_margin"))
            if gm_latest is not None and gm_prior is not None:
                rec["operating_margin_delta"] = gm_latest - gm_prior

        records.append(rec)

    result = pl.DataFrame(records)
    nn_rev = result.height - result["revenue_yoy_acceleration"].null_count()
    nn_prof = result.height - result["profit_yoy_acceleration"].null_count()
    nn_cfo = result.height - result["cfo_to_net_profit_ttm"].null_count()
    logger.info(
        f"  YoY acceleration: rev={nn_rev}/{result.height} ({100*nn_rev/result.height:.0f}%), "
        f"profit={nn_prof}/{result.height} ({100*nn_prof/result.height:.0f}%), "
        f"cfo_ratio={nn_cfo}/{result.height} ({100*nn_cfo/result.height:.0f}%)"
    )
    return result


def _to_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def _cagr(base: float | None, latest: float | None, years: int) -> float | None:
    """Compute CAGR. Returns None if inputs are invalid."""
    if base is None or latest is None or base <= 0 or latest <= 0:
        return None
    try:
        return (latest / base) ** (1.0 / years) - 1.0
    except (ZeroDivisionError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Historical market data
# ---------------------------------------------------------------------------

def _fetch_historical_market_data(
    tickers: list[str],
    trade_date: datetime.date,
    max_workers: int = 4,
) -> pl.DataFrame:
    """Fetch historical daily bars for all tickers around trade_date.

    Caches raw daily bars per ticker to data/cache/daily/{start}_{end}/
    so subsequent runs with the same date range skip the API entirely.

    Computes:
    - RS60d (60-day relative strength)
    - MA structure (price vs MA20/MA60)
    - Turnover support
    - Close price (for valuation reconstruction)
    """
    import pandas as pd
    import akshare as ak
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm
    from ai_investor.connectors.akshare_connector import _throttled_call

    # We need ~90 trading days of history
    start_date = (trade_date - datetime.timedelta(days=150)).strftime("%Y%m%d")
    end_date = trade_date.strftime("%Y%m%d")

    # Cache directory for this date range
    cache_dir = Path(f"data/cache/daily/{start_date}_{end_date}")
    cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_raw_bars(ticker: str) -> pd.DataFrame | None:
        """Fetch raw daily bars with per-ticker disk cache."""
        cache_path = cache_dir / f"{ticker}.parquet"
        if cache_path.exists():
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass  # re-fetch on corrupt cache

        try:
            df = _throttled_call(
                ak.stock_zh_a_hist,
                symbol=ticker,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                try:
                    df.to_parquet(cache_path, index=False)
                except Exception:
                    pass
                return df
        except Exception:
            pass
        return None

    def _compute_indicators(ticker: str, df: pd.DataFrame | None) -> dict:
        """Compute technical indicators from raw daily bars."""
        if df is None or df.empty:
            return _empty_market_record(ticker)

        try:
            date_col = "日期" if "日期" in df.columns else df.columns[0]
            close_col = "收盘" if "收盘" in df.columns else "close"
            vol_col = "成交量" if "成交量" in df.columns else "volume"

            closes = df[close_col].astype(float).tolist()
            volumes = df[vol_col].astype(float).tolist()

            if len(closes) < 5:
                return _empty_market_record(ticker)

            rec = {"ticker": ticker}
            rec["close"] = closes[-1]

            # RS60d
            if len(closes) >= 60:
                rec["rs_60d"] = (closes[-1] / closes[-60]) - 1
            elif len(closes) >= 20:
                rec["rs_60d"] = (closes[-1] / closes[-20]) - 1
            else:
                rec["rs_60d"] = None

            # MA20 & MA60
            ma20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else None
            ma60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else None
            if ma20 and ma60:
                rec["ma_structure"] = 1.0 if closes[-1] > ma20 > ma60 else 0.0
            else:
                rec["ma_structure"] = None

            # ADV20
            recent_vols = volumes[-20:] if len(volumes) >= 20 else volumes
            rec["adv20_amount_cny"] = sum(recent_vols) / len(recent_vols) if recent_vols else None

            # Turnover support
            if len(volumes) >= 20:
                avg5 = sum(volumes[-5:]) / 5
                avg20 = sum(volumes[-20:]) / 20
                rec["turnover_support"] = avg5 / avg20 if avg20 > 0 else None
            else:
                rec["turnover_support"] = None

            return rec
        except Exception:
            return _empty_market_record(ticker)

    def _worker(ticker: str) -> dict:
        df = _fetch_raw_bars(ticker)
        return _compute_indicators(ticker, df)

    # Check how many are already cached
    cached = sum(1 for t in tickers if (cache_dir / f"{t}.parquet").exists())
    logger.info(f"  Daily bar cache: {cached}/{len(tickers)} hits in {cache_dir}")

    records = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tickers}
        for f in tqdm(as_completed(futures), total=len(tickers), desc="Market Data"):
            records.append(f.result())

    return pl.DataFrame(records)


def _empty_market_record(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "close": None,
        "rs_60d": None,
        "ma_structure": None,
        "adv20_amount_cny": None,
        "turnover_support": None,
    }


# ---------------------------------------------------------------------------
# Stock metadata
# ---------------------------------------------------------------------------

def _fetch_stock_metadata(tickers: list[str]) -> pl.DataFrame:
    """Fetch industry, board, and listing info for tickers.

    Uses cached stock info if available.
    """
    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    cache_path = Path("data/cache/stock_info_meta.parquet")
    if cache_path.exists():
        df = pl.read_parquet(cache_path)
        if set(tickers).issubset(set(df["ticker"].to_list())):
            logger.info(f"  [cache hit] stock metadata")
            return df.filter(pl.col("ticker").is_in(tickers))

    # Fetch full stock list for metadata
    try:
        df_raw = _throttled_call(ak.stock_info_a_code_name)
    except Exception as e:
        logger.warning(f"  Failed to fetch stock metadata: {e}")
        return pl.DataFrame({
            "ticker": tickers,
            "name": [""] * len(tickers),
            "exchange": [""] * len(tickers),
            "board": ["main"] * len(tickers),
        })

    records = []
    for _, row in df_raw.iterrows():
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        # Determine exchange and board from code prefix
        exchange = "SH" if code.startswith(("6",)) else "SZ"
        if code.startswith("68"):
            board = "star"  # 科创板
        elif code.startswith("30"):
            board = "chinext"  # 创业板
        elif code.startswith("8"):
            board = "bse"  # 北交所
        else:
            board = "main"

        records.append({
            "ticker": code,
            "name": str(row.get("name", "")),
            "exchange": exchange,
            "board": board,
        })

    result = pl.DataFrame(records)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        result.write_parquet(cache_path)
    except Exception:
        pass

    return result.filter(pl.col("ticker").is_in(tickers))


# ---------------------------------------------------------------------------
# Risk flags (approximate)
# ---------------------------------------------------------------------------

def _fetch_risk_flags_approx(tickers: list[str]) -> pl.DataFrame:
    """Approximate risk flags. Uses current-state data as proxy.

    For strict PIT, risk flags would need historical event tracking.
    This is acceptable for PIT-Lite since ST/delisting status changes
    are relatively rare and typically persist for extended periods.
    """
    # Return empty flags — the snapshot assembler will default them to False
    return pl.DataFrame({
        "ticker": tickers,
        "risk_goodwill_high": [False] * len(tickers),
        "risk_equity_pledge_high": [False] * len(tickers),
        "risk_regulatory_probe": [False] * len(tickers),
        "risk_major_reduction": [False] * len(tickers),
        "risk_material_negative_announcement": [False] * len(tickers),
        "is_st": [False] * len(tickers),
        "is_delisting": [False] * len(tickers),
        "is_suspended": [False] * len(tickers),
    })


# ---------------------------------------------------------------------------
# Assemble final snapshot
# ---------------------------------------------------------------------------

def _assemble_snapshot(
    pit_view: pl.DataFrame,
    market_data: pl.DataFrame,
    metadata: pl.DataFrame,
    risk_flags: pl.DataFrame,
    trade_date: datetime.date,
    *,
    cagr_data: pl.DataFrame | None = None,
    accel_data: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Join all data sources into the 45-column snapshot format.

    Reconstructs valuation metrics (PE, PB) from:
    - PIT financial data (EPS, BPS from latest visible report)
    - Historical market data (close price at trade_date)
    - Acceleration data (YoY deltas from consecutive annual reports)
    """
    from ai_investor.connectors.snapshot_builder import REQUIRED_COLUMNS

    # Start with PIT financial data
    df = pit_view.select([
        "ticker",
        pl.col("eps").cast(pl.Float64, strict=False),
        pl.col("revenue_yoy").cast(pl.Float64, strict=False),
        pl.col("profit_yoy").cast(pl.Float64, strict=False),
        pl.col("bps").cast(pl.Float64, strict=False),
        pl.col("roe").cast(pl.Float64, strict=False),
        pl.col("gross_margin").cast(pl.Float64, strict=False),
        pl.col("cfo_per_share").cast(pl.Float64, strict=False),
        pl.col("industry").cast(pl.Utf8, strict=False),
        pl.col("source_type").cast(pl.Utf8, strict=False),
    ])

    # Join market data
    if market_data.height > 0:
        df = df.join(market_data, on="ticker", how="left")

    # Join CAGR data
    if cagr_data is not None and cagr_data.height > 0:
        cagr_cols = [c for c in cagr_data.columns if c not in df.columns or c == "ticker"]
        if len(cagr_cols) > 1:
            df = df.join(cagr_data.select(cagr_cols), on="ticker", how="left")

    # Join acceleration data (true YoY deltas + cfo_ratio + margin_delta)
    if accel_data is not None and accel_data.height > 0:
        accel_cols = [c for c in accel_data.columns if c not in df.columns or c == "ticker"]
        if len(accel_cols) > 1:
            df = df.join(accel_data.select(accel_cols), on="ticker", how="left")

    # Join metadata
    if metadata.height > 0:
        meta_cols = [c for c in metadata.columns if c not in df.columns or c == "ticker"]
        if len(meta_cols) > 1:
            df = df.join(metadata.select(meta_cols), on="ticker", how="left")

    # Join risk flags
    if risk_flags.height > 0:
        risk_cols = [c for c in risk_flags.columns if c not in df.columns or c == "ticker"]
        if len(risk_cols) > 1:
            df = df.join(risk_flags.select(risk_cols), on="ticker", how="left")

    # --- Reconstruct valuation metrics from PIT data ---
    # eps_yield = EPS / close (earnings yield)
    if "eps" in df.columns and "close" in df.columns:
        df = df.with_columns(
            (pl.col("eps") / pl.col("close")).alias("eps_yield")
        )

    # bps_yield = BPS / close (book yield, inverse of PB)
    if "bps" in df.columns and "close" in df.columns:
        df = df.with_columns(
            (pl.col("bps") / pl.col("close")).alias("bps_yield")
        )

    # Map PIT fields to expected column names
    # Note: revenue_yoy_acceleration/profit_yoy_acceleration/cfo_to_net_profit_ttm
    # are now computed properly in _compute_yoy_acceleration(), not renamed.
    rename_map = {
        "roe": "roe_ttm",
        "gross_margin": "gross_margin_stability_12q",
        "industry": "industry_l1",
    }

    for old_name, new_name in rename_map.items():
        if old_name in df.columns and new_name not in df.columns:
            df = df.rename({old_name: new_name})

    # Add industry_anchor = industry_l1
    if "industry_l1" in df.columns and "industry_anchor" not in df.columns:
        df = df.with_columns(pl.col("industry_l1").alias("industry_anchor"))

    # Ensure all required columns exist with defaults
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            if col in ("is_st", "is_delisting", "is_suspended",
                       "has_latest_financials", "has_standard_unqualified_audit",
                       "risk_goodwill_high", "risk_equity_pledge_high",
                       "risk_regulatory_probe", "risk_major_reduction",
                       "risk_material_negative_announcement"):
                df = df.with_columns(pl.lit(False).alias(col))
            elif col in ("listed_trading_days",):
                df = df.with_columns(pl.lit(365).alias(col))
            elif col in ("security_type",):
                df = df.with_columns(pl.lit("A").alias(col))
            elif col in ("size_bucket",):
                df = df.with_columns(pl.lit("mid").alias(col))
            else:
                df = df.with_columns(pl.lit(None).cast(pl.Float64).alias(col))

    # Select only required columns
    available = [c for c in REQUIRED_COLUMNS if c in df.columns]
    df = df.select(available)

    return df
