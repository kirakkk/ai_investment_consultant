"""Pydantic v2 models for strategy.yaml configuration.

These models type-check and validate the strategy DSL at load time,
ensuring the config is well-formed before the pipeline runs.

V2 updates:
  - All models use extra="allow" to support forward-compatible fields
  - Added sector_split, label_mode, percentile labels
  - Added effective_weight_coverage support
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Common config — allow unknown fields so V1 and V2 YAMLs both load
# ---------------------------------------------------------------------------
_MODEL_CFG = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Sub-sections
# ---------------------------------------------------------------------------

class Schedule(BaseModel):
    run_windows: list[str]
    asof_rule: str
    research_refresh: str

    model_config = _MODEL_CFG


class Market(BaseModel):
    exchanges: list[str]
    boards: list[str]
    security_types: list[str]
    currency: str

    model_config = _MODEL_CFG


class IncludeSeed(BaseModel):
    code: str
    enabled: bool

    model_config = _MODEL_CFG


class UniverseVersioning(BaseModel):
    policy: str
    key_fields: list[str]

    model_config = _MODEL_CFG


class Universe(BaseModel):
    include_seeds: list[IncludeSeed]
    hard_exclusions: list[str]
    eligibility_rules: dict[str, object]
    versioning: UniverseVersioning

    model_config = _MODEL_CFG


class DataContract(BaseModel):
    truth_sources: list[str]
    structured_sources: list[str]
    attention_sources: list[str]
    pit_required_fields: list[str]

    model_config = _MODEL_CFG


class MetricDef(BaseModel):
    id: str
    direction: str
    critical: bool

    model_config = _MODEL_CFG


class FeatureGroup(BaseModel):
    weight: int
    metrics: list[MetricDef]

    model_config = _MODEL_CFG


class Normalization(BaseModel):
    peer_group: list[str]
    winsorize_pct: list[float]
    zscore_clip: float
    # V2: optional sector split for separate normalization
    sector_split: list[str] | None = None

    model_config = _MODEL_CFG


class MissingDataPolicy(BaseModel):
    critical_fields_block: bool
    noncritical_fields_degrade: bool

    model_config = _MODEL_CFG


class FeaturePipeline(BaseModel):
    normalization: Normalization
    missing_data_policy: MissingDataPolicy
    feature_groups: dict[str, FeatureGroup]

    model_config = _MODEL_CFG


class RiskOverlayItem(BaseModel):
    code: str
    penalty: int

    model_config = _MODEL_CFG


class RiskOverlays(BaseModel):
    max_penalty_points: int
    items: list[RiskOverlayItem]

    model_config = _MODEL_CFG


class AttentionOverlay(BaseModel):
    enabled: bool
    positive_alpha_weight: int
    outputs: list[str]

    model_config = _MODEL_CFG


class ScoreBand(BaseModel):
    min: float
    max: float
    label: str

    model_config = _MODEL_CFG


class LabelMapping(BaseModel):
    score_bands: list[ScoreBand]
    blocked_label: str
    # V2: percentile-based labeling
    label_mode: str | None = None       # "percentile" or None (= absolute)
    label_percentiles: dict[str, float] | None = None

    model_config = _MODEL_CFG


class OutputContract(BaseModel):
    schema_ref: str
    required_fields: list[str]
    allowed_tiers: list[str]
    disclaimer_id: str

    model_config = _MODEL_CFG


class Policy(BaseModel):
    result_states: list[str]
    degraded_reason_codes: list[str]
    block_reason_codes: list[str]
    banned_output_actions: list[str]

    model_config = _MODEL_CFG


class Backtest(BaseModel):
    rebalance: str
    evaluation_horizons_days: list[int]
    benchmarks: list[str]

    model_config = _MODEL_CFG


class PromotionGate(BaseModel):
    require_positive_top_quintile_spread: bool
    require_rank_ic_positive_ratio_gte: float
    require_turnover_within_limit: bool
    max_single_industry_weight: float

    model_config = _MODEL_CFG


class Validation(BaseModel):
    backtest: Backtest
    promotion_gate: PromotionGate

    model_config = _MODEL_CFG


class AuditConfig(BaseModel):
    log_required: bool
    audit_keys: list[str]

    model_config = _MODEL_CFG


# ---------------------------------------------------------------------------
# Top-level StrategyConfig
# ---------------------------------------------------------------------------

class StrategyConfig(BaseModel):
    """Full typed representation of a strategy.yaml file."""

    strategy_id: str
    strategy_version: str
    display_name: str
    description: str
    owner: str
    status: str
    mode: str
    timezone: str
    reason_code_registry_ref: str
    data_freshness_profile_ref: str

    schedule: Schedule
    market: Market
    universe: Universe
    data_contract: DataContract
    feature_pipeline: FeaturePipeline
    risk_overlays: RiskOverlays
    attention_overlay: AttentionOverlay
    label_mapping: LabelMapping
    output_contract: OutputContract
    policy: Policy
    validation: Validation
    audit: AuditConfig

    model_config = _MODEL_CFG

    def get_feature_weights(self) -> dict[str, int]:
        """Return feature_group_name → weight mapping."""
        return {name: fg.weight for name, fg in self.feature_pipeline.feature_groups.items()}

    def get_total_weight(self) -> int:
        """Return sum of all feature group weights (should be 100)."""
        return sum(fg.weight for fg in self.feature_pipeline.feature_groups.values())

    def get_risk_overlay_map(self) -> dict[str, int]:
        """Return risk_code → penalty mapping."""
        return {item.code: item.penalty for item in self.risk_overlays.items}

    def get_label_for_score(self, score: float | None) -> str:
        """Map a total_score to the corresponding label string (absolute mode)."""
        if score is None:
            return self.label_mapping.blocked_label
        for band in self.label_mapping.score_bands:
            if band.min <= score <= band.max:
                return band.label
        return self.label_mapping.blocked_label

    def get_label_mode(self) -> str:
        """Return 'percentile' or 'absolute'."""
        return self.label_mapping.label_mode or "absolute"

    def get_label_percentiles(self) -> dict[str, float]:
        """Return label → cumulative percentile mapping for percentile mode."""
        return self.label_mapping.label_percentiles or {}

    def get_effective_weight_coverage_threshold(self) -> float:
        """Minimum effective weight coverage to participate in ranking."""
        # Default 0.0 means no filtering (V1 behavior)
        return 0.0
