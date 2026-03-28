"""Unit tests for Universe Builder."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ai_investor.strategy.loader import load_strategy
from ai_investor.universe.builder import build_universe

STRATEGY_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
    / "ashare_quality_cashflow_regime_v1"
)


def _make_row(**overrides: object) -> dict:
    """Build a minimal valid snapshot row."""
    base = {
        "ticker": "600519",
        "name": "Test",
        "exchange": "SSE",
        "board": "main",
        "security_type": "common_stock",
        "industry_l1": "Test",
        "industry_l2": None,
        "listed_trading_days": 1000,
        "adv20_amount_cny": 500_000_000.0,
        "total_equity": 100_000_000_000.0,
        "has_latest_financials": True,
        "has_standard_unqualified_audit": True,
        "is_st": False,
        "is_delisting": False,
        "is_suspended": False,
    }
    base.update(overrides)
    return base


class TestUniverseBuilder:
    def setup_method(self) -> None:
        self.config = load_strategy(STRATEGY_DIR)

    def test_normal_stock_included(self) -> None:
        df = pl.DataFrame([_make_row()])
        decisions = build_universe(df, self.config)
        assert len(decisions) == 1
        assert decisions[0].included is True
        assert "INC_FULL_MARKET_SEED" in decisions[0].inclusion_reasons
        assert decisions[0].exclusion_reasons == []

    def test_st_stock_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="000001", is_st=True)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_ST_OR_RISK_WARNING" in decisions[0].exclusion_reasons

    def test_etf_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="510050", security_type="etf")])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_NOT_COMMON_STOCK" in decisions[0].exclusion_reasons
        assert "EXC_ETF" in decisions[0].exclusion_reasons

    def test_low_liquidity_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="600001", adv20_amount_cny=10_000.0)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_LOW_LIQUIDITY" in decisions[0].exclusion_reasons

    def test_new_listing_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="301001", listed_trading_days=30)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_INSUFFICIENT_LISTING_DAYS" in decisions[0].exclusion_reasons

    def test_suspended_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="600002", is_suspended=True)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_SUSPENDED" in decisions[0].exclusion_reasons

    def test_no_financials_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="600003", has_latest_financials=False)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_NO_FINANCIALS" in decisions[0].exclusion_reasons

    def test_negative_equity_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="600004", total_equity=-500.0)])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_NEGATIVE_EQUITY" in decisions[0].exclusion_reasons

    def test_wrong_board_excluded(self) -> None:
        df = pl.DataFrame([_make_row(ticker="688001", board="star")])
        decisions = build_universe(df, self.config)
        assert decisions[0].included is False
        assert "EXC_BOARD_NOT_ALLOWED" in decisions[0].exclusion_reasons

    def test_mixed_universe(self) -> None:
        """Multiple stocks, some included, some excluded."""
        df = pl.DataFrame([
            _make_row(ticker="600519"),
            _make_row(ticker="000001", is_st=True),
            _make_row(ticker="300750", board="chinext"),
        ])
        decisions = build_universe(df, self.config)
        assert len(decisions) == 3

        by_ticker = {d.ticker: d for d in decisions}
        assert by_ticker["600519"].included is True
        assert by_ticker["000001"].included is False
        assert by_ticker["300750"].included is True  # chinext is allowed

    def test_inclusion_reasons_are_detailed(self) -> None:
        df = pl.DataFrame([_make_row()])
        decisions = build_universe(df, self.config)
        reasons = decisions[0].inclusion_reasons
        assert "INC_SECURITY_TYPE_COMMON_STOCK" in reasons
        assert "INC_BOARD_ALLOWED" in reasons
        assert "INC_LIQUIDITY_PASS" in reasons
        assert "INC_FINANCIALS_READY" in reasons
        assert "INC_AUDIT_PASS" in reasons
        assert "INC_POSITIVE_EQUITY_PASS" in reasons
