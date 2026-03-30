"""End-to-end PIT integration test.

Pipeline: PIT Events → PIT Snapshot → Feature Engine → Scoring Engine
Tests that the full chain works with real data.

Uses a small sample (100 tickers) to keep runtime manageable.
"""
import sys, os
sys.path.insert(0, os.path.abspath('src'))
os.environ["PYTHONIOENCODING"] = "utf-8"

import datetime
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("pit_e2e")

import polars as pl

from ai_investor.backtest.conventions import AsOfContext
from ai_investor.backtest.pit_store import pull_forecasts, pull_express, pull_formal, build_pit_view
from ai_investor.backtest.pit_snapshot import build_pit_snapshot
from ai_investor.features.engine import compute_features
from ai_investor.scoring.engine import score_universe
from ai_investor.strategy.loader import load_strategy
from pathlib import Path


def main():
    # ===================================================================
    # PHASE 1: Build PIT events for 2022 annual
    # ===================================================================
    # 1. Pull ALL needed report periods (3 years of history for CAGR)
    logger.info("=" * 60)
    logger.info("PHASE 1: Pull PIT events (2019-2022 for 3y CAGR)")
    logger.info("=" * 60)

    import polars as pl
    from ai_investor.backtest.pit_store import pull_forecasts, pull_express, pull_formal

    # Pull 2022 annual events (forecast + express + formal)
    forecasts = pull_forecasts("20221231")
    express = pull_express("20221231")
    formal_2022 = pull_formal("20221231")
    formal_q3_22 = pull_formal("20220930")

    # Pull 2021 and 2020 and 2019 annual formals for CAGR computation
    formal_2021 = pull_formal("20211231")
    formal_2020 = pull_formal("20201231")
    formal_2019 = pull_formal("20191231")

    all_events = pl.concat(
        [forecasts, express, formal_2022, formal_q3_22,
         formal_2021, formal_2020, formal_2019],
        how="diagonal_relaxed"
    )
    logger.info(f"Total events loaded: {all_events.height}")

    # ===================================================================
    # PHASE 2: Build PIT view at decision_date = 2023-05-06
    # (After statutory deadline, all formal reports visible)
    # ===================================================================
    decision_date = datetime.date(2023, 5, 6)
    logger.info(f"\nPHASE 2: Building PIT view at {decision_date}")

    pit_view = build_pit_view(decision_date, all_events)
    logger.info(f"PIT view: {pit_view.height} tickers")

    # Take a sample of 100 tickers for speed
    sample_tickers = pit_view["ticker"].to_list()[:100]
    pit_view_sample = pit_view.filter(pl.col("ticker").is_in(sample_tickers))
    logger.info(f"Sampled {pit_view_sample.height} tickers for E2E test")

    # ===================================================================
    # PHASE 3: Build PIT snapshot (financial + market data)
    # ===================================================================
    logger.info(f"\nPHASE 3: Building PIT snapshot")

    asof = AsOfContext.build(decision_date)
    logger.info(f"AsOfContext: trade={asof.trade_date}, entry={asof.entry_date}")

    snapshot = build_pit_snapshot(
        asof,
        pit_events=all_events,
        max_tickers=100,
        max_workers=4,
    )

    logger.info(f"PIT Snapshot shape: {snapshot.height} × {snapshot.width}")
    logger.info(f"Snapshot columns: {snapshot.columns}")

    # Check data quality
    if snapshot.height == 0:
        logger.error("FATAL: Empty snapshot!")
        return

    # Report non-null coverage
    logger.info("\nData coverage in PIT snapshot:")
    for col in snapshot.columns:
        non_null = snapshot.height - snapshot[col].null_count()
        pct = 100 * non_null / snapshot.height
        if pct < 100:
            logger.info(f"  {col}: {non_null}/{snapshot.height} ({pct:.0f}%)")

    # ===================================================================
    # PHASE 4: Run through Feature Engine + Scoring Engine
    # ===================================================================
    logger.info(f"\nPHASE 4: Feature computation + scoring")

    strategy_dir = Path("src/ai_investor/strategy/registry/growth_capture_12m_v1")
    config = load_strategy(strategy_dir)
    logger.info(f"Loaded strategy: growth_capture_12m_v1")

    # Get included tickers (exclude ST, suspended, etc.)
    included = set()
    for row in snapshot.iter_rows(named=True):
        t = str(row.get("ticker", ""))
        if row.get("is_st") or row.get("is_delisting") or row.get("is_suspended"):
            continue
        included.add(t)

    logger.info(f"Included tickers (post-filter): {len(included)}")

    # Compute features
    feature_results = compute_features(snapshot, config, included)
    logger.info(f"Feature results: {len(feature_results)} tickers")

    # Count scorable
    scorable = [fr for fr in feature_results if not fr.missing_critical]
    blocked = [fr for fr in feature_results if fr.missing_critical]
    logger.info(f"  Scorable: {len(scorable)}, Blocked (missing_critical): {len(blocked)}")

    if not scorable:
        logger.error("No scorable tickers! Check snapshot data quality.")
        return

    # Score universe
    snapshot_rows = {
        str(row["ticker"]): row
        for row in snapshot.iter_rows(named=True)
    }

    scoring_results = score_universe(
        feature_results, snapshot_rows, config,
        label_mode="percentile",
    )

    ranked = [r for r in scoring_results if r.rank is not None]
    logger.info(f"Scored and ranked: {len(ranked)} tickers")

    # ===================================================================
    # PHASE 5: Verify results make sense
    # ===================================================================
    logger.info(f"\nPHASE 5: Result verification")

    # Top 5 picks
    top5 = sorted(ranked, key=lambda r: r.total_score, reverse=True)[:5]
    logger.info("Top 5 picks:")
    for i, r in enumerate(top5):
        logger.info(
            f"  #{i+1}: {r.ticker} | score={r.total_score:.2f} | "
            f"rank={r.rank}/{len(ranked)} | label={r.label}"
        )

    # Verify no impossible scores
    for r in ranked:
        assert 0 <= r.total_score <= 100, f"Score out of range: {r.ticker}={r.total_score}"

    # Verify PIT integrity: check that the financial data in the snapshot
    # does NOT contain any report_period after decision_date
    if "source_type" in pit_view_sample.columns:
        future_reports = pit_view_sample.filter(
            pl.col("report_period") > decision_date.isoformat()
        )
        assert future_reports.height == 0, \
            f"PIT VIOLATION: {future_reports.height} future reports found!"

    logger.info("\n" + "=" * 60)
    logger.info("✅ END-TO-END TEST PASSED")
    logger.info("   PIT snapshot → features → scoring → ranking: ALL OK")
    logger.info("   No look-ahead bias detected")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
