"""Run parameter optimization.

Usage:
    python scripts/run_optimization.py --strategy growth_capture_12m_v1 --horizon 1y --slices data/backtest/2024-12-31
"""

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import polars as pl
from tqdm import tqdm

from ai_investor.backtest.optimizer import (
    PrecomputedSlice,
    format_pareto_report,
    precompute_slice,
    run_optimization,
)
from ai_investor.features.engine import compute_features
from ai_investor.strategy.loader import load_strategy

logger = logging.getLogger("run_opt")


def load_latest_snapshot() -> tuple[pl.DataFrame, dict[str, dict]]:
    """Load the most recently generated live snapshot."""
    snap_dir = Path("data/live_snapshots")
    if not snap_dir.exists():
        raise FileNotFoundError("No live snapshots found. Run scripts/generate_live_snapshot.py first.")

    files = list(snap_dir.glob("snapshot_*.parquet"))
    if not files:
        raise FileNotFoundError("No snapshot files found in data/live_snapshots/")

    latest = max(files, key=lambda f: f.stat().st_mtime)
    logger.info(f"Using snapshot: {latest.name}")

    df = pl.read_parquet(latest)
    
    # Convert to ticker -> row dict for fast lookup
    rows = {}
    for r in df.iter_rows(named=True):
        if r["ticker"]:
            rows[str(r["ticker"])] = r
            
    return df, rows


def load_backtest_slice(
    slice_dir: Path,
    horizon: str,
) -> tuple[dict[str, dict], dict[str, float], str]:
    """Load backtest data for a single slice."""
    logger.info(f"Loading slice from {slice_dir}")

    # Manifest
    manifest_path = slice_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    trade_date = manifest["asof_context"]["trade_date"]

    # Forward returns
    fwd_path = slice_dir / "forward_returns.parquet"
    if not fwd_path.exists():
        raise FileNotFoundError(f"Forward returns not found: {fwd_path}")

    df_fwd = pl.read_parquet(fwd_path)
    
    # Check if the requested horizon exists
    col_name = f"fwd_{horizon}"
    if col_name not in df_fwd.columns:
        raise ValueError(f"Horizon {horizon} not found in {fwd_path}. Available: {df_fwd.columns}")

    # Build ticker -> returns dict
    # Extract fwd_1m, fwd_3m, etc. whatever is available
    fwd_map: dict[str, dict[str, float | None]] = {}
    for r in df_fwd.iter_rows(named=True):
        t = str(r["ticker"])
        fwd_map[t] = {k: v for k, v in r.items() if k.startswith("fwd_")}

    # Benchmark returns
    bm_path = slice_dir / "benchmark_returns.json"
    bm = {}
    if bm_path.exists():
        with open(bm_path, "r", encoding="utf-8") as f:
            bm = json.load(f)
            
    return fwd_map, bm, trade_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Optuna multi-objective optimization")
    parser.add_argument("--strategy", required=True, help="Strategy ID, e.g. growth_capture_12m_v1")
    parser.add_argument("--horizon", required=True, help="Forward return horizon to target, e.g. 1y")
    parser.add_argument("--slices", nargs="+", required=True, help="Paths to backtest slice directories")
    parser.add_argument("--trials", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", default="optimization_report.md", help="Output MD report path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    # 1. Load Strategy
    registry_dir = Path("src/ai_investor/strategy/registry")
    strategy_dir = registry_dir / args.strategy
    if not strategy_dir.exists():
        raise ValueError(f"Strategy {args.strategy} not found in registry")

    config = load_strategy(strategy_dir)
    logger.info(f"Loaded strategy {args.strategy}")

    # 2. Get Features
    # Note: Using the live snapshot as a proxy for the historical financial state.
    # In a strict PIT setup, each slice would have its own snapshot.
    df_snapshot, snapshot_rows = load_latest_snapshot()
    
    # Exclude logic
    included_tickers = []
    for r in snapshot_rows.values():
        if r.get("is_st") or r.get("is_delisting") or r.get("is_suspended"):
            continue
        listed_days = r.get("listed_trading_days", 0)
        if listed_days and listed_days < 180:
            continue
        included_tickers.append(r["ticker"])

    logger.info(f"Computing base features for {len(included_tickers)} valid tickers...")
    feature_results = compute_features(df_snapshot, config, set(included_tickers))

    # 3. Precompute slices
    logger.info(f"Precomputing {len(args.slices)} slices...")
    precomputed_slices: list[PrecomputedSlice] = []
    for path_str in args.slices:
        slice_dir = Path(path_str)
        fwd_map, bm, trade_date = load_backtest_slice(slice_dir, args.horizon)
        
        sl = precompute_slice(
            feature_results,
            snapshot_rows,
            config,
            fwd_map,
            bm,
            trade_date,
        )
        precomputed_slices.append(sl)

    # 4. Optimize
    study = run_optimization(
        slices=precomputed_slices,
        horizon=args.horizon,
        n_trials=args.trials,
        seed=args.seed,
    )

    # 5. Report
    report_md = format_pareto_report(
        study=study,
        group_names=precomputed_slices[0].group_names,
        horizon=args.horizon,
        strategy_name=args.strategy,
        n_slices=len(args.slices),
        pit_mode="non_strict_90d",
    )

    out_path = Path(args.output)
    out_path.write_text(report_md, encoding="utf-8")
    logger.info(f"Report saved to {out_path.absolute()}")


if __name__ == "__main__":
    main()
