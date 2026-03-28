"""Scoring and Ranking Engine.

Takes normalized feature vectors, applies weighted scoring per feature group,
applies risk overlay penalties, and produces cross-sectional rankings.

V2 updates:
  - Percentile-based label assignment (label_mode=percentile)
  - Coverage-aware scoring (effective_weight_coverage)
  - Dynamic sub_scores (works with any feature group names from strategy.yaml)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_investor.features.engine import FeatureResult
from ai_investor.models.score_result import PenaltyItem, SubScores
from ai_investor.models.strategy import StrategyConfig


@dataclass
class ScoringResult:
    """Scoring result for a single ticker."""

    ticker: str
    sub_scores: dict[str, float] = field(default_factory=dict)
    penalties: list[PenaltyItem] = field(default_factory=list)
    risk_penalty_total: float = 0.0
    total_score: float = 0.0
    rank: int | None = None
    percentile_rank: float | None = None
    label: str = ""
    # V2: coverage metadata
    effective_weight_coverage: float = 1.0
    missing_weight_share: float = 0.0


def _compute_sub_scores(
    features: dict[str, float],
    config: StrategyConfig,
) -> tuple[dict[str, float], float, float]:
    """Compute per-feature-group sub-scores.

    Returns:
        (sub_scores_dict, effective_weight_coverage, missing_weight_share)
    """
    sub_scores: dict[str, float] = {}
    total_weight = config.get_total_weight()
    active_weight = 0

    for group_name, group_def in config.feature_pipeline.feature_groups.items():
        metric_values: list[float] = []
        for metric in group_def.metrics:
            if metric.id in features:
                metric_values.append(features[metric.id])

        if metric_values:
            mean_normalized = sum(metric_values) / len(metric_values)
            active_weight += group_def.weight
        else:
            mean_normalized = 0.0

        sub_scores[group_name] = round(mean_normalized * group_def.weight, 4)

    # Coverage calculation
    effective_weight_coverage = active_weight / total_weight if total_weight > 0 else 0.0
    missing_weight_share = 1.0 - effective_weight_coverage

    return sub_scores, effective_weight_coverage, missing_weight_share


def _apply_risk_overlays(
    ticker: str,
    snapshot_row: dict[str, object],
    config: StrategyConfig,
) -> list[PenaltyItem]:
    """Check each risk overlay rule and produce penalty items."""
    penalties: list[PenaltyItem] = []
    risk_map = config.get_risk_overlay_map()

    for code, penalty_points in risk_map.items():
        col_name = code.lower()
        if snapshot_row.get(col_name, False):
            penalties.append(PenaltyItem(
                code=code,
                score_impact=-abs(penalty_points),
                detail=f"Risk flag {code} triggered for {ticker}.",
            ))

    return penalties


def _assign_percentile_labels(
    results: list[ScoringResult],
    config: StrategyConfig,
) -> None:
    """Assign labels based on cross-sectional percentile rank.

    Uses label_percentiles from config (cumulative thresholds):
      重点研究: 0.05   → top 5%
      继续跟踪: 0.20   → next 15%
      暂不优先: 0.50   → next 30%
      回避: 1.00       → bottom 50%
    """
    pct_map = config.get_label_percentiles()
    if not pct_map:
        return

    # Sort labels by cumulative percentile (ascending)
    sorted_labels = sorted(pct_map.items(), key=lambda x: x[1])

    n = len(results)
    for r in results:
        if r.percentile_rank is None:
            r.label = config.label_mapping.blocked_label
            continue
        # percentile_rank is 0-based (rank 1 → 0.0, last → ~1.0)
        for label_name, cum_pct in sorted_labels:
            if r.percentile_rank < cum_pct:
                r.label = label_name
                break
        else:
            r.label = sorted_labels[-1][0]


def score_universe(
    feature_results: list[FeatureResult],
    snapshot_rows: dict[str, dict[str, object]],
    config: StrategyConfig,
    *,
    label_mode: str | None = None,
    label_percentiles: dict[str, float] | None = None,
) -> list[ScoringResult]:
    """Score all tickers in the universe and produce cross-sectional rankings.

    Args:
        feature_results: Normalized features from the Feature Engine.
        snapshot_rows: Raw snapshot data as dict per ticker (for risk overlays).
        config: Strategy configuration.
        label_mode: Override label mode ("percentile" or "absolute"). If None, uses config.
        label_percentiles: Override percentile thresholds. If None, uses config.

    Returns:
        List of ScoringResult sorted by total_score descending.
    """
    max_penalty = config.risk_overlays.max_penalty_points
    results: list[ScoringResult] = []

    for fr in feature_results:
        sub_scores, eff_cov, miss_share = _compute_sub_scores(fr.normalized_features, config)

        row = snapshot_rows.get(fr.ticker, {})
        penalties = _apply_risk_overlays(fr.ticker, row, config)

        # Cap total penalty
        raw_penalty = sum(p.score_impact for p in penalties)
        capped_penalty = max(raw_penalty, -max_penalty)

        # Total score = sum of sub_scores + capped_penalty, clamped to [0, 100]
        raw_total = sum(sub_scores.values()) + capped_penalty
        total_score = round(max(0.0, min(100.0, raw_total)), 4)

        results.append(ScoringResult(
            ticker=fr.ticker,
            sub_scores=sub_scores,
            penalties=penalties,
            risk_penalty_total=capped_penalty,
            total_score=total_score,
            effective_weight_coverage=round(eff_cov, 4),
            missing_weight_share=round(miss_share, 4),
        ))

    # Sort by total_score descending for ranking
    results.sort(key=lambda r: r.total_score, reverse=True)

    # Assign rank and percentile rank
    n = len(results)
    for i, r in enumerate(results):
        r.rank = i + 1
        r.percentile_rank = round(i / n, 4) if n > 0 else None

    # Label assignment
    effective_label_mode = label_mode or config.get_label_mode()

    if effective_label_mode == "percentile":
        # Inject override percentiles if provided
        if label_percentiles:
            config.label_mapping.label_percentiles = label_percentiles
        _assign_percentile_labels(results, config)
    else:
        # Absolute score-band labels (V1 default)
        for r in results:
            r.label = config.get_label_for_score(r.total_score)

    return results


def build_sub_scores_model(sub_scores: dict[str, float], risk_penalty: float) -> SubScores:
    """Convert raw sub_scores dict to the Pydantic SubScores model.

    V2: Dynamically passes all group scores, works with any strategy.
    """
    kwargs = dict(sub_scores)
    kwargs["risk_penalty"] = risk_penalty
    return SubScores(**kwargs)
