"""PIT-Lite Multi-Slice Backtest with corrected growth_acceleration.

Runs Optuna optimization across multiple decision dates (slices)
to produce statistically meaningful weight recommendations.

Slices use quarterly decision dates post-annual-report-disclosure:
  - 2022-05-06 (May 2022, post-2021 annual)
  - 2022-11-07 (Nov 2022, post-2022 Q3)
  - 2023-05-06 (May 2023, post-2022 annual)
  - 2023-11-06 (Nov 2023, post-2023 Q3)

Each slice needs:
  - PIT events from 2018+ (for 3-year CAGR) + consecutive annuals (for acceleration)
  - Daily bars (qfq) for market indicators
  - Forward returns (hfq) for Optuna evaluation
"""
import sys, os
sys.path.insert(0, os.path.abspath('src'))
os.environ["PYTHONIOENCODING"] = "utf-8"

import datetime
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("pit_multi")

import polars as pl

from ai_investor.backtest.conventions import AsOfContext
from ai_investor.backtest.pit_store import (
    pull_forecasts, pull_express, pull_formal,
)
from ai_investor.backtest.pit_snapshot import build_pit_snapshot
from ai_investor.backtest.data_prep import fetch_forward_returns, fetch_benchmark_returns
from ai_investor.backtest.optimizer import (
    precompute_slice, run_optimization, format_pareto_report,
)
from ai_investor.features.engine import compute_features
from ai_investor.strategy.loader import load_strategy

OPTUNA_TRIALS = 120

# Decision dates: post-disclosure windows
# Note: slice 4 (2023-11-06) disabled — API rate-limit prevents full cache build.
# Will re-enable once data fetching completes overnight.
DECISION_DATES = [
    datetime.date(2022, 5, 6),    # Post-2021 annual
    datetime.date(2022, 11, 7),   # Post-2022 Q3
    datetime.date(2023, 5, 6),    # Post-2022 annual
    # datetime.date(2023, 11, 6),   # Post-2023 Q3 — data incomplete
]


def _load_pit_events(decision_date: datetime.date) -> pl.DataFrame:
    """Load PIT events appropriate for a given decision date.

    Need at least 4 years of annual formals for CAGR + acceleration.
    """
    year = decision_date.year
    frames = []

    # Annual formals: 4 years back (for CAGR base) + latest
    for y in range(year - 4, year + 1):
        rp = f"{y}1231"
        try:
            df = pull_formal(rp)
            if df.height > 0:
                frames.append(df)
        except Exception as e:
            logger.warning(f"  Could not load formal {rp}: {e}")

    # Also load Q3 report for the latest year (may be visible)
    for rp in [f"{year}0930", f"{year-1}0930"]:
        try:
            df = pull_formal(rp)
            if df.height > 0:
                frames.append(df)
        except Exception:
            pass

    # Forecasts and express for the latest annual
    for rp in [f"{year-1}1231", f"{year}1231"]:
        for fn in [pull_forecasts, pull_express]:
            try:
                df = fn(rp)
                if df.height > 0:
                    frames.append(df)
            except Exception:
                pass

    if not frames:
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")
    logger.info(f"  Events for {decision_date}: {combined.height} rows")
    return combined


