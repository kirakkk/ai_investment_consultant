"""Unit tests for strategy loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_investor.models.strategy import StrategyConfig
from ai_investor.strategy.loader import discover_strategies, load_strategy

REGISTRY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
)
STRATEGY_DIR = REGISTRY_DIR / "ashare_quality_cashflow_regime_v1"


class TestLoadStrategy:
    def test_loads_valid_strategy(self) -> None:
        config = load_strategy(STRATEGY_DIR)
        assert isinstance(config, StrategyConfig)
        assert config.strategy_id == "ashare_quality_cashflow_regime_v1"
        assert config.strategy_version == "0.1.0"

    def test_strategy_has_feature_groups(self) -> None:
        config = load_strategy(STRATEGY_DIR)
        groups = config.feature_pipeline.feature_groups
        assert "profitability_quality" in groups
        assert "cashflow_balance_sheet" in groups
        assert "growth_regime" in groups
        assert "valuation_sanity" in groups
        assert "market_confirmation" in groups

    def test_total_weight_is_100(self) -> None:
        config = load_strategy(STRATEGY_DIR)
        assert config.get_total_weight() == 100

    def test_risk_overlay_map(self) -> None:
        config = load_strategy(STRATEGY_DIR)
        risk_map = config.get_risk_overlay_map()
        assert "RISK_GOODWILL_HIGH" in risk_map
        assert risk_map["RISK_GOODWILL_HIGH"] == 5

    def test_label_mapping(self) -> None:
        config = load_strategy(STRATEGY_DIR)
        assert config.get_label_for_score(85.0) == "重点研究"
        assert config.get_label_for_score(70.0) == "继续跟踪"
        assert config.get_label_for_score(50.0) == "暂不优先"
        assert config.get_label_for_score(30.0) == "回避"
        assert config.get_label_for_score(None) == "回避"

    def test_missing_strategy_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_strategy(tmp_path)


class TestDiscoverStrategies:
    def test_discovers_strategy(self) -> None:
        strategies = discover_strategies(REGISTRY_DIR)
        assert "ashare_quality_cashflow_regime_v1" in strategies

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        assert discover_strategies(tmp_path) == {}
