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
    industry: str = ""  # V2: for industry concentration cap
    # V2: coverage metadata
    effective_weight_coverage: float = 1.0
    missing_weight_share: float = 0.0
    # V3-P0: carry forward missing_critical flag for rank exclusion
    _missing_critical: bool = False


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
    max_industry_pct: float = 0.0,
) -> None:
    """Assign labels based on cross-sectional percentile rank.

    Uses label_percentiles from config (cumulative thresholds):
      重点研究: 0.05   → top 5%
      继续跟踪: 0.20   → next 15%
      暂不优先: 0.50   → next 30%
      回避: 1.00       → bottom 50%

    V3-P0 fixes:
      - Industry cap now uses backfill-to-capacity: after demoting excess
        stocks, promote highest-scored from next tier (respecting cap)
        until tier reaches target capacity or no eligible candidates remain.
      - Target capacity is computed from the eligible (ranked) population,
        not the post-demotion remnant.
    """
    pct_map = config.get_label_percentiles()
    if not pct_map:
        return

    # Sort labels by cumulative percentile (ascending)
    sorted_labels = sorted(pct_map.items(), key=lambda x: x[1])

    n = len(results)
    # Phase 1: assign raw labels by percentile
    for r in results:
        if r.percentile_rank is None:
            r.label = config.label_mapping.blocked_label
            continue
        for label_name, cum_pct in sorted_labels:
            if r.percentile_rank < cum_pct:
                r.label = label_name
                break
        else:
            r.label = sorted_labels[-1][0]

    # Phase 2: enforce industry concentration cap with backfill-to-capacity
    if max_industry_pct > 0:
        import logging
        from collections import Counter
        logger = logging.getLogger(__name__)

        # Compute target capacity for each tier based on eligible population
        eligible_count = sum(1 for r in results if r.percentile_rank is not None)
        target_capacities: dict[str, int] = {}
        prev_cum = 0.0
        for label_name, cum_pct in sorted_labels:
            tier_pct = cum_pct - prev_cum
            target_capacities[label_name] = max(1, round(eligible_count * tier_pct))
            prev_cum = cum_pct

        # Process each tier from top to bottom (except last, which absorbs)
        for tier_idx in range(len(sorted_labels) - 1):
            tier_label = sorted_labels[tier_idx][0]
            next_label = sorted_labels[tier_idx + 1][0]
            target_cap = target_capacities[tier_label]
            max_per_industry = max(1, int(target_cap * max_industry_pct))

            # Step A: Demote excess from over-represented industries
            tier_results = [r for r in results if r.label == tier_label]
            industry_count: dict[str, int] = {}
            demoted = []
            for r in tier_results:
                ind = r.industry or "unknown"
                industry_count[ind] = industry_count.get(ind, 0) + 1
                if industry_count[ind] > max_per_industry:
                    r.label = next_label
                    demoted.append((r.ticker, ind))

            # Step B: Backfill from next tier to reach target_cap
            current_count = sum(1 for r in results if r.label == tier_label)
            if current_count < target_cap:
                # Candidates: stocks currently in next_label, sorted by score desc
                candidates = sorted(
                    [r for r in results if r.label == next_label],
                    key=lambda r: r.total_score,
                    reverse=True,
                )
                # Rebuild industry count for current tier
                industry_count_now: dict[str, int] = Counter(
                    r.industry or "unknown"
                    for r in results if r.label == tier_label
                )
                promoted = []
                for c in candidates:
                    if current_count >= target_cap:
                        break
                    ind = c.industry or "unknown"
                    if industry_count_now.get(ind, 0) < max_per_industry:
                        c.label = tier_label
                        industry_count_now[ind] = industry_count_now.get(ind, 0) + 1
                        current_count += 1
                        promoted.append((c.ticker, ind))

                if promoted:
                    logger.info(
                        f"Industry cap backfill: promoted {len(promoted)} "
                        f"into '{tier_label}' (target={target_cap})"
                    )

            if demoted:
                final_count = sum(1 for r in results if r.label == tier_label)
                logger.info(
                    f"Industry cap: demoted {len(demoted)} from '{tier_label}', "
                    f"final={final_count}/{target_cap} "
                    f"(max {max_per_industry}/industry)"
                )


def score_universe(
    feature_results: list[FeatureResult],
    snapshot_rows: dict[str, dict[str, object]],
    config: StrategyConfig,
    *,
    label_mode: str | None = None,
    label_percentiles: dict[str, float] | None = None,
) -> list[ScoringResult]:
    """Score all tickers in the universe and produce cross-sectional rankings.

    V3-P0 fix: missing_critical tickers are excluded from percentile_rank
    computation and labeled as blocked. They still appear in results but
    do not pollute the ranking denominator.
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
            industry=str(row.get("industry_anchor", row.get("industry_l1", ""))),
            effective_weight_coverage=round(eff_cov, 4),
            missing_weight_share=round(miss_share, 4),
            _missing_critical=fr.missing_critical,  # V3-P0: carry forward
        ))

    # Sort by total_score descending for ranking
    results.sort(key=lambda r: r.total_score, reverse=True)

    # V3-P0: Separate scorable vs blocked (missing_critical)
    scorable = [r for r in results if not r._missing_critical]
    blocked = [r for r in results if r._missing_critical]

    # Assign rank and percentile rank only to scorable tickers
    n_scorable = len(scorable)
    for i, r in enumerate(scorable):
        r.rank = i + 1
        r.percentile_rank = round(i / n_scorable, 4) if n_scorable > 0 else None

    # Blocked tickers get no rank
    for r in blocked:
        r.rank = None
        r.percentile_rank = None

    # Label assignment
    effective_label_mode = label_mode or config.get_label_mode()

    if effective_label_mode == "percentile":
        # Inject override percentiles if provided
        if label_percentiles:
            config.label_mapping.label_percentiles = label_percentiles
        # V2: read industry concentration cap from config extra fields
        max_ind_pct = getattr(config, "max_single_industry_pct", 0.0) or 0.0
        _assign_percentile_labels(results, config, max_industry_pct=float(max_ind_pct))
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
