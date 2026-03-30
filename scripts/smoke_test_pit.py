"""PIT-Lite Smoke Test: pull real data for a small sample, verify the pipeline.

Tests:
1. Pull 2022 annual report events (forecast + express + formal)
2. Build PIT view at different decision dates
3. Verify no look-ahead bias
"""
import sys, os
sys.path.insert(0, os.path.abspath('src'))
os.environ["PYTHONIOENCODING"] = "utf-8"

import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("pit_smoke")

from ai_investor.backtest.pit_store import (
    pull_forecasts, pull_express, pull_formal, build_pit_view
)

def main():
    # 1. Pull 2022 annual report events
    logger.info("=" * 60)
    logger.info("STEP 1: Pulling 2022 annual report events")
    logger.info("=" * 60)

    forecasts = pull_forecasts("20221231")
    express = pull_express("20221231")
    formal = pull_formal("20221231")

    logger.info(f"Forecasts: {forecasts.height} records")
    logger.info(f"Express:   {express.height} records")
    logger.info(f"Formal:    {formal.height} records")

    # Concatenate
    import polars as pl
    all_events = pl.concat([forecasts, express, formal], how="diagonal_relaxed")
    logger.info(f"Total events: {all_events.height}")

    # 2. Build PIT view at different decision dates
    logger.info("\n" + "=" * 60)
    logger.info("STEP 2: Building PIT views at different dates")
    logger.info("=" * 60)

    test_dates = [
        datetime.date(2023, 1, 1),   # Before most forecasts
        datetime.date(2023, 2, 1),   # Mid forecast season
        datetime.date(2023, 4, 1),   # Express + some formals
        datetime.date(2023, 5, 5),   # After statutory deadline (all visible)
    ]

    for dd in test_dates:
        pit = build_pit_view(dd, all_events)
        if pit.height > 0:
            sources = pit.group_by("source_type").len()
            source_str = ", ".join(
                f"{r['source_type']}={r['len']}"
                for r in sources.iter_rows(named=True)
            )
            logger.info(f"  {dd}: {pit.height} tickers visible | {source_str}")

            # Verify no available_at > decision_date
            violations = pit.filter(pl.col("available_at") > dd.isoformat())
            if violations.height > 0:
                logger.error(f"  ⚠️ LOOK-AHEAD BIAS DETECTED: {violations.height} rows!")
            else:
                logger.info(f"  ✅ No look-ahead bias")
        else:
            logger.info(f"  {dd}: 0 tickers visible")

    # 3. Cross-check: at Jan 1, no formal 2022 annual should be visible
    logger.info("\n" + "=" * 60)
    logger.info("STEP 3: Anti-leak verification")
    logger.info("=" * 60)

    pit_jan = build_pit_view(datetime.date(2023, 1, 1), all_events)
    if pit_jan.height > 0:
        formal_leak = pit_jan.filter(
            (pl.col("source_type") == "formal") &
            (pl.col("report_period") == "2022-12-31")
        )
        if formal_leak.height > 0:
            logger.error(f"  ❌ CRITICAL: {formal_leak.height} formal 2022 annual reports visible at Jan 1!")
        else:
            logger.info(f"  ✅ No formal 2022 annual reports at Jan 1 (correct)")

    logger.info("\nSmoke test complete!")

if __name__ == "__main__":
    main()
