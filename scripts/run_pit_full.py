"""PIT-Lite FULL SCALE run: all ~11,000 tickers + Optuna.

Phases:
1. Load cached PIT events (instant - already in Parquet)
2. Build PIT snapshot for ALL tickers (daily bars now have disk cache)
3. Feature engine + scoring
4. Forward returns (also cached per-slice)
5. Optuna optimization
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
logger = logging.getLogger("pit_full")

import polars as pl

from ai_investor.backtest.conventions import AsOfContext
from ai_investor.backtest.pit_store import (
    pull_forecasts, pull_express, pull_formal, build_pit_view,
)
from ai_investor.backtest.pit_snapshot import build_pit_snapshot
from ai_investor.backtest.data_prep import fetch_forward_returns, fetch_benchmark_returns
from ai_investor.backtest.optimizer import (
    precompute_slice, run_optimization, format_pareto_report,
)
from ai_investor.features.engine import compute_features
from ai_investor.strategy.loader import load_strategy

OPTUNA_TRIALS = 100


def main():
    t_start = datetime.datetime.now()

    # ===================================================================
    # PHASE 1: Load cached PIT events
    # ===================================================================
    logger.info("=" * 60)
    logger.info("PHASE 1: Loading PIT events from cache")
    logger.info("=" * 60)

    event_frames = []
    for pull_fn in [pull_forecasts, pull_express, pull_formal]:
        df = pull_fn("20221231")
        if df.height > 0:
            event_frames.append(df)

    for year in ["20211231", "20201231", "20191231"]:
        df = pull_formal(year)
        if df.height > 0:
            event_frames.append(df)

    df_q3 = pull_formal("20220930")
    if df_q3.height > 0:
        event_frames.append(df_q3)

    all_events = pl.concat(event_frames, how="diagonal_relaxed")
    logger.info(f"Total events: {all_events.height}")

    # ===================================================================
    # PHASE 2: Build FULL PIT snapshot (no max_tickers limit)
    # ===================================================================
    decision_date = datetime.date(2023, 5, 6)
    logger.info(f"\nPHASE 2: Building FULL PIT snapshot")
    logger.info(f"  Decision date: {decision_date}")

    asof = AsOfContext.build(decision_date)
    logger.info(f"  AsOfContext: trade={asof.trade_date}, entry={asof.entry_date}")

    snapshot = build_pit_snapshot(
        asof,
        pit_events=all_events,
        max_tickers=None,  # FULL SCALE - no limit
        max_workers=10,    # higher concurrency
    )

    logger.info(f"  Snapshot: {snapshot.height} rows × {snapshot.width} cols")

    # Coverage report
    logger.info("  Key coverage:")
    for col in ["roe_ttm", "revenue_cagr_3y", "profit_cagr_3y",
                 "rs_60d", "eps_yield", "cfo_to_net_profit_ttm"]:
        if col in snapshot.columns:
            nn = snapshot.height - snapshot[col].null_count()
            logger.info(f"    {col}: {nn}/{snapshot.height} ({100*nn/snapshot.height:.0f}%)")

    # ===================================================================
    # PHASE 3: Feature Engine
    # ===================================================================
    logger.info(f"\nPHASE 3: Feature Engine")

    strategy_dir = Path("src/ai_investor/strategy/registry/growth_capture_12m_v1")
    config = load_strategy(strategy_dir)

    snapshot_rows = {
        str(row["ticker"]): row
        for row in snapshot.iter_rows(named=True)
    }

    included = set()
    for r in snapshot_rows.values():
        t = str(r.get("ticker", ""))
        if r.get("is_st") or r.get("is_delisting") or r.get("is_suspended"):
            continue
        included.add(t)

    logger.info(f"  Included: {len(included)}")
    feature_results = compute_features(snapshot, config, included)

    scorable = [fr for fr in feature_results if not fr.missing_critical]
    logger.info(f"  Scorable: {len(scorable)}, Blocked: {len(feature_results) - len(scorable)}")

    if len(scorable) < 50:
        logger.error("Too few scorable tickers!")
        return

    # ===================================================================
    # PHASE 4: Forward returns
    # ===================================================================
    logger.info(f"\nPHASE 4: Forward returns (entry={asof.entry_date})")

    scorable_tickers = [fr.ticker for fr in scorable]
    fwd_df = fetch_forward_returns(scorable_tickers, asof, max_workers=10)

    fwd_map = {}
    for row in fwd_df.iter_rows(named=True):
        t = str(row["ticker"])
        fwd_map[t] = {k: v for k, v in row.items() if k.startswith("fwd_")}

    for h in ["fwd_1m", "fwd_3m", "fwd_6m", "fwd_1y"]:
        if h in fwd_df.columns:
            nn = fwd_df.height - fwd_df[h].null_count()
            logger.info(f"  {h}: {nn}/{fwd_df.height} ({100*nn/fwd_df.height:.0f}%)")

    bm_returns = fetch_benchmark_returns(asof)
    logger.info(f"  Benchmark: {bm_returns}")

    # ===================================================================
    # PHASE 5: Optuna
    # ===================================================================
    horizon = "1y"
    logger.info(f"\nPHASE 5: Optuna ({OPTUNA_TRIALS} trials, horizon={horizon})")

    sl = precompute_slice(
        feature_results, snapshot_rows, config,
        fwd_map, bm_returns, asof.trade_date.isoformat(),
    )

    study = run_optimization(
        slices=[sl], horizon=horizon,
        n_trials=OPTUNA_TRIALS, seed=42,
    )

    # ===================================================================
    # PHASE 6: Report
    # ===================================================================
    report_md = format_pareto_report(
        study=study, group_names=sl.group_names,
        horizon=horizon, strategy_name="growth_capture_12m_v1",
        n_slices=1, pit_mode="pit_lite_statutory_deadline",
    )

    report_path = Path("optimization_report_pit_lite_full.md")
    report_path.write_text(report_md, encoding="utf-8")

    elapsed = (datetime.datetime.now() - t_start).total_seconds()
    pareto = study.best_trials

    logger.info(f"\n{'='*60}")
    logger.info(f"FULL SCALE COMPLETE ({elapsed/60:.1f} min)")
    logger.info(f"  Total tickers in snapshot: {snapshot.height}")
    logger.info(f"  Scorable: {len(scorable)}")
    logger.info(f"  Pareto solutions: {len(pareto)}")
    for i, t in enumerate(pareto[:5]):
        wts = ", ".join(
            f"{g}={t.user_attrs.get(f'weight_{g}', '?')}"
            for g in sl.group_names
        )
        logger.info(
            f"  #{i+1}: IC={t.values[0]:.4f} Excess={t.values[1]:.4f} "
            f"Disaster={t.values[2]:.4f} | {wts}"
        )
    logger.info(f"Report: {report_path.absolute()}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
