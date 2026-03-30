"""Backtest data preparation.

Fetches and caches all data needed for a single backtest slice:
- Historical snapshot (financials, market data, industry, valuation)
- Forward returns for all tickers at multiple horizons
- Benchmark (中证全指 000985) forward returns
- Delisted stock registry

All functions respect the AsOfContext conventions and cache results
to parquet files in data/backtest/{trade_date}/.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

import polars as pl

from ai_investor.backtest.conventions import (
    HORIZON_WINDOWS,
    AsOfContext,
    get_trade_date_offset,
)

logger = logging.getLogger(__name__)

# Default backtest data directory
BACKTEST_DATA_DIR = Path("data/backtest")


def _cache_path(asof: AsOfContext, name: str) -> Path:
    """Return cache file path for a given trade_date and data name."""
    d = BACKTEST_DATA_DIR / asof.trade_date.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.parquet"


# ---------------------------------------------------------------------------
# 1. Delisted stock registry
# ---------------------------------------------------------------------------

def fetch_delisted_stocks() -> pl.DataFrame:
    """Fetch combined SH+SZ delisted stock registry.

    Returns DataFrame with columns: [ticker, name, list_date, delist_date]
    """
    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    records = []

    # Shanghai
    try:
        df_sh = _throttled_call(ak.stock_info_sh_delist)
        for row in df_sh.itertuples():
            records.append({
                "ticker": str(getattr(row, "公司代码", "")).strip(),
                "name": str(getattr(row, "公司简称", "")).strip(),
                "delist_date": str(getattr(row, "暂停上市日期", "")),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch SH delisted stocks: {e}")

    # Shenzhen
    try:
        df_sz = _throttled_call(ak.stock_info_sz_delist, symbol="终止上市公司")
        for row in df_sz.itertuples():
            records.append({
                "ticker": str(getattr(row, "证券代码", "")).strip(),
                "name": str(getattr(row, "证券简称", "")).strip(),
                "delist_date": str(getattr(row, "终止上市日期", "")),
            })
    except Exception as e:
        logger.warning(f"Failed to fetch SZ delisted stocks: {e}")

    logger.info(f"Delisted stocks: {len(records)} total (SH+SZ)")
    return pl.DataFrame(records)


# ---------------------------------------------------------------------------
# 2. Forward returns
# ---------------------------------------------------------------------------

def fetch_forward_returns(
    tickers: list[str],
    asof: AsOfContext,
    windows: dict[str, int] | None = None,
    *,
    max_workers: int = 4,
) -> pl.DataFrame:
    """Fetch forward returns for all tickers from entry_date.

    Uses 后复权 (hfq) daily data from AKShare stock_zh_a_hist.
    Entry price = T+1 close (per as-of convention).
    Forward return = (close at T+1+window / close at T+1) - 1.

    Per-ticker raw bars cached to data/cache/daily_hfq/{start}_{end}/
    Slice-level result cached to data/backtest/{trade_date}/forward_returns.parquet.
    Slice cache invalidates if ticker count differs.

    Args:
        tickers: List of stock codes.
        asof: AsOfContext with resolved dates.
        windows: Horizon windows dict. Defaults to HORIZON_WINDOWS.
        max_workers: Concurrent workers for API calls.

    Returns:
        DataFrame with columns: [ticker, fwd_1m, fwd_3m, fwd_6m, fwd_1y]
    """
    cache = _cache_path(asof, "forward_returns")
    if cache.exists():
        cached_df = pl.read_parquet(cache)
        if cached_df.height >= len(tickers):
            logger.info(f"Forward returns cache hit: {cache} ({cached_df.height} tickers)")
            return cached_df
        else:
            logger.info(
                f"Forward returns cache stale: {cached_df.height} cached vs "
                f"{len(tickers)} requested. Re-fetching..."
            )

    if windows is None:
        windows = HORIZON_WINDOWS

    import pandas as pd
    import akshare as ak
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm
    from ai_investor.connectors.akshare_connector import _throttled_call

    # Date range for raw bars
    max_window = max(windows.values())
    start_str = (asof.entry_date - datetime.timedelta(days=5)).strftime("%Y%m%d")
    try:
        end_date = get_trade_date_offset(asof.entry_date, max_window + 5)
        end_str = end_date.strftime("%Y%m%d")
    except ValueError:
        end_str = (asof.entry_date + datetime.timedelta(days=max_window * 2)).strftime("%Y%m%d")

    entry_str = asof.entry_date.isoformat()

    # Per-ticker raw bar cache (hfq)
    bar_cache_dir = Path(f"data/cache/daily_hfq/{start_str}_{end_str}")
    bar_cache_dir.mkdir(parents=True, exist_ok=True)

    cached_count = sum(1 for t in tickers if (bar_cache_dir / f"{t}.parquet").exists())
    logger.info(f"  Forward bar cache: {cached_count}/{len(tickers)} hits in {bar_cache_dir}")

    def _fetch_raw(ticker: str) -> pd.DataFrame | None:
        """Fetch hfq raw bars with per-ticker disk cache."""
        cp = bar_cache_dir / f"{ticker}.parquet"
        if cp.exists():
            try:
                return pd.read_parquet(cp)
            except Exception:
                pass
        try:
            df = _throttled_call(
                ak.stock_zh_a_hist,
                symbol=ticker, period="daily",
                start_date=start_str, end_date=end_str,
                adjust="hfq",
            )
            if df is not None and not df.empty:
                try:
                    df.to_parquet(cp, index=False)
                except Exception:
                    pass
                return df
        except Exception:
            pass
        return None

    def _compute_fwd(ticker: str, df: pd.DataFrame | None) -> dict:
        rec: dict = {"ticker": ticker}
        if df is None or df.empty:
            for label in windows:
                rec[f"fwd_{label}"] = None
            rec["_status"] = "no_data"
            return rec

        try:
            date_col = "日期" if "日期" in df.columns else df.columns[0]
            close_col = "收盘" if "收盘" in df.columns else "close"
            df[date_col] = df[date_col].astype(str)

            entry_rows = df[df[date_col] == entry_str]
            if entry_rows.empty:
                for delta in [1, -1, 2, -2]:
                    alt = (asof.entry_date + datetime.timedelta(days=delta)).isoformat()
                    entry_rows = df[df[date_col] == alt]
                    if not entry_rows.empty:
                        break

            if entry_rows.empty:
                for label in windows:
                    rec[f"fwd_{label}"] = None
                rec["_status"] = "no_entry_price"
                return rec

            entry_price = float(entry_rows.iloc[0][close_col])
            if entry_price <= 0:
                for label in windows:
                    rec[f"fwd_{label}"] = None
                rec["_status"] = "zero_entry_price"
                return rec

            entry_idx = entry_rows.index[0]
            df_from_entry = df.loc[entry_idx:]

            for label, n_days in windows.items():
                if len(df_from_entry) > n_days:
                    exit_price = float(df_from_entry.iloc[n_days][close_col])
                    rec[f"fwd_{label}"] = round((exit_price / entry_price) - 1, 6)
                else:
                    rec[f"fwd_{label}"] = None

            rec["_entry_price"] = entry_price
            rec["_status"] = "ok"

        except Exception as e:
            logger.warning(f"  {ticker}: forward return compute failed: {e}")
            for label in windows:
                rec[f"fwd_{label}"] = None
            rec["_status"] = f"error:{type(e).__name__}"

        return rec

    def _worker(ticker: str) -> dict:
        df = _fetch_raw(ticker)
        return _compute_fwd(ticker, df)

    logger.info(
        f"Fetching forward returns for {len(tickers)} tickers "
        f"(entry={asof.entry_date}, windows={list(windows.keys())})"
    )
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tickers}
        for future in tqdm(as_completed(futures), total=len(tickers),
                          desc="Forward Returns"):
            records.append(future.result())

    df_result = pl.DataFrame(records)

    # Log coverage
    for label in windows:
        col = f"fwd_{label}"
        non_null = df_result.height - df_result[col].null_count()
        logger.info(
            f"  {col}: {non_null}/{df_result.height} "
            f"({100 * non_null / df_result.height:.0f}%)"
        )

    # Cache slice-level
    df_result.write_parquet(cache)
    logger.info(f"Forward returns cached to {cache}")

    return df_result


# ---------------------------------------------------------------------------
# 3. Benchmark returns
# ---------------------------------------------------------------------------

def fetch_benchmark_returns(
    asof: AsOfContext,
    benchmark: str = "000985",  # 中证全指
    windows: dict[str, int] | None = None,
) -> dict[str, float | None]:
    """Fetch benchmark index forward returns.

    Returns dict like {'fwd_1m': 0.05, 'fwd_3m': 0.12, ...}.
    Uses 东方财富 index daily data.
    """
    if windows is None:
        windows = HORIZON_WINDOWS

    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    max_window = max(windows.values())
    start_str = (asof.entry_date - datetime.timedelta(days=5)).strftime("%Y%m%d")
    end_str = (asof.entry_date + datetime.timedelta(days=max_window * 2)).strftime("%Y%m%d")

    try:
        df = _throttled_call(
            ak.index_zh_a_hist,
            symbol=benchmark,
            period="daily",
            start_date=start_str,
            end_date=end_str,
        )
    except Exception as e:
        logger.error(f"Failed to fetch benchmark {benchmark}: {e}")
        return {f"fwd_{label}": None for label in windows}

    if df is None or df.empty:
        return {f"fwd_{label}": None for label in windows}

    date_col = "日期" if "日期" in df.columns else df.columns[0]
    close_col = "收盘" if "收盘" in df.columns else "close"
    df[date_col] = df[date_col].astype(str)

    entry_str = asof.entry_date.isoformat()
    entry_rows = df[df[date_col] == entry_str]
    if entry_rows.empty:
        for delta in [1, -1, 2]:
            alt = (asof.entry_date + datetime.timedelta(days=delta)).isoformat()
            entry_rows = df[df[date_col] == alt]
            if not entry_rows.empty:
                break

    if entry_rows.empty:
        logger.warning(f"Benchmark {benchmark}: no entry price at {entry_str}")
        return {f"fwd_{label}": None for label in windows}

    entry_price = float(entry_rows.iloc[0][close_col])
    entry_idx = entry_rows.index[0]
    df_from_entry = df.loc[entry_idx:]

    result = {}
    for label, n_days in windows.items():
        if len(df_from_entry) > n_days:
            exit_price = float(df_from_entry.iloc[n_days][close_col])
            result[f"benchmark_{label}"] = round((exit_price / entry_price) - 1, 6)
        else:
            result[f"benchmark_{label}"] = None

    logger.info(f"Benchmark {benchmark} returns: {result}")
    return result


# ---------------------------------------------------------------------------
# 4. Historical stock universe (which tickers existed at trade_date)
# ---------------------------------------------------------------------------

def build_historical_ticker_list(
    asof: AsOfContext,
    delisted: pl.DataFrame | None = None,
) -> list[str]:
    """Build the list of tickers that were actively traded near trade_date.

    Strategy:
    1. Start with current full stock list
    2. Add back delisted stocks that were still listed at trade_date
    3. Remove stocks whose IPO date is after trade_date (if detectable)

    This is an approximation. True PIT universe requires a security master.
    """
    import akshare as ak
    from ai_investor.connectors.akshare_connector import _throttled_call

    # Current stock list
    df_current = _throttled_call(ak.stock_zh_a_spot_em)
    tickers = set(str(r) for r in df_current["代码"].to_list())
    logger.info(f"Current stock list: {len(tickers)}")

    # Add back delisted stocks that were listed at trade_date
    if delisted is not None and delisted.height > 0:
        trade_str = asof.trade_date.isoformat()
        for row in delisted.iter_rows(named=True):
            delist_str = str(row.get("delist_date", ""))
            if delist_str and delist_str > trade_str:
                tickers.add(row["ticker"])

    logger.info(f"Historical universe (approx): {len(tickers)} tickers")
    return sorted(tickers)
