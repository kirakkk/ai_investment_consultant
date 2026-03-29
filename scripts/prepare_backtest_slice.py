"""Prepare backtest data for a single slice.

Usage:
    python scripts/prepare_backtest_slice.py --date 2025-01-01

This will:
1. Resolve the as-of context (trade_date, financial_cutoff, entry_date)
2. Fetch forward returns for all tickers at 1m/3m/6m/1y horizons
3. Fetch benchmark (中证全指) forward returns
4. Build dataset manifest with SHA256 hashes and coverage gates
5. Save everything to data/backtest/{trade_date}/
"""

import argparse
import datetime
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_investor.backtest.conventions import AsOfContext
from ai_investor.backtest.data_prep import (
    BACKTEST_DATA_DIR,
    build_historical_ticker_list,
    fetch_benchmark_returns,
    fetch_delisted_stocks,
    fetch_forward_returns,
)
from ai_investor.backtest.manifest import build_manifest


def main():
    parser = argparse.ArgumentParser(description="Prepare backtest data slice")
    parser.add_argument(
        "--date", required=True,
        help="Base date (YYYY-MM-DD), e.g. 2025-01-01"
    )
    parser.add_argument(
        "--max-workers", type=int, default=4,
        help="Max concurrent API workers (default: 4)"
    )
    parser.add_argument(
        "--max-tickers", type=int, default=0,
        help="Limit tickers for testing (0 = all)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("prepare_slice")

    # Step 1: Build as-of context
    base_date = datetime.date.fromisoformat(args.date)
    asof = AsOfContext.build(base_date)
    logger.info(f"As-of context: {asof.to_dict()}")

    data_dir = BACKTEST_DATA_DIR / asof.trade_date.isoformat()
    data_dir.mkdir(parents=True, exist_ok=True)

    # Step 2: Fetch delisted stocks
    logger.info("Fetching delisted stock registry...")
    delisted = fetch_delisted_stocks()
    delisted.write_parquet(data_dir / "delisted_stocks.parquet")

    # Step 3: Build historical ticker list
    logger.info("Building historical ticker universe...")
    tickers = build_historical_ticker_list(asof, delisted)

    if args.max_tickers > 0:
        tickers = tickers[:args.max_tickers]
        logger.info(f"Limited to {len(tickers)} tickers for testing")

    # Step 4: Fetch forward returns
    logger.info("Fetching forward returns...")
    fwd_df = fetch_forward_returns(
        tickers, asof, max_workers=args.max_workers
    )

    # Step 5: Mark delisted stocks
    delisted_tickers = set(delisted["ticker"].to_list())
    # For tickers that delisted AFTER entry_date but BEFORE window end,
    # their forward return will naturally be None (data ends at delist).
    # We flag them for downstream treatment.
    fwd_df = fwd_df.with_columns(
        pl.col("ticker").is_in(list(delisted_tickers)).alias("is_delisted")
    )
    fwd_df.write_parquet(data_dir / "forward_returns.parquet", use_pyarrow=True)

    # Step 6: Fetch benchmark returns
    logger.info("Fetching benchmark (中证全指 000985) returns...")
    benchmark = fetch_benchmark_returns(asof)

    import json
    with open(data_dir / "benchmark_returns.json", "w") as f:
        json.dump(benchmark, f, indent=2)

    # Step 7: Build manifest
    logger.info("Building dataset manifest...")
    manifest = build_manifest(data_dir, asof)
    manifest.save(data_dir / "manifest.json")

    # Summary
    logger.info("=" * 60)
    logger.info(f"Backtest slice prepared: {data_dir}")
    logger.info(f"  trade_date: {asof.trade_date}")
    logger.info(f"  entry_date: {asof.entry_date}")
    logger.info(f"  tickers: {fwd_df.height}")
    logger.info(f"  files: {len(manifest.files)}")
    for label in ["1m", "3m", "6m", "1y"]:
        col = f"fwd_{label}"
        if col in fwd_df.columns:
            non_null = fwd_df.height - fwd_df[col].null_count()
            logger.info(f"  {col}: {non_null}/{fwd_df.height} "
                       f"({100*non_null/fwd_df.height:.0f}%)")
    logger.info("=" * 60)


if __name__ == "__main__":
    import polars as pl
    main()
