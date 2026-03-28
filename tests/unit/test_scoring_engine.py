"""Unit tests for Scoring Engine."""

from __future__ import annotations

from pathlib import Path

from ai_investor.features.engine import FeatureResult
from ai_investor.scoring.engine import build_sub_scores_model, score_universe
from ai_investor.strategy.loader import load_strategy

STRATEGY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
    / "ashare_quality_cashflow_regime_v1"
)


def _make_feature_result(ticker: str, **metric_overrides: float) -> FeatureResult:
    base = {
        "roe_ttm": 0.7, "gross_margin_stability_12q": 0.6, "asset_turnover_delta": 0.5,
        "cfo_to_net_profit_ttm": 0.7, "fcf_ttm_margin": 0.6, "net_debt_to_ebitda": 0.5,
        "receivable_inventory_anomaly": 0.4,
        "revenue_yoy_acceleration": 0.6, "profit_yoy_acceleration": 0.5,
        "operating_margin_delta": 0.5, "industry_regime_strength": 0.6,
        "pe_pctile_in_industry": 0.4, "pb_pctile_in_industry": 0.5,
        "ev_ebitda_pctile_in_industry": 0.4,
        "rs_60d": 0.6, "ma_structure": 0.7, "turnover_support": 0.5,
    }
    base.update(metric_overrides)
    return FeatureResult(ticker=ticker, normalized_features=base)


class TestScoringEngine:
    def setup_method(self) -> None:
        self.config = load_strategy(STRATEGY_DIR)

    def test_score_in_valid_range(self) -> None:
        frs = [_make_feature_result("A")]
        results = score_universe(frs, {}, self.config)
        assert len(results) == 1
        assert 0 <= results[0].total_score <= 100

    def test_ranking_order(self) -> None:
        """Higher normalized features → higher score → better rank."""
        frs = [
            _make_feature_result("LOW", roe_ttm=0.1, cfo_to_net_profit_ttm=0.1),
            _make_feature_result("HIGH", roe_ttm=0.9, cfo_to_net_profit_ttm=0.9),
        ]
        results = score_universe(frs, {}, self.config)
        assert results[0].ticker == "HIGH"  # rank 1
        assert results[1].ticker == "LOW"
        assert results[0].rank == 1
        assert results[1].rank == 2

    def test_risk_penalty_applied(self) -> None:
        frs = [_make_feature_result("A")]
        rows = {"A": {"risk_goodwill_high": True}}
        results = score_universe(frs, rows, self.config)
        assert len(results[0].penalties) == 1
        assert results[0].penalties[0].code == "RISK_GOODWILL_HIGH"
        assert results[0].risk_penalty_total < 0

    def test_multiple_penalties_capped(self) -> None:
        """Total penalties should not exceed max_penalty_points."""
        frs = [_make_feature_result("A")]
        rows = {"A": {
            "risk_goodwill_high": True,      # 5
            "risk_equity_pledge_high": True,  # 5
            "risk_regulatory_probe": True,    # 8
            "risk_major_reduction": True,     # 3
        }}
        results = score_universe(frs, rows, self.config)
        # max_penalty_points is 20, total = 5+5+8+3 = 21, should cap at -20
        assert results[0].risk_penalty_total >= -20

    def test_label_assigned(self) -> None:
        frs = [_make_feature_result("A")]
        results = score_universe(frs, {}, self.config)
        assert results[0].label in ["重点研究", "继续跟踪", "暂不优先", "回避"]

    def test_percentile_rank_bounds(self) -> None:
        frs = [_make_feature_result("A"), _make_feature_result("B")]
        results = score_universe(frs, {}, self.config)
        for r in results:
            assert r.percentile_rank is not None
            assert 0 <= r.percentile_rank <= 1


class TestBuildSubScoresModel:
    def test_constructs_valid_model(self) -> None:
        sub = build_sub_scores_model(
            {"profitability_quality": 15.0, "cashflow_balance_sheet": 12.0,
             "growth_regime": 10.0, "valuation_sanity": 8.0, "market_confirmation": 14.0},
            -5.0,
        )
        assert sub.profitability_quality == 15.0
        assert sub.risk_penalty == -5.0
