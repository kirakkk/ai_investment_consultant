"""Unit tests for Feature Engine."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ai_investor.features.engine import FeatureResult, compute_features
from ai_investor.strategy.loader import load_strategy

STRATEGY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
    / "ashare_quality_cashflow_regime_v1"
)


def _make_snapshot_df(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _base_row(ticker: str = "600519", **overrides: object) -> dict:
    row: dict = {
        "ticker": ticker, "name": "Test", "exchange": "SSE",
        "board": "main", "industry_l1": "Test",
        "roe_ttm": 0.25, "gross_margin_stability_12q": 0.85, "asset_turnover_delta": 0.03,
        "cfo_to_net_profit_ttm": 1.1, "fcf_ttm_margin": 0.2, "net_debt_to_ebitda": 0.5,
        "receivable_inventory_anomaly": 0.15,
        "revenue_yoy_acceleration": 0.1, "profit_yoy_acceleration": 0.12,
        "operating_margin_delta": 0.02, "industry_regime_strength": 0.7,
        "pe_pctile_in_industry": 0.3, "pb_pctile_in_industry": 0.35,
        "ev_ebitda_pctile_in_industry": 0.32,
        "rs_60d": 0.7, "ma_structure": 0.75, "turnover_support": 0.65,
    }
    row.update(overrides)
    return row


class TestFeatureEngine:
    def setup_method(self) -> None:
        self.config = load_strategy(STRATEGY_DIR)

    def test_single_stock_produces_features(self) -> None:
        df = _make_snapshot_df([_base_row()])
        results = compute_features(df, self.config, {"600519"})
        assert len(results) == 1
        assert len(results[0].normalized_features) > 0
        assert results[0].missing_critical is False

    def test_normalized_values_in_0_1(self) -> None:
        """With a single stock, z-score maps to 0.5 (single point → no variance)."""
        df = _make_snapshot_df([_base_row()])
        results = compute_features(df, self.config, {"600519"})
        for val in results[0].normalized_features.values():
            assert 0.0 <= val <= 1.0, f"Normalized value {val} out of [0,1]"

    def test_multiple_stocks_ranking_separation(self) -> None:
        """Two stocks with different values should get different normalized scores."""
        rows = [
            _base_row("A", roe_ttm=0.10, cfo_to_net_profit_ttm=0.5),
            _base_row("B", roe_ttm=0.40, cfo_to_net_profit_ttm=1.5),
        ]
        df = _make_snapshot_df(rows)
        results = compute_features(df, self.config, {"A", "B"})
        by_ticker = {r.ticker: r for r in results}
        # B should have higher roe_ttm normalized value
        assert by_ticker["B"].normalized_features["roe_ttm"] > by_ticker["A"].normalized_features["roe_ttm"]

    def test_missing_noncritical_feature_tracked(self) -> None:
        """Missing non-critical metric appears in missing_features."""
        row = _base_row()
        row["gross_margin_stability_12q"] = None  # non-critical
        df = _make_snapshot_df([row])
        results = compute_features(df, self.config, {"600519"})
        assert "gross_margin_stability_12q" in results[0].missing_features
        assert results[0].missing_critical is False

    def test_missing_critical_feature_flagged(self) -> None:
        """Missing critical metric sets missing_critical=True."""
        row = _base_row()
        row["roe_ttm"] = None  # critical
        df = _make_snapshot_df([row])
        results = compute_features(df, self.config, {"600519"})
        assert "roe_ttm" in results[0].missing_features
        assert results[0].missing_critical is True

    def test_excluded_tickers_not_computed(self) -> None:
        """Only included tickers get features computed."""
        df = _make_snapshot_df([_base_row("A"), _base_row("B")])
        results = compute_features(df, self.config, {"A"})  # B not included
        assert len(results) == 1
        assert results[0].ticker == "A"

    def test_empty_included_returns_empty(self) -> None:
        df = _make_snapshot_df([_base_row()])
        results = compute_features(df, self.config, set())
        assert results == []
