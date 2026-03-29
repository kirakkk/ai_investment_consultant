"""Parameter optimization engine.

Uses Optuna multi-objective optimization to search for optimal
feature group weights. Each (strategy, horizon) combination runs
as an independent Study producing a Pareto front.

Architecture:
  PrecomputedSlice = precompute_slice(snapshot, config)
    ↓ [cached, immutable]
  score_with_weights(precomputed, weights) → scores
    ↓ [pure O(N) arithmetic, called 1000s of times]
  metrics(scores, fwd_returns) → (ICIR, TopK excess, Disaster rate)

The precompute/score split guarantees:
  1. Feature normalization is done ONCE (same z-scores for all trials)
  2. score_with_weights is algebraically equivalent to score_universe
  3. No "fast scorer" shortcuts that could diverge from production
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import optuna

from ai_investor.backtest.metrics import (
    compute_icir,
    disaster_rate,
    rank_ic,
    topk_excess_return,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Precomputed slice: frozen features + forward returns
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickerFeatures:
    """Precomputed features for a single ticker."""
    ticker: str
    industry: str
    # group_name → mean normalized value [0, 1]
    group_means: dict[str, float]
    # risk penalty (already computed, constant across weight trials)
    risk_penalty: float
    # forward returns at each horizon
    fwd_returns: dict[str, float | None]
    # whether this ticker has missing critical features
    missing_critical: bool


@dataclass
class PrecomputedSlice:
    """All data needed to evaluate one backtest slice across many weight trials.

    This object is built ONCE and reused for every Optuna trial.
    """
    trade_date: str
    tickers: list[TickerFeatures]
    group_names: list[str]  # ordered list of feature group names
    benchmark_returns: dict[str, float | None]  # horizon → benchmark return
    total_weight_budget: float = 100.0


def precompute_slice(
    feature_results: list,
    snapshot_rows: dict[str, dict],
    config,
    fwd_returns_map: dict[str, dict[str, float | None]],
    benchmark_returns: dict[str, float | None],
    trade_date: str,
) -> PrecomputedSlice:
    """Build a PrecomputedSlice from feature engine output.

    Args:
        feature_results: Output of compute_features()
        snapshot_rows: Raw snapshot data (for risk overlays)
        config: StrategyConfig
        fwd_returns_map: ticker → {fwd_1m: x, fwd_3m: y, ...}
        benchmark_returns: {benchmark_1m: x, ...}
        trade_date: ISO date string for audit trail
    """
    from ai_investor.scoring.engine import _apply_risk_overlays

    group_names = list(config.feature_pipeline.feature_groups.keys())
    max_penalty = config.risk_overlays.max_penalty_points

    ticker_features: list[TickerFeatures] = []

    for fr in feature_results:
        row = snapshot_rows.get(fr.ticker, {})

        # Compute group means (same logic as _compute_sub_scores, but without weight)
        group_means: dict[str, float] = {}
        for gname, gdef in config.feature_pipeline.feature_groups.items():
            metric_values = []
            for m in gdef.metrics:
                if m.id in fr.normalized_features:
                    metric_values.append(fr.normalized_features[m.id])
            group_means[gname] = (sum(metric_values) / len(metric_values)) if metric_values else 0.0

        # Risk penalty (constant, doesn't depend on weights)
        penalties = _apply_risk_overlays(fr.ticker, row, config)
        raw_penalty = sum(p.score_impact for p in penalties)
        capped_penalty = max(raw_penalty, -max_penalty)

        # Forward returns
        fwd = fwd_returns_map.get(fr.ticker, {})

        ticker_features.append(TickerFeatures(
            ticker=fr.ticker,
            industry=str(row.get("industry_anchor", row.get("industry_l1", ""))),
            group_means=group_means,
            risk_penalty=capped_penalty,
            fwd_returns=fwd,
            missing_critical=fr.missing_critical,
        ))

    return PrecomputedSlice(
        trade_date=trade_date,
        tickers=ticker_features,
        group_names=group_names,
        benchmark_returns=benchmark_returns,
    )


def score_with_weights(
    precomputed: PrecomputedSlice,
    weights: dict[str, float],
) -> tuple[list[str], list[float], list[str]]:
    """Score all tickers using given weights. Pure arithmetic.

    This is algebraically identical to score_universe() but:
    - Does NOT re-normalize features (they're precomputed)
    - Does NOT reload config (weights are passed directly)
    - Excludes missing_critical tickers from scoring

    Args:
        precomputed: PrecomputedSlice with frozen features
        weights: {group_name: weight_value} summing to ~100

    Returns:
        (tickers, scores, industries) — parallel lists, sorted by score desc.
        Only includes scorable tickers (not missing_critical).
    """
    scored: list[tuple[str, float, str]] = []

    for tf in precomputed.tickers:
        if tf.missing_critical:
            continue

        # Weighted sum: same as _compute_sub_scores but with trial weights
        total = 0.0
        for gname, mean_val in tf.group_means.items():
            w = weights.get(gname, 0.0)
            total += mean_val * w

        # Add risk penalty
        total += tf.risk_penalty

        # Clamp to [0, 100]
        total = max(0.0, min(100.0, total))

        scored.append((tf.ticker, total, tf.industry))

    # Sort descending
    scored.sort(key=lambda x: x[1], reverse=True)

    tickers = [s[0] for s in scored]
    scores = [s[1] for s in scored]
    industries = [s[2] for s in scored]

    return tickers, scores, industries


# ---------------------------------------------------------------------------
# Softmax weight parameterization
# ---------------------------------------------------------------------------

def logits_to_weights(
    logits: dict[str, float],
    total_budget: float = 100.0,
    min_pct: float = 5.0,
    max_pct: float = 60.0,
) -> dict[str, float]:
    """Convert raw logits to constrained weights via softmax.

    Constraint: sum(logits) = 0 (enforced by subtracting mean).
    Output weights are in [min_pct, max_pct] and sum to total_budget.

    If softmax output violates min/max, we clip and redistribute.
    """
    if not logits:
        return {}

    # Enforce sum=0 constraint (remove translational invariance)
    mean_logit = sum(logits.values()) / len(logits)
    centered = {k: v - mean_logit for k, v in logits.items()}

    # Softmax
    max_val = max(centered.values())
    exp_vals = {k: math.exp(v - max_val) for k, v in centered.items()}
    exp_sum = sum(exp_vals.values())
    raw_weights = {k: (v / exp_sum) * total_budget for k, v in exp_vals.items()}

    # Clip to [min_pct, max_pct] and redistribute
    weights = dict(raw_weights)
    for _ in range(10):  # iterative clipping
        clipped = False
        for k in weights:
            if weights[k] < min_pct:
                weights[k] = min_pct
                clipped = True
            elif weights[k] > max_pct:
                weights[k] = max_pct
                clipped = True

        if not clipped:
            break

        # Redistribute remainder
        fixed_keys = {k for k in weights if weights[k] == min_pct or weights[k] == max_pct}
        free_keys = set(weights.keys()) - fixed_keys
        if not free_keys:
            break

        fixed_sum = sum(weights[k] for k in fixed_keys)
        remaining = total_budget - fixed_sum
        free_sum = sum(raw_weights[k] for k in free_keys)

        if free_sum > 0:
            for k in free_keys:
                weights[k] = (raw_weights[k] / free_sum) * remaining

    return weights


# ---------------------------------------------------------------------------
# Multi-objective Optuna optimization
# ---------------------------------------------------------------------------

def run_optimization(
    slices: list[PrecomputedSlice],
    horizon: str,
    *,
    n_trials: int = 200,
    seed: int = 42,
    k_pct: float = 0.05,
    max_industry_pct: float = 0.25,
    disaster_threshold: float = -0.20,
) -> optuna.Study:
    """Run multi-objective optimization for a (strategy, horizon) pair.

    Objectives (all directions set via Optuna):
      1. MAXIMIZE mean Rank IC across slices
      2. MAXIMIZE mean Top-K excess return (after industry cap)
      3. MINIMIZE mean disaster rate

    Returns the Optuna study with Pareto-optimal trials.
    """
    if not slices:
        raise ValueError("No precomputed slices provided")

    group_names = slices[0].group_names
    fwd_col = f"fwd_{horizon}"
    bm_col = f"benchmark_{horizon}"

    def objective(trial: optuna.Trial) -> tuple[float, float, float]:
        # Sample logits (sum=0 enforced in logits_to_weights)
        logits = {}
        for gname in group_names:
            logits[gname] = trial.suggest_float(f"logit_{gname}", -3.0, 3.0)

        weights = logits_to_weights(logits)

        # Store weights for analysis
        for gname, w in weights.items():
            trial.set_user_attr(f"weight_{gname}", round(w, 2))

        # Evaluate across all slices
        ic_values: list[float] = []
        excess_values: list[float] = []
        disaster_values: list[float] = []

        for sl in slices:
            tickers, scores, industries = score_with_weights(sl, weights)

            # Get forward returns for this horizon
            fwd_map = {tf.ticker: tf.fwd_returns for tf in sl.tickers}
            returns = [fwd_map.get(t, {}).get(fwd_col) for t in tickers]

            # Metric 1: Rank IC
            ic = rank_ic(scores, returns)
            if ic is not None:
                ic_values.append(ic)

            # Metric 2: Top-K excess return
            bm_ret = sl.benchmark_returns.get(bm_col, 0.0) or 0.0
            excess = topk_excess_return(
                scores, returns, tickers, industries,
                k_pct=k_pct, max_industry_pct=max_industry_pct,
                benchmark_return=bm_ret,
            )
            if excess is not None:
                excess_values.append(excess)

            # Metric 3: Disaster rate
            dr = disaster_rate(scores, returns, k_pct=k_pct, threshold=disaster_threshold)
            if dr is not None:
                disaster_values.append(dr)

        # Aggregate across slices
        mean_ic = sum(ic_values) / len(ic_values) if ic_values else -1.0
        mean_excess = sum(excess_values) / len(excess_values) if excess_values else -1.0
        mean_disaster = sum(disaster_values) / len(disaster_values) if disaster_values else 1.0

        return mean_ic, mean_excess, mean_disaster

    # Create multi-objective study
    sampler = optuna.samplers.TPESampler(seed=seed, multivariate=True)
    study = optuna.create_study(
        directions=["maximize", "maximize", "minimize"],
        sampler=sampler,
        study_name=f"weight_opt_{horizon}",
    )

    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    # Log Pareto front
    pareto = study.best_trials
    logger.info(f"Optimization complete: {n_trials} trials, "
                f"{len(pareto)} Pareto-optimal solutions")

    for i, trial in enumerate(pareto):
        weights_str = ", ".join(
            f"{gname}={trial.user_attrs.get(f'weight_{gname}', '?')}"
            for gname in group_names
        )
        logger.info(
            f"  Pareto #{i+1}: IC={trial.values[0]:.4f}, "
            f"Excess={trial.values[1]:.4f}, "
            f"Disaster={trial.values[2]:.4f} | {weights_str}"
        )

    return study


def format_pareto_report(
    study: optuna.Study,
    group_names: list[str],
    horizon: str,
    strategy_name: str,
    n_slices: int,
    pit_mode: str = "non_strict_90d",
) -> str:
    """Generate a markdown report from optimization results.

    Includes mandatory governance fields per audit requirements.
    """
    pareto = study.best_trials

    lines = [
        f"# Optimization Report: {strategy_name} / {horizon}",
        "",
        "## Governance",
        "",
        f"- **PIT Mode**: `{pit_mode}`",
        f"- **Entry Price**: `t1_close`",
        f"- **Slices Used**: {n_slices}",
        f"- **Trials**: {len(study.trials)}",
        f"- **Pareto Solutions**: {len(pareto)}",
        "",
    ]

    # Warnings
    if n_slices < 5:
        lines.extend([
            "> [!WARNING]",
            f"> **Pipeline Smoke Test Only** (n_slices={n_slices} < 5).",
            "> These results are NOT statistically meaningful.",
            "> Do NOT use these weights in production.",
            "",
        ])

    if pit_mode != "strict_pit":
        lines.extend([
            "> [!CAUTION]",
            "> **Non-strict PIT data**. Financial data uses 90-day buffer,",
            "> not true disclosure-date PIT. Results may contain look-ahead bias.",
            "> Production weight updates require strict PIT back-validation.",
            "",
        ])

    # Pareto table
    lines.extend([
        "## Pareto Front",
        "",
        "| # | Rank IC | Excess Return | Disaster Rate | " + " | ".join(group_names) + " |",
        "|---|---------|---------------|---------------|" + "|".join(["---"] * len(group_names)) + "|",
    ])

    for i, trial in enumerate(pareto):
        row = [
            str(i + 1),
            f"{trial.values[0]:.4f}",
            f"{trial.values[1]:.4f}",
            f"{trial.values[2]:.4f}",
        ]
        for gname in group_names:
            w = trial.user_attrs.get(f"weight_{gname}", "?")
            row.append(f"{w}")
        lines.append("| " + " | ".join(row) + " |")

    lines.extend([
        "",
        "## Approval Status",
        "",
        "- [ ] Reviewed by strategy owner",
        "- [ ] Back-validated on strict PIT data (required before production update)",
        "- [ ] Risk committee sign-off",
        "",
    ])

    return "\n".join(lines)
