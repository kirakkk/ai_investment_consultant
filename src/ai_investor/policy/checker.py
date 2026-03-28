"""Policy Checker — determine result_state based on data quality and policy rules.

Implements the state machine:
    - ok: all data complete, no policy violations
    - degraded: non-critical data missing or stale
    - blocked: critical data missing, policy violation, or universe excluded
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai_investor.features.engine import FeatureResult
from ai_investor.models.strategy import StrategyConfig
from ai_investor.universe.builder import MembershipDecision


@dataclass
class PolicyDecision:
    """Result of the policy check for a single ticker."""

    result_state: str  # "ok" | "degraded" | "blocked"
    state_reason_codes: list[str] = field(default_factory=list)
    critical_complete: bool = True
    missing_features: list[str] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)
    degraded_reasons: list[str] = field(default_factory=list)
    confidence: float = 1.0


def check_policy(
    membership: MembershipDecision,
    feature_result: FeatureResult | None,
    config: StrategyConfig,
) -> PolicyDecision:
    """Determine result_state and data_quality for a single ticker.

    Args:
        membership: Universe membership decision.
        feature_result: Feature computation result (None if excluded).
        config: Strategy configuration.

    Returns:
        PolicyDecision with result_state and supporting metadata.
    """
    state_reason_codes: list[str] = []
    degraded_reasons: list[str] = []
    missing_features: list[str] = []
    critical_complete = True
    confidence = 1.0

    # --- Blocked conditions ---

    # Not in universe
    if not membership.included:
        return PolicyDecision(
            result_state="blocked",
            state_reason_codes=["BLK_UNIVERSE_EXCLUDED"],
            critical_complete=False,
            confidence=0.0,
        )

    # No feature result (shouldn't happen for included tickers, but defensive)
    if feature_result is None:
        return PolicyDecision(
            result_state="blocked",
            state_reason_codes=["BLK_MISSING_CRITICAL_INPUTS"],
            critical_complete=False,
            confidence=0.0,
        )

    # Critical features missing
    if feature_result.missing_critical:
        state_reason_codes.append("BLK_MISSING_CRITICAL_INPUTS")
        critical_complete = False
        missing_features = feature_result.missing_features
        return PolicyDecision(
            result_state="blocked",
            state_reason_codes=state_reason_codes,
            critical_complete=False,
            missing_features=missing_features,
            confidence=0.0,
        )

    # --- Degraded conditions ---

    if feature_result.missing_features:
        missing_features = feature_result.missing_features
        degraded_reasons.append("DGD_MISSING_NONCRITICAL_FEATURES")
        state_reason_codes.append("DGD_MISSING_NONCRITICAL_FEATURES")
        # Reduce confidence proportionally to missing features
        total_metrics = len(feature_result.normalized_features) + len(feature_result.missing_features)
        if total_metrics > 0:
            confidence = len(feature_result.normalized_features) / total_metrics

    # --- Determine final state ---

    if state_reason_codes:
        result_state = "degraded"
    else:
        result_state = "ok"
        confidence = _compute_data_confidence(feature_result)

    return PolicyDecision(
        result_state=result_state,
        state_reason_codes=state_reason_codes,
        critical_complete=critical_complete,
        missing_features=missing_features,
        stale_sources=[],  # TODO: implement freshness checks in MVP1
        degraded_reasons=degraded_reasons,
        confidence=round(confidence, 4),
    )


def _compute_data_confidence(feature_result: FeatureResult) -> float:
    """Compute a data confidence score based on feature completeness.

    For MVP0 this is a simple ratio. Future versions will incorporate
    data freshness and source quality signals.
    """
    total = len(feature_result.normalized_features) + len(feature_result.missing_features)
    if total == 0:
        return 0.0
    return round(len(feature_result.normalized_features) / total, 4)
