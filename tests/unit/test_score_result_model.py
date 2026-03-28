"""Unit tests for ScoreResult Pydantic model — state machine invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_investor.models.score_result import ScoreResult


def _make_base_data(**overrides: object) -> dict:
    """Build a minimal valid ScoreResult dict, with overrides."""
    base = {
        "request_id": "req-test-001",
        "run_id": "run-test-001",
        "asof": "2026-03-24T15:00:00+08:00",
        "strategy_id": "test_strategy",
        "strategy_version": "0.1.0",
        "universe_version": "uv-001",
        "data_version": "dv-001",
        "feature_version": "fv-001",
        "policy_version": "pv-001",
        "security": {
            "ticker": "600519",
            "exchange": "SSE",
            "name": "Test",
            "board": "main",
            "security_type": "common_stock",
            "industry_l1": "Test",
        },
        "universe_membership": {
            "included": True,
            "inclusion_reasons": ["INC_FULL_MARKET_SEED"],
            "exclusion_reasons": [],
        },
        "result_state": "ok",
        "state_reason_codes": [],
        "total_score": 75.0,
        "rank": 1,
        "percentile_rank": 0.1,
        "sub_scores": {
            "profitability_quality": 20.0,
            "cashflow_balance_sheet": 15.0,
            "growth_regime": 15.0,
            "valuation_sanity": 10.0,
            "market_confirmation": 18.0,
            "risk_penalty": -3.0,
        },
        "penalties": [],
        "label": "继续跟踪",
        "confidence": 0.9,
        "drivers": [],
        "risks": [],
        "triggers": [],
        "invalidation": [],
        "data_quality": {
            "critical_complete": True,
            "missing_features": [],
            "stale_sources": [],
            "degraded_reasons": [],
        },
        "audit": {
            "render_tier": "R0",
            "disclaimer_id": "CN_R0_RESEARCH_ONLY_V1",
            "policy_passed": True,
            "banned_terms_checked": True,
            "source_hashes": [],
            "applied_reason_codes": ["INC_FULL_MARKET_SEED"],
        },
    }
    base.update(overrides)
    return base


class TestScoreResultOk:
    def test_valid_ok_result(self) -> None:
        data = _make_base_data()
        result = ScoreResult.model_validate(data)
        assert result.result_state.value == "ok"
        assert result.total_score == 75.0

    def test_ok_with_nonempty_reasons_fails(self) -> None:
        data = _make_base_data(state_reason_codes=["DGD_SOMETHING"])
        with pytest.raises(ValidationError, match="ok result must have empty state_reason_codes"):
            ScoreResult.model_validate(data)


class TestScoreResultBlocked:
    def test_valid_blocked_result(self) -> None:
        data = _make_base_data(
            result_state="blocked",
            state_reason_codes=["BLK_UNIVERSE_EXCLUDED"],
            total_score=None,
            rank=None,
            percentile_rank=None,
            label="回避",
        )
        result = ScoreResult.model_validate(data)
        assert result.result_state.value == "blocked"
        assert result.total_score is None

    def test_blocked_with_score_fails(self) -> None:
        data = _make_base_data(
            result_state="blocked",
            state_reason_codes=["BLK_UNIVERSE_EXCLUDED"],
            total_score=50.0,
            rank=None,
            percentile_rank=None,
            label="回避",
        )
        with pytest.raises(ValidationError, match="blocked result must have total_score=null"):
            ScoreResult.model_validate(data)

    def test_blocked_with_empty_reasons_fails(self) -> None:
        data = _make_base_data(
            result_state="blocked",
            state_reason_codes=[],
            total_score=None,
            rank=None,
            percentile_rank=None,
            label="回避",
        )
        with pytest.raises(ValidationError, match="blocked result must have non-empty state_reason_codes"):
            ScoreResult.model_validate(data)

    def test_blocked_with_rank_fails(self) -> None:
        data = _make_base_data(
            result_state="blocked",
            state_reason_codes=["BLK_UNIVERSE_EXCLUDED"],
            total_score=None,
            rank=5,
            percentile_rank=None,
            label="回避",
        )
        with pytest.raises(ValidationError, match="blocked result must have rank=null"):
            ScoreResult.model_validate(data)


class TestScoreResultDegraded:
    def test_valid_degraded_result(self) -> None:
        data = _make_base_data(
            result_state="degraded",
            state_reason_codes=["DGD_MISSING_NONCRITICAL_FEATURES"],
        )
        result = ScoreResult.model_validate(data)
        assert result.result_state.value == "degraded"
        assert len(result.state_reason_codes) > 0

    def test_degraded_with_empty_reasons_fails(self) -> None:
        data = _make_base_data(
            result_state="degraded",
            state_reason_codes=[],
        )
        with pytest.raises(ValidationError, match="degraded result must have non-empty state_reason_codes"):
            ScoreResult.model_validate(data)


class TestCodePatterns:
    def test_invalid_penalty_code_pattern(self) -> None:
        data = _make_base_data(
            penalties=[{"code": "BAD_CODE", "score_impact": -5, "detail": "test"}],
        )
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(data)

    def test_invalid_driver_code_pattern(self) -> None:
        data = _make_base_data(
            drivers=[{"code": "BAD_CODE", "title": "t", "detail": "d"}],
        )
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(data)

    def test_invalid_exclusion_reason_pattern(self) -> None:
        data = _make_base_data()
        data["universe_membership"]["exclusion_reasons"] = ["BAD_PREFIX"]
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(data)

    def test_score_out_of_range(self) -> None:
        data = _make_base_data(total_score=150.0)
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(data)

    def test_extra_field_forbidden(self) -> None:
        data = _make_base_data(extra_field="not_allowed")
        with pytest.raises(ValidationError):
            ScoreResult.model_validate(data)
