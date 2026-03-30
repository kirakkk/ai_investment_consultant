"""PIT Event Store — pull, cache, and query financial events with temporal integrity.

This module fetches financial disclosure events (forecasts, express reports,
formal financials) from AKShare, annotates each with an `available_at` date,
and provides a PIT query API that returns only data visible at a given
decision date.

Data flow:
  AKShare APIs ──► Raw Parquet cache (data/pit_raw/)
                       │
  decision_date ──► build_pit_view() ──► Unified PIT DataFrame
                       │
                  available_at filtering + priority merge
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import polars as pl

from ai_investor.backtest.conventions import (
    compute_available_at,
    enumerate_report_periods,
    resolve_trade_date,
    statutory_deadline,
)

logger = logging.getLogger(__name__)

# Cache directory for raw PIT event data
PIT_RAW_DIR = Path("data/pit_raw")


# ---------------------------------------------------------------------------
# Unified schema for financial events
# ---------------------------------------------------------------------------

# These are the columns we normalize across all 3 source types.
# Not all sources provide all columns; missing ones are null.
UNIFIED_COLUMNS = [
    "ticker",
    "report_period",     # e.g. 2022-12-31
    "source_type",       # "forecast" | "express" | "formal"
    "ann_date",          # raw announcement date from API
    "available_at",      # computed: first trading day data is usable
    # Financial metrics (subset available from each source)
    "eps",               # 每股收益
    "revenue",           # 营业总收入/营业收入
    "revenue_yoy",       # 营收同比增长 (%)
    "net_profit",        # 净利润
    "profit_yoy",        # 净利润同比增长 (%)
    "bps",               # 每股净资产
    "roe",               # 净资产收益率 (%)
    "gross_margin",      # 销售毛利率 (%)
    "cfo_per_share",     # 每股经营现金流量
    "industry",          # 所处行业
    # Forecast-specific
    "forecast_type",     # 预增/预减/略增/略减/扭亏/首亏/续亏/续盈
    "profit_change_pct", # 业绩变动幅度 (%)
]


def _ensure_pit_dir() -> Path:
    PIT_RAW_DIR.mkdir(parents=True, exist_ok=True)
    return PIT_RAW_DIR


# ---------------------------------------------------------------------------
# 1. Pull forecasts (业绩预告)
# ---------------------------------------------------------------------------

def pull_forecasts(report_period: str) -> pl.DataFrame:
    """Pull 业绩预告 for a given report period.

    Args:
        report_period: Format "YYYYMMDD", e.g. "20221231"

    Returns:
        Unified-schema DataFrame with source_type="forecast"
    """
    cache_path = _ensure_pit_dir() / f"forecast_{report_period}.parquet"
    if cache_path.exists():
        logger.info(f"  [cache hit] forecast {report_period}")
        return pl.read_parquet(cache_path)

    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    logger.info(f"  Pulling forecasts for {report_period}...")
    try:
        df = _throttled_call(ak.stock_yjyg_em, date=report_period)
    except Exception as e:
        logger.warning(f"  Failed to pull forecasts {report_period}: {e}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    if df is None or df.empty:
        logger.info(f"  No forecasts for {report_period}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    # Parse report_period date
    rp_date = datetime.date(
        int(report_period[:4]), int(report_period[4:6]), int(report_period[6:])
    )

    records = []
    for _, row in df.iterrows():
        ticker = str(row.get("股票代码", "")).strip()
        if not ticker:
            continue

        ann_date_raw = row.get("公告日期")
        if ann_date_raw is None:
            continue

        ann_date = _parse_date(ann_date_raw)
        if ann_date is None:
            continue

        try:
            avail = compute_available_at("forecast", rp_date, ann_date)
        except Exception:
            continue

        records.append({
            "ticker": ticker,
            "report_period": rp_date.isoformat(),
            "source_type": "forecast",
            "ann_date": ann_date.isoformat(),
            "available_at": avail.isoformat(),
            "eps": None,
            "revenue": None,
            "revenue_yoy": None,
            "net_profit": _safe_float(row.get("预测数值")),
            "profit_yoy": None,
            "profit_change_pct": _safe_float(row.get("业绩变动幅度")),
            "bps": None,
            "roe": None,
            "gross_margin": None,
            "cfo_per_share": None,
            "industry": None,
            "forecast_type": str(row.get("预告类型", "")),
        })

    result = pl.DataFrame(records)
    if result.height > 0:
        result.write_parquet(cache_path)
        logger.info(f"  Cached {result.height} forecasts for {report_period}")
    return result


# ---------------------------------------------------------------------------
# 2. Pull express reports (业绩快报)
# ---------------------------------------------------------------------------

def pull_express(report_period: str) -> pl.DataFrame:
    """Pull 业绩快报 for a given report period.

    Args:
        report_period: Format "YYYYMMDD", e.g. "20221231"
    """
    cache_path = _ensure_pit_dir() / f"express_{report_period}.parquet"
    if cache_path.exists():
        logger.info(f"  [cache hit] express {report_period}")
        return pl.read_parquet(cache_path)

    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    logger.info(f"  Pulling express reports for {report_period}...")
    try:
        df = _throttled_call(ak.stock_yjkb_em, date=report_period)
    except Exception as e:
        logger.warning(f"  Failed to pull express {report_period}: {e}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    if df is None or df.empty:
        logger.info(f"  No express reports for {report_period}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    rp_date = datetime.date(
        int(report_period[:4]), int(report_period[4:6]), int(report_period[6:])
    )

    # Determine column names (may vary slightly across AKShare versions)
    cols = df.columns.tolist()

    records = []
    for _, row in df.iterrows():
        ticker = str(row.get("股票代码", "")).strip()
        if not ticker:
            continue

        ann_date_raw = row.get("公告日期")
        if ann_date_raw is None:
            continue

        ann_date = _parse_date(ann_date_raw)
        if ann_date is None:
            continue

        try:
            avail = compute_available_at("express", rp_date, ann_date)
        except Exception:
            continue

        # Express reports have richer financial data than forecasts
        # Column names use Chinese with sub-categories
        revenue_col = next((c for c in cols if "营业收入" in c and "营业收入" == c.split("-")[-1]), None)
        revenue_yoy_col = next((c for c in cols if "营业收入" in c and "同比增长" in c), None)
        profit_col = next((c for c in cols if "净利润" in c and "净利润" == c.split("-")[-1]), None)
        profit_yoy_col = next((c for c in cols if "净利润" in c and "同比增长" in c), None)

        records.append({
            "ticker": ticker,
            "report_period": rp_date.isoformat(),
            "source_type": "express",
            "ann_date": ann_date.isoformat(),
            "available_at": avail.isoformat(),
            "eps": _safe_float(row.get("每股收益")),
            "revenue": _safe_float(row.get(revenue_col)) if revenue_col else None,
            "revenue_yoy": _safe_float(row.get(revenue_yoy_col)) if revenue_yoy_col else None,
            "net_profit": _safe_float(row.get(profit_col)) if profit_col else None,
            "profit_yoy": _safe_float(row.get(profit_yoy_col)) if profit_yoy_col else None,
            "profit_change_pct": None,
            "bps": _safe_float(row.get("每股净资产")),
            "roe": _safe_float(row.get("净资产收益率")),
            "gross_margin": None,
            "cfo_per_share": None,
            "industry": str(row.get("所处行业", "")),
            "forecast_type": None,
        })

    result = pl.DataFrame(records)
    if result.height > 0:
        result.write_parquet(cache_path)
        logger.info(f"  Cached {result.height} express reports for {report_period}")
    return result


# ---------------------------------------------------------------------------
# 3. Pull formal financials (正式财报)
# ---------------------------------------------------------------------------

def pull_formal(report_period: str) -> pl.DataFrame:
    """Pull 正式财报 for a given report period.

    Uses statutory deadline as available_at (conservative, no look-ahead).
    """
    cache_path = _ensure_pit_dir() / f"formal_{report_period}.parquet"
    if cache_path.exists():
        logger.info(f"  [cache hit] formal {report_period}")
        return pl.read_parquet(cache_path)

    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    logger.info(f"  Pulling formal financials for {report_period}...")
    try:
        df = _throttled_call(ak.stock_yjbb_em, date=report_period)
    except Exception as e:
        logger.warning(f"  Failed to pull formal {report_period}: {e}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    if df is None or df.empty:
        logger.info(f"  No formal financials for {report_period}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    rp_date = datetime.date(
        int(report_period[:4]), int(report_period[4:6]), int(report_period[6:])
    )

    # For formal reports, available_at = statutory_deadline + next trading day
    try:
        avail = compute_available_at("formal", rp_date)
    except Exception:
        logger.warning(f"  Cannot compute available_at for formal {report_period}")
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    avail_str = avail.isoformat()

    cols = df.columns.tolist()
    revenue_col = next((c for c in cols if "营业总收入" in c and "营业总收入" == c.split("-")[-1]), None)
    revenue_yoy_col = next((c for c in cols if "营业总收入" in c and "同比增长" in c), None)
    profit_col = next((c for c in cols if "净利润" in c and "净利润" == c.split("-")[-1]), None)
    profit_yoy_col = next((c for c in cols if "净利润" in c and "同比增长" in c), None)

    records = []
    for _, row in df.iterrows():
        ticker = str(row.get("股票代码", "")).strip()
        if not ticker:
            continue

        records.append({
            "ticker": ticker,
            "report_period": rp_date.isoformat(),
            "source_type": "formal",
            "ann_date": str(row.get("最新公告日期", "")),  # unreliable, for audit only
            "available_at": avail_str,
            "eps": _safe_float(row.get("每股收益")),
            "revenue": _safe_float(row.get(revenue_col)) if revenue_col else None,
            "revenue_yoy": _safe_float(row.get(revenue_yoy_col)) if revenue_yoy_col else None,
            "net_profit": _safe_float(row.get(profit_col)) if profit_col else None,
            "profit_yoy": _safe_float(row.get(profit_yoy_col)) if profit_yoy_col else None,
            "profit_change_pct": None,
            "bps": _safe_float(row.get("每股净资产")),
            "roe": _safe_float(row.get("净资产收益率")),
            "gross_margin": _safe_float(row.get("销售毛利率")),
            "cfo_per_share": _safe_float(row.get("每股经营现金流量")),
            "industry": str(row.get("所处行业", "")),
            "forecast_type": None,
        })

    result = pl.DataFrame(records)
    if result.height > 0:
        result.write_parquet(cache_path)
        logger.info(f"  Cached {result.height} formal records for {report_period}")
    return result


# ---------------------------------------------------------------------------
# 4. Build PIT view — the core query
# ---------------------------------------------------------------------------

# Priority: higher number = higher priority (more recent/detailed)
SOURCE_PRIORITY = {"forecast": 1, "express": 2, "formal": 3}


def pull_all_events(
    start_year: int = 2020,
    end_date: datetime.date | None = None,
) -> pl.DataFrame:
    """Pull all financial events for all report periods in range.

    Fetches forecasts, express reports, and formal financials for every
    standard quarter from start_year to end_date.

    Returns concatenated DataFrame in unified schema.
    """
    if end_date is None:
        end_date = datetime.date.today()

    periods = enumerate_report_periods(start_year, end_date)
    logger.info(f"Pulling events for {len(periods)} report periods "
                f"({periods[0]} → {periods[-1]})")

    all_frames: list[pl.DataFrame] = []

    for rp in periods:
        rp_str = rp.strftime("%Y%m%d")
        logger.info(f"--- Report period: {rp_str} ---")

        for pull_fn in [pull_forecasts, pull_express, pull_formal]:
            try:
                df = pull_fn(rp_str)
                if df.height > 0:
                    all_frames.append(df)
            except Exception as e:
                logger.error(f"  Error pulling {pull_fn.__name__} for {rp_str}: {e}")

    if not all_frames:
        return pl.DataFrame(schema={c: pl.Utf8 for c in UNIFIED_COLUMNS})

    combined = pl.concat(all_frames, how="diagonal_relaxed")
    logger.info(f"Total events pulled: {combined.height}")
    return combined


def build_pit_view(
    decision_date: datetime.date,
    events: pl.DataFrame | None = None,
    *,
    start_year: int = 2020,
) -> pl.DataFrame:
    """Build a PIT financial snapshot as of decision_date.

    For each ticker, returns the single most informative financial record
    that was visible (available_at <= decision_date).

    Priority resolution (for the SAME report_period):
      formal > express > forecast
    If multiple report periods are visible, take the latest one.

    Args:
        decision_date: The "today" of the backtest simulation.
        events: Pre-loaded events DataFrame. If None, loads from cache.
        start_year: How far back to scan for events.

    Returns:
        DataFrame with one row per ticker, containing the latest visible
        financial data as of decision_date.
    """
    if events is None:
        events = pull_all_events(start_year, decision_date)

    if events.height == 0:
        return events

    decision_str = decision_date.isoformat()

    # Cast available_at to string for comparison (ISO format sorts correctly)
    filtered = events.filter(pl.col("available_at") <= decision_str)

    if filtered.height == 0:
        logger.warning(f"No events visible as of {decision_date}")
        return filtered

    # Add priority score
    filtered = filtered.with_columns(
        pl.col("source_type").replace_strict(
            SOURCE_PRIORITY, default=0
        ).alias("_priority")
    )

    # For each ticker: pick the row with (latest report_period, highest priority)
    # Sort by report_period DESC, priority DESC → take first per ticker
    result = (
        filtered
        .sort(["report_period", "_priority"], descending=[True, True])
        .group_by("ticker")
        .first()
        .drop("_priority")
    )

    logger.info(
        f"PIT view for {decision_date}: {result.height} tickers, "
        f"source distribution: {_source_distribution(result)}"
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(val) -> datetime.date | None:
    """Parse a date from various formats AKShare might return."""
    if val is None:
        return None
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    try:
        s = str(val).strip()
        if len(s) >= 10:
            return datetime.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        pass
    return None


def _safe_float(val) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def _source_distribution(df: pl.DataFrame) -> dict[str, int]:
    """Count records by source_type."""
    if "source_type" not in df.columns:
        return {}
    counts = df.group_by("source_type").len()
    return {
        str(row["source_type"]): int(row["len"])
        for row in counts.iter_rows(named=True)
    }
