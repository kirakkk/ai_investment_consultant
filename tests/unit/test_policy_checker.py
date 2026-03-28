"""Unit tests for Policy Checker."""

from __future__ import annotations

from ai_investor.features.engine import FeatureResult
from ai_investor.policy.checker import check_policy
from ai_investor.universe.builder import MembershipDecision
from pathlib import Path
from ai_investor.strategy.loader import load_strategy

STRATEGY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
    / "ashare_quality_cashflow_regime_v1"
)


def _config():
    return load_strategy(STRATEGY_DIR)


class TestPolicyCheckerOk:
    def test_complete_data_returns_ok(self) -> None:
        membership = MembershipDecision(
            ticker="600519", included=True,
            inclusion_reasons=["INC_FULL_MARKET_SEED"],
        )
        fr = FeatureResult(
            ticker="600519",
            normalized_features={"roe_ttm": 0.8, "cfo_to_net_profit_ttm": 0.7},
            missing_features=[],
            missing_critical=False,
        )
        result = check_policy(membership, fr, _config())
        assert result.result_state == "ok"
        assert result.state_reason_codes == []
        assert result.critical_complete is True
        assert result.confidence > 0


class TestPolicyCheckerBlocked:
    def test_excluded_from_universe(self) -> None:
        membership = MembershipDecision(
            ticker="600978", included=False,
            exclusion_reasons=["EXC_ST_OR_RISK_WARNING"],
        )
        result = check_policy(membership, None, _config())
        assert result.result_state == "blocked"
        assert "BLK_UNIVERSE_EXCLUDED" in result.state_reason_codes
        assert result.confidence == 0.0

    def test_missing_critical_features(self) -> None:
        membership = MembershipDecision(
            ticker="600900", included=True,
            inclusion_reasons=["INC_FULL_MARKET_SEED"],
        )
        fr = FeatureResult(
            ticker="600900",
            normalized_features={"gross_margin_stability_12q": 0.5},
            missing_features=["roe_ttm", "cfo_to_net_profit_ttm"],
            missing_critical=True,
        )
        result = check_policy(membership, fr, _config())
        assert result.result_state == "blocked"
        assert "BLK_MISSING_CRITICAL_INPUTS" in result.state_reason_codes

    def test_no_feature_result_blocks(self) -> None:
        membership = MembershipDecision(
            ticker="600001", included=True,
            inclusion_reasons=["INC_FULL_MARKET_SEED"],
        )
        result = check_policy(membership, None, _config())
        assert result.result_state == "blocked"
        assert "BLK_MISSING_CRITICAL_INPUTS" in result.state_reason_codes


class TestPolicyCheckerDegraded:
    def test_missing_noncritical_features(self) -> None:
        membership = MembershipDecision(
            ticker="600887", included=True,
            inclusion_reasons=["INC_FULL_MARKET_SEED"],
        )
        fr = FeatureResult(
            ticker="600887",
            normalized_features={"roe_ttm": 0.7, "cfo_to_net_profit_ttm": 0.6},
            missing_features=["pe_pctile_in_industry", "pb_pctile_in_industry"],
            missing_critical=False,
        )
        result = check_policy(membership, fr, _config())
        assert result.result_state == "degraded"
        assert "DGD_MISSING_NONCRITICAL_FEATURES" in result.state_reason_codes
        assert result.confidence < 1.0
        assert result.missing_features == ["pe_pctile_in_industry", "pb_pctile_in_industry"]
