"""Data connector protocol — provider-agnostic interface.

All connectors must implement these methods. The pipeline never
imports a concrete connector directly; it receives a DataConnector
instance via dependency injection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class DataConnector(Protocol):
    """Abstract interface for a market-data / financials connector."""

    def fetch_stock_list(self) -> pl.DataFrame:
        """Return full market stock list.

        Required columns:
            ticker: str, name: str, exchange: str (SSE/SZSE),
            board: str, security_type: str
        """
        ...

    def fetch_financials(self, tickers: list[str]) -> pl.DataFrame:
        """Return financial indicators for given tickers.

        Required columns:
            ticker, roe_ttm, gross_margin_stability_12q,
            asset_turnover_delta, cfo_to_net_profit_ttm,
            fcf_ttm_margin, net_debt_to_ebitda,
            receivable_inventory_anomaly,
            revenue_yoy_acceleration, profit_yoy_acceleration,
            operating_margin_delta,
            eps_ttm, bps,
            revenue_cagr_3y, profit_cagr_3y,
            has_latest_financials, has_standard_unqualified_audit,
            total_equity
        """
        ...

    def fetch_market_data(self, tickers: list[str]) -> pl.DataFrame:
        """Return market/trading data for given tickers.

        Required columns:
            ticker, listed_trading_days, adv20_amount_cny,
            revenue_yoy_acceleration, profit_yoy_acceleration,
            operating_margin_delta, industry_regime_strength,
            pe_pctile_in_industry, pb_pctile_in_industry,
            ev_ebitda_pctile_in_industry,
            rs_60d, ma_structure, turnover_support
        """
        ...

    def fetch_valuation(self, tickers: list[str]) -> pl.DataFrame:
        """Return raw valuation metrics for given tickers.

        Required columns:
            ticker, pe_ttm_raw, pb_raw
        """
        ...

    def fetch_risk_flags(self, tickers: list[str]) -> pl.DataFrame:
        """Return risk flag booleans for given tickers.

        Required columns:
            ticker, is_st, is_delisting, is_suspended,
            risk_goodwill_high, risk_equity_pledge_high,
            risk_regulatory_probe, risk_major_reduction,
            risk_material_negative_announcement
        """
        ...

    def fetch_industry(self, tickers: list[str]) -> pl.DataFrame:
        """Return industry classification for given tickers.

        Required columns:
            ticker, industry_l1, industry_l2
        """
        ...