def _build_slice(
    decision_date: datetime.date,
    config,
    strategy_dir: Path,
    max_tickers: int | None = None,
) -> dict:
    """Build one backtest slice: snapshot → features → forward returns."""
    logger.info(f"\n{'='*60}")
    logger.info(f"SLICE: decision_date={decision_date}")
    logger.info(f"{'='*60}")

    asof = AsOfContext.build(decision_date)
    logger.info(f"  AsOfContext: trade={asof.trade_date}, entry={asof.entry_date}")

    # Load events
    events = _load_pit_events(decision_date)
    if events.height == 0:
        logger.error(f"  No events for {decision_date}!")
        return None

    # Build PIT snapshot
    snapshot = build_pit_snapshot(
        asof, pit_events=events,
        max_tickers=max_tickers,
        max_workers=2,
    )
    logger.info(f"  Snapshot: {snapshot.height} rows × {snapshot.width} cols")

    # Coverage
    for col in ["roe_ttm", "revenue_cagr_3y", "revenue_yoy_acceleration",
                 "profit_yoy_acceleration", "cfo_to_net_profit_ttm", "rs_60d"]:
        if col in snapshot.columns:
            nn = snapshot.height - snapshot[col].null_count()
            logger.info(f"    {col}: {nn}/{snapshot.height} ({100*nn/snapshot.height:.0f}%)")

    # Feature engine
    snapshot_rows = {str(row["ticker"]): row for row in snapshot.iter_rows(named=True)}
    included = set()
    for r in snapshot_rows.values():
        t = str(r.get("ticker", ""))
        if r.get("is_st") or r.get("is_delisting") or r.get("is_suspended"):
            continue
        included.add(t)

    feature_results = compute_features(snapshot, config, included)
    scorable = [fr for fr in feature_results if not fr.missing_critical]
    logger.info(f"  Scorable: {len(scorable)}/{len(included)}")

    if len(scorable) < 50:
        logger.error(f"  Too few scorable tickers!")
        return None

    # Forward returns
    scorable_tickers = [fr.ticker for fr in scorable]
    fwd_df = fetch_forward_returns(scorable_tickers, asof, max_workers=2)
    fwd_map = {}
    for row in fwd_df.iter_rows(named=True):
        fwd_map[str(row["ticker"])] = {k: v for k, v in row.items() if k.startswith("fwd_")}

    for h in ["fwd_1m", "fwd_3m", "fwd_6m", "fwd_1y"]:
        if h in fwd_df.columns:
            nn = fwd_df.height - fwd_df[h].null_count()
            logger.info(f"    {h}: {nn}/{fwd_df.height} ({100*nn/fwd_df.height:.0f}%)")

    bm = fetch_benchmark_returns(asof)
    logger.info(f"  Benchmark: {bm}")

    # Precompute slice
    sl = precompute_slice(
        feature_results, snapshot_rows, config,
        fwd_map, bm, asof.trade_date.isoformat(),
    )

    return {
        "slice": sl,
        "asof": asof,
        "snapshot_height": snapshot.height,
        "scorable_count": len(scorable),
    }


def main():
    t_start = datetime.datetime.now()

    strategy_dir = Path("src/ai_investor/strategy/registry/growth_capture_12m_v1")
    config = load_strategy(strategy_dir)

    # ===================================================================
    # Build all slices
    # ===================================================================
    slices = []
    for dd in DECISION_DATES:
        result = _build_slice(dd, config, strategy_dir)
        if result:
            slices.append(result)

    if len(slices) < 2:
        logger.error("Need at least 2 valid slices for multi-slice optimization!")
        return

    logger.info(f"\n{'='*60}")
    logger.info(f"ALL SLICES BUILT: {len(slices)}/{len(DECISION_DATES)}")
    for s in slices:
        logger.info(f"  {s['asof'].trade_date}: {s['scorable_count']} scorable")
    logger.info(f"{'='*60}")

    # ===================================================================
    # Run Optuna across all slices
    # ===================================================================
    horizon = "1y"
    logger.info(f"\nOptuna: {OPTUNA_TRIALS} trials, {len(slices)} slices, horizon={horizon}")

    slice_objs = [s["slice"] for s in slices]
    study = run_optimization(
        slices=slice_objs, horizon=horizon,
        n_trials=OPTUNA_TRIALS, seed=42,
    )

    # ===================================================================
    # Report
    # ===================================================================
    report_md = format_pareto_report(
        study=study, group_names=slice_objs[0].group_names,
        horizon=horizon, strategy_name="growth_capture_12m_v1",
        n_slices=len(slices), pit_mode="pit_lite_statutory_deadline",
    )

    report_path = Path("optimization_report_multi_slice.md")
    report_path.write_text(report_md, encoding="utf-8")

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    pareto = study.best_trials

    logger.info(f"\n{'='*60}")
    logger.info(f"MULTI-SLICE OPTIMIZATION COMPLETE ({elapsed/60:.1f} min)")
    logger.info(f"  Slices: {len(slices)}")
    logger.info(f"  Trials: {OPTUNA_TRIALS}")
    logger.info(f"  Pareto solutions: {len(pareto)}")
    for i, t in enumerate(pareto[:5]):
        wts = ", ".join(
            f"{g}={t.user_attrs.get(f'weight_{g}', '?')}"
            for g in slice_objs[0].group_names
        )
        logger.info(
            f"  #{i+1}: IC={t.values[0]:.4f} Excess={t.values[1]:.4f} "
            f"Disaster={t.values[2]:.4f} | {wts}"
        )
    logger.info(f"Report: {report_path.absolute()}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
