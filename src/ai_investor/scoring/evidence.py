"""Evidence Assembler — construct drivers, risks, triggers, and invalidation.

Given the scoring result and raw snapshot data, assemble the structured
evidence blocks required by the score_result output contract.
"""

from __future__ import annotations

from ai_investor.features.engine import FeatureResult
from ai_investor.models.score_result import RiskItem, ThesisItem, TriggerItem
from ai_investor.models.strategy import StrategyConfig
from ai_investor.scoring.engine import ScoringResult


def assemble_drivers(
    feature_result: FeatureResult,
    scoring_result: ScoringResult,
    config: StrategyConfig,
    snapshot_row: dict[str, object],
    top_n: int = 3,
) -> list[ThesisItem]:
    """Generate top-N driver items from the highest-contributing features.

    Selects the features with highest normalized values and constructs
    structured ThesisItem entries.
    """
    if not feature_result.normalized_features:
        return []

    # Build metric_id → (direction, raw_value, normalized_value) map
    metric_info: dict[str, tuple[str | None, object, float]] = {}
    for group_name, group_def in config.feature_pipeline.feature_groups.items():
        for metric in group_def.metrics:
            if metric.id in feature_result.normalized_features:
                raw = snapshot_row.get(metric.id)
                norm = feature_result.normalized_features[metric.id]
                metric_info[metric.id] = (metric.direction, raw, norm)

    # Sort by normalized value descending
    sorted_metrics = sorted(metric_info.items(), key=lambda x: x[1][2], reverse=True)

    drivers: list[ThesisItem] = []
    for metric_id, (direction, raw_value, norm_value) in sorted_metrics[:top_n]:
        code = f"DRV_{metric_id.upper()}"
        drivers.append(ThesisItem(
            code=code,
            title=f"{metric_id} 表现突出",
            detail=f"{metric_id} 归一化值 {norm_value:.2f}，原始值 {raw_value}。",
            metric_id=metric_id,
            raw_value=raw_value if isinstance(raw_value, (int, float, str)) else None,
            normalized_value=norm_value,
            direction=direction,
        ))

    return drivers


def assemble_risks(
    scoring_result: ScoringResult,
    snapshot_row: dict[str, object],
) -> list[RiskItem]:
    """Generate risk disclosure items from triggered penalties."""
    risks: list[RiskItem] = []

    for penalty in scoring_result.penalties:
        severity = "high" if abs(penalty.score_impact) >= 8 else ("medium" if abs(penalty.score_impact) >= 5 else "low")
        risks.append(RiskItem(
            code=penalty.code,
            title=f"风险: {penalty.code}",
            detail=penalty.detail,
            severity=severity,
        ))

    return risks


def assemble_triggers() -> list[TriggerItem]:
    """Generate default forward-looking triggers.

    MVP0 uses a static set of common triggers.
    """
    return [
        TriggerItem(
            code="TRG_QUARTERLY_EARNINGS",
            title="季度业绩披露",
            condition="下一季度业绩披露后重新评估 growth_regime 分组",
            watch_metric="revenue_yoy_acceleration",
        ),
        TriggerItem(
            code="TRG_MAJOR_ANNOUNCEMENT",
            title="重大公告监控",
            condition="如出现重大重组、并购、增发等公告，触发重新评估",
            watch_metric=None,
        ),
    ]


def assemble_invalidation() -> list[TriggerItem]:
    """Generate default invalidation conditions.

    MVP0 uses a static set of conditions that would immediately
    invalidate the current research conclusion.
    """
    return [
        TriggerItem(
            code="INV_ST_DESIGNATION",
            title="被 ST 标记",
            condition="如被实施 ST 或 *ST，当前结论立即失效",
            watch_metric=None,
        ),
        TriggerItem(
            code="INV_DELISTING_NOTICE",
            title="退市风险警示",
            condition="如收到退市风险警示或终止上市决定，当前结论立即失效",
            watch_metric=None,
        ),
        TriggerItem(
            code="INV_MAJOR_FRAUD",
            title="重大财务造假",
            condition="如被证实存在重大财务造假，当前结论立即失效",
            watch_metric=None,
        ),
    ]
