"""Pydantic v2 models for the score_result output contract.

These models mirror `score_result.schema.json` and serve as the runtime
contract between the strategy kernel and all downstream consumers.

Design notes:
- Blocked results set total_score / rank / percentile_rank to None.
- state_reason_codes must be non-empty for degraded/blocked states.
- Code patterns enforce the naming convention in AGENT.md §七.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Exchange(str, Enum):
    SSE = "SSE"
    SZSE = "SZSE"


class Board(str, Enum):
    MAIN = "main"
    CHINEXT = "chinext"
    STAR = "star"
    OTHER = "other"


class SecurityType(str, Enum):
    COMMON_STOCK = "common_stock"
    ETF = "etf"
    REIT = "reit"
    BOND_LIKE = "bond_like"
    B_SHARE = "b_share"
    OTHER = "other"


class ResultState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    RANK_EXCLUDED = "rank_excluded"  # V2: scored but insufficient coverage for ranking


class RenderTier(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"


class SourceType(str, Enum):
    TRUTH = "truth"
    STRUCTURED = "structured"
    ATTENTION = "attention"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Direction(str, Enum):
    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"


class Label(str, Enum):
    FOCUS = "重点研究"
    TRACK = "继续跟踪"
    DEPRIORITIZE = "暂不优先"
    AVOID = "回避"


# ---------------------------------------------------------------------------
# Sub-models ($defs in JSON Schema)
# ---------------------------------------------------------------------------

class SourceRef(BaseModel):
    """Point-in-time reference to a data source snapshot."""

    source_name: str
    source_type: SourceType
    asof: datetime
    source_hash: str

    model_config = {"extra": "forbid"}


class PenaltyItem(BaseModel):
    """Risk overlay penalty applied to total_score."""

    code: Annotated[str, Field(pattern=r"^RISK_[A-Z0-9_]+$")]
    score_impact: Annotated[float, Field(ge=-20, le=0)]
    detail: str
    source_refs: list[SourceRef] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ThesisItem(BaseModel):
    """A positive driver supporting the research conclusion."""

    code: Annotated[str, Field(pattern=r"^DRV_[A-Z0-9_]+$")]
    title: str
    detail: str
    metric_id: str | None = None
    raw_value: float | str | None = None
    normalized_value: float | None = None
    direction: Direction | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class RiskItem(BaseModel):
    """A risk disclosure item in the research output."""

    code: Annotated[str, Field(pattern=r"^RISK_[A-Z0-9_]+$")]
    title: str
    detail: str
    severity: Severity
    source_refs: list[SourceRef] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class TriggerItem(BaseModel):
    """A forward-looking trigger or invalidation condition."""

    code: Annotated[str, Field(pattern=r"^(TRG|INV)_[A-Z0-9_]+$")]
    title: str
    condition: str
    watch_metric: str | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Composite sub-models
# ---------------------------------------------------------------------------

class Security(BaseModel):
    """Security identification block."""

    ticker: str
    exchange: Exchange
    name: str
    board: Board
    security_type: SecurityType
    industry_l1: str
    industry_l2: str | None = None

    model_config = {"extra": "forbid"}


class UniverseMembership(BaseModel):
    """Universe inclusion/exclusion decision with reason codes."""

    included: bool
    inclusion_reasons: list[Annotated[str, Field(pattern=r"^(INC|CND)_[A-Z0-9_]+$")]]
    exclusion_reasons: list[Annotated[str, Field(pattern=r"^EXC_[A-Z0-9_]+$")]]

    model_config = {"extra": "forbid"}


class SubScores(BaseModel):
    """Per-feature-group sub-scores.

    V2: Dynamic — accepts any feature group names from strategy.yaml.
    Risk penalty is always present; all other fields are strategy-specific.
    """

    risk_penalty: Annotated[float, Field(ge=-25, le=0)] = 0.0

    # V1 groups (optional, for backward compat)
    profitability_quality: float | None = None
    cashflow_balance_sheet: float | None = None
    growth_regime: float | None = None
    valuation_sanity: float | None = None
    market_confirmation: float | None = None

    # V2 value groups (optional)
    quality_durability: float | None = None
    value_yield: float | None = None
    capital_allocation: float | None = None

    # V2 growth groups (optional)
    growth_acceleration: float | None = None
    growth_quality: float | None = None
    industry_momentum: float | None = None
    valuation_constraint: float | None = None

    model_config = {"extra": "allow"}


class DataQuality(BaseModel):
    """Data quality summary for audit."""

    critical_complete: bool
    missing_features: list[str]
    stale_sources: list[str]
    degraded_reasons: list[Annotated[str, Field(pattern=r"^DGD_[A-Z0-9_]+$")]]

    model_config = {"extra": "forbid"}


class Audit(BaseModel):
    """Audit trail attached to every research output."""

    render_tier: RenderTier
    disclaimer_id: str
    policy_passed: bool
    banned_terms_checked: bool
    source_hashes: list[str]
    applied_reason_codes: list[str]

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Top-level ScoreResult
# ---------------------------------------------------------------------------

class ScoreResult(BaseModel):
    """Top-level research output for a single security.

    Invariants enforced by model_validator:
    - blocked → total_score/rank/percentile_rank must be None,
                state_reason_codes must be non-empty
    - degraded → state_reason_codes must be non-empty
    - ok → state_reason_codes must be empty
    """

    request_id: str
    run_id: str
    asof: datetime
    strategy_id: str
    strategy_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    universe_version: str
    data_version: str
    feature_version: str
    policy_version: str

    security: Security
    universe_membership: UniverseMembership

    result_state: ResultState
    state_reason_codes: list[Annotated[str, Field(pattern=r"^(DGD|BLK)_[A-Z0-9_]+$")]]

    total_score: Annotated[float | None, Field(ge=0, le=100)]
    rank: Annotated[int | None, Field(ge=1)]
    percentile_rank: Annotated[float | None, Field(ge=0, le=1)]

    sub_scores: SubScores
    penalties: list[PenaltyItem]
    label: str  # V2: dynamic labels, no longer constrained to Label enum
    confidence: Annotated[float, Field(ge=0, le=1)]

    drivers: list[ThesisItem]
    risks: list[RiskItem]
    triggers: list[TriggerItem]
    invalidation: list[TriggerItem]

    data_quality: DataQuality
    audit: Audit

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _check_state_invariants(self) -> "ScoreResult":
        """Enforce state machine rules from AGENT.md §九."""
        if self.result_state == ResultState.BLOCKED:
            if self.total_score is not None:
                raise ValueError("blocked result must have total_score=null")
            if self.rank is not None:
                raise ValueError("blocked result must have rank=null")
            if self.percentile_rank is not None:
                raise ValueError("blocked result must have percentile_rank=null")
            if not self.state_reason_codes:
                raise ValueError("blocked result must have non-empty state_reason_codes")

        elif self.result_state == ResultState.DEGRADED:
            if not self.state_reason_codes:
                raise ValueError("degraded result must have non-empty state_reason_codes")

        elif self.result_state == ResultState.OK:
            if self.state_reason_codes:
                raise ValueError("ok result must have empty state_reason_codes")

        return self
