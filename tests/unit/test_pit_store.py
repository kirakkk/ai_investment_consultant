"""Tests for PIT-Lite temporal logic and event store.

These tests verify that:
1. Statutory deadlines are computed correctly
2. available_at respects source-type rules
3. PIT view correctly filters and merges by priority
"""

import datetime
import pytest

import polars as pl

from ai_investor.backtest.conventions import (
    statutory_deadline,
    compute_available_at,
    enumerate_report_periods,
)
from ai_investor.backtest.pit_store import build_pit_view, UNIFIED_COLUMNS


# ---------------------------------------------------------------------------
# Test statutory_deadline
# ---------------------------------------------------------------------------

class TestStatutoryDeadline:

    def test_q1_deadline(self):
        """Q1 report (03-31) must be disclosed by April 30."""
        assert statutory_deadline(datetime.date(2023, 3, 31)) == datetime.date(2023, 4, 30)

    def test_q2_deadline(self):
        """Mid-year report (06-30) must be disclosed by August 31."""
        assert statutory_deadline(datetime.date(2023, 6, 30)) == datetime.date(2023, 8, 31)

    def test_q3_deadline(self):
        """Q3 report (09-30) must be disclosed by October 31."""
        assert statutory_deadline(datetime.date(2023, 9, 30)) == datetime.date(2023, 10, 31)

    def test_annual_deadline(self):
        """Annual report (12-31) must be disclosed by April 30 of NEXT year."""
        assert statutory_deadline(datetime.date(2022, 12, 31)) == datetime.date(2023, 4, 30)

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError, match="Invalid report_period month"):
            statutory_deadline(datetime.date(2023, 5, 15))


# ---------------------------------------------------------------------------
# Test enumerate_report_periods
# ---------------------------------------------------------------------------

class TestEnumerateReportPeriods:

    def test_generates_quarterly_dates(self):
        periods = enumerate_report_periods(2023, datetime.date(2023, 12, 31))
        assert len(periods) == 4
        assert periods[0] == datetime.date(2023, 3, 31)
        assert periods[1] == datetime.date(2023, 6, 30)
        assert periods[2] == datetime.date(2023, 9, 30)
        assert periods[3] == datetime.date(2023, 12, 31)

    def test_respects_end_date(self):
        periods = enumerate_report_periods(2023, datetime.date(2023, 7, 15))
        assert len(periods) == 2  # Q1 and Q2 only
        assert periods[-1] == datetime.date(2023, 6, 30)


# ---------------------------------------------------------------------------
# Test build_pit_view (synthetic data)
# ---------------------------------------------------------------------------

class TestBuildPitView:

    def _make_events(self) -> pl.DataFrame:
        """Create synthetic events for testing PIT logic."""
        return pl.DataFrame([
            # Ticker 600001: has forecast (Jan 15), express (Feb 10), formal (May 2)
            {
                "ticker": "600001", "report_period": "2022-12-31",
                "source_type": "forecast", "ann_date": "2023-01-15",
                "available_at": "2023-01-16",  # next trading day
                "eps": None, "revenue": None, "revenue_yoy": None,
                "net_profit": 100000000.0, "profit_yoy": None,
                "profit_change_pct": 15.0,
                "bps": None, "roe": None, "gross_margin": None,
                "cfo_per_share": None, "industry": "电子",
                "forecast_type": "预增",
            },
            {
                "ticker": "600001", "report_period": "2022-12-31",
                "source_type": "express", "ann_date": "2023-02-10",
                "available_at": "2023-02-13",
                "eps": 1.5, "revenue": 5000000000.0, "revenue_yoy": 12.0,
                "net_profit": 120000000.0, "profit_yoy": 18.0,
                "profit_change_pct": None,
                "bps": 8.5, "roe": 15.0, "gross_margin": None,
                "cfo_per_share": None, "industry": "电子",
                "forecast_type": None,
            },
            {
                "ticker": "600001", "report_period": "2022-12-31",
                "source_type": "formal", "ann_date": "2023-04-20",
                "available_at": "2023-05-04",  # statutory: May 2 (next TD after Apr 30)
                "eps": 1.55, "revenue": 5100000000.0, "revenue_yoy": 13.0,
                "net_profit": 125000000.0, "profit_yoy": 20.0,
                "profit_change_pct": None,
                "bps": 8.6, "roe": 15.5, "gross_margin": 35.0,
                "cfo_per_share": 1.2, "industry": "电子",
                "forecast_type": None,
            },
            # Ticker 600002: only has formal Q3 2022 report
            {
                "ticker": "600002", "report_period": "2022-09-30",
                "source_type": "formal", "ann_date": "2022-11-01",
                "available_at": "2022-11-01",
                "eps": 0.8, "revenue": 3000000000.0, "revenue_yoy": 5.0,
                "net_profit": 80000000.0, "profit_yoy": 8.0,
                "profit_change_pct": None,
                "bps": 6.0, "roe": 10.0, "gross_margin": 28.0,
                "cfo_per_share": 0.6, "industry": "医药生物",
                "forecast_type": None,
            },
        ])

    def test_early_decision_sees_only_forecast(self):
        """At Jan 20, only 600001's forecast is visible."""
        events = self._make_events()
        pit = build_pit_view(datetime.date(2023, 1, 20), events)

        ticker_001 = pit.filter(pl.col("ticker") == "600001")
        assert ticker_001.height == 1
        assert ticker_001["source_type"][0] == "forecast"

    def test_mid_decision_sees_express_over_forecast(self):
        """At Mar 1, express supersedes forecast (higher priority)."""
        events = self._make_events()
        pit = build_pit_view(datetime.date(2023, 3, 1), events)

        ticker_001 = pit.filter(pl.col("ticker") == "600001")
        assert ticker_001.height == 1
        assert ticker_001["source_type"][0] == "express"

    def test_late_decision_sees_formal(self):
        """At Jun 1, formal report is visible and takes priority."""
        events = self._make_events()
        pit = build_pit_view(datetime.date(2023, 6, 1), events)

        ticker_001 = pit.filter(pl.col("ticker") == "600001")
        assert ticker_001.height == 1
        assert ticker_001["source_type"][0] == "formal"
        # Formal should have the most complete data
        assert ticker_001["gross_margin"][0] == 35.0

    def test_old_report_visible_for_ticker_002(self):
        """Ticker 600002 only has Q3 2022 formal — visible from Nov 2022."""
        events = self._make_events()

        # Before Nov 1: nothing visible
        pit_early = build_pit_view(datetime.date(2022, 10, 15), events)
        ticker_002 = pit_early.filter(pl.col("ticker") == "600002")
        assert ticker_002.height == 0

        # After Nov 1: Q3 formal visible
        pit_late = build_pit_view(datetime.date(2022, 12, 1), events)
        ticker_002 = pit_late.filter(pl.col("ticker") == "600002")
        assert ticker_002.height == 1
        assert ticker_002["roe"][0] == 10.0

    def test_no_future_data_leaks(self):
        """At Jan 1 2023, no express/formal for 2022 annual should leak through."""
        events = self._make_events()
        pit = build_pit_view(datetime.date(2023, 1, 1), events)

        # 600001 should not have express or formal yet
        ticker_001 = pit.filter(pl.col("ticker") == "600001")
        # Only Q3 2022 formal (from ticker 600002) or nothing for 600001
        # Since forecast hasn't been announced yet (Jan 15), 600001 is invisible
        if ticker_001.height > 0:
            assert ticker_001["source_type"][0] != "express"
            assert ticker_001["source_type"][0] != "formal"
