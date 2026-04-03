"""Standalone data cache builder for remote machines.

This script ONLY downloads and caches raw daily bar data (qfq + hfq)
for all A-share tickers. It has minimal dependencies (akshare, polars, tqdm)
and can run on any machine with a clean IP.

After completion, copy the data/cache/ directory back to your dev machine.

Usage:
    pip install akshare polars tqdm
    python scripts/remote_cache_builder.py

The script is fully resumable — it skips already-cached tickers.
"""
import datetime
import time
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s"
)
logger = logging.getLogger("cache_builder")

try:
    from tqdm import tqdm
except ImportError:
    # Fallback if tqdm not installed
    def tqdm(iterable, **kwargs):
        total = kwargs.get("total", "?")
        for i, item in enumerate(iterable):
            if i % 100 == 0:
                print(f"  Progress: {i}/{total}")
            yield item

import akshare as ak

# ---------------------------------------------------------------------------
# Configuration: which date ranges to cache
# ---------------------------------------------------------------------------
# Each tuple: (start_date, end_date, adjust_type, cache_subdir)
# adjust_type: "qfq" (前复权) for technical indicators, "hfq" (后复权) for returns

CACHE_TASKS = [
    # QFQ daily bars for PIT snapshot market data
    ("20211207", "20220506", "qfq", "daily"),       # Slice 1
    ("20220610", "20221107", "qfq", "daily"),       # Slice 2
    ("20221206", "20230505", "qfq", "daily"),       # Slice 3
    ("20230609", "20231106", "qfq", "daily"),       # Slice 4

    # HFQ daily bars for forward returns
    ("20220504", "20230524", "hfq", "daily_hfq"),   # Slice 1 fwd
    ("20221103", "20231124", "hfq", "daily_hfq"),   # Slice 2 fwd
    ("20230503", "20240527", "hfq", "daily_hfq"),   # Slice 3 fwd
    ("20231102", "20241126", "hfq", "daily_hfq"),   # Slice 4 fwd
]

CACHE_ROOT = Path("data/cache")
MAX_WORKERS = 4          # Concurrent API calls
THROTTLE_SECONDS = 0.15  # Delay between requests
MAX_RETRIES = 6


# ---------------------------------------------------------------------------
# Ticker list
# ---------------------------------------------------------------------------

def get_all_tickers() -> list[str]:
    """Fetch full A-share ticker list."""
    logger.info("Fetching A-share ticker list...")
    df = ak.stock_zh_a_spot_em()
    tickers = sorted(df["代码"].astype(str).tolist())
    logger.info(f"  Total tickers: {len(tickers)}")
    return tickers


# ---------------------------------------------------------------------------
# Single ticker fetch with retry
# ---------------------------------------------------------------------------

def _fetch_single(
    ticker: str,
    start: str,
    end: str,
    adjust: str,
    cache_dir: Path,
) -> str:
    """Fetch and cache daily bars for one ticker. Returns status string."""
    cache_file = cache_dir / f"{ticker}.parquet"
    if cache_file.exists():
        return "cached"

    import polars as pl

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(THROTTLE_SECONDS)
            df = ak.stock_zh_a_hist(
                symbol=ticker,
                period="daily",
                start_date=start,
                end_date=end,
                adjust=adjust,
            )
            if df is not None and not df.empty:
                pl_df = pl.from_pandas(df)
                pl_df.write_parquet(cache_file)
                return "fetched"
            else:
                # Empty result (delisted/suspended stock) — write empty marker
                pl.DataFrame({"_empty": [True]}).write_parquet(cache_file)
                return "empty"
        except Exception as e:
            wait = min(2 ** attempt + 5, 60)
            if attempt < MAX_RETRIES:
                time.sleep(wait)
            else:
                return f"failed: {e}"

    return "failed: max_retries"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_task(
    tickers: list[str],
    start: str,
    end: str,
    adjust: str,
    cache_subdir: str,
):
    """Run one cache task for all tickers."""
    dir_name = f"{start}_{end}"
    cache_dir = CACHE_ROOT / cache_subdir / dir_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    already_cached = len(list(cache_dir.glob("*.parquet")))
    need_fetch = len(tickers) - already_cached

    logger.info(f"\n{'='*60}")
    logger.info(f"Task: {cache_subdir}/{dir_name} ({adjust})")
    logger.info(f"  Tickers: {len(tickers)}, cached: {already_cached}, need: {need_fetch}")
    logger.info(f"{'='*60}")

    if need_fetch == 0:
        logger.info("  All cached! Skipping.")
        return

    stats = {"cached": 0, "fetched": 0, "empty": 0, "failed": 0}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(_fetch_single, t, start, end, adjust, cache_dir): t
            for t in tickers
        }
        for future in tqdm(as_completed(futures), total=len(tickers),
                          desc=f"{cache_subdir}/{dir_name}"):
            result = future.result()
            status = result.split(":")[0] if ":" in result else result
            stats[status] = stats.get(status, 0) + 1

    logger.info(f"  Results: {stats}")


def main():
    t_start = datetime.datetime.now()

    tickers = get_all_tickers()

    for start, end, adjust, subdir in CACHE_TASKS:
        run_task(tickers, start, end, adjust, subdir)

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    logger.info(f"\n{'='*60}")
    logger.info(f"ALL TASKS COMPLETE ({elapsed/60:.1f} min)")

    # Print final cache stats
    for start, end, adjust, subdir in CACHE_TASKS:
        dir_name = f"{start}_{end}"
        cache_dir = CACHE_ROOT / subdir / dir_name
        count = len(list(cache_dir.glob("*.parquet"))) if cache_dir.exists() else 0
        logger.info(f"  {subdir}/{dir_name}: {count} files")

    logger.info(f"{'='*60}")
    logger.info(f"\nNext step: copy data/cache/ back to your dev machine,")
    logger.info(f"then run: python scripts/run_pit_multi_slice.py")


if __name__ == "__main__":
    main()
