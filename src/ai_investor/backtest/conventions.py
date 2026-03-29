"""As-of conventions for backtesting.

Defines the single-source-of-truth for all temporal definitions used
in historical snapshot construction, forward return calculation, and
point-in-time data alignment.

V3 audit decisions (locked):
  - pit_mode:    "non_strict_90d" (only option for V3.0)
  - entry_price: "t1_close" (only option for V3.0)
  - financial_cutoff: trade_date - 90 calendar days

These are NOT configurable. Changing them requires a new audit cycle.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trading calendar (loaded once, cached)
# ---------------------------------------------------------------------------

_TRADE_DATES: list[datetime.date] | None = None


def _load_trade_dates() -> list[datetime.date]:
    """Load full A-share trading calendar from Sina via AKShare."""
    global _TRADE_DATES
    if _TRADE_DATES is not None:
        return _TRADE_DATES

    import akshare as ak

    cal = ak.tool_trade_date_hist_sina()
    col = cal.columns[0]
    dates = sorted(cal[col].apply(lambda x: x if isinstance(x, datetime.date)
                                  else datetime.date.fromisoformat(str(x))).tolist())
    _TRADE_DATES = dates
    logger.info(f"Loaded trading calendar: {len(dates)} days "
                f"({dates[0]} → {dates[-1]})")
    return _TRADE_DATES


def resolve_trade_date(base_date: datetime.date) -> datetime.date:
    """Return base_date if it is a trading day, else the most recent
    trading day before it.

    >>> resolve_trade_date(datetime.date(2025, 1, 1))
    datetime.date(2024, 12, 31)
    """
    dates = _load_trade_dates()
    if base_date in dates:
        return base_date
    # Binary search for the last trading day <= base_date
    import bisect
    idx = bisect.bisect_right(dates, base_date) - 1
    if idx < 0:
        raise ValueError(f"No trading day found on or before {base_date}")
    return dates[idx]


def get_next_trade_date(trade_date: datetime.date) -> datetime.date:
    """Return the next trading day after trade_date (exclusive).

    This is the T+1 date used for forward return start.
    """
    dates = _load_trade_dates()
    import bisect
    idx = bisect.bisect_right(dates, trade_date)
    if idx >= len(dates):
        raise ValueError(f"No trading day found after {trade_date}")
    return dates[idx]


def get_trade_date_offset(trade_date: datetime.date, offset: int) -> datetime.date:
    """Return the trading day that is `offset` trading days from trade_date.

    offset > 0: future
    offset < 0: past
    offset = 0: trade_date itself
    """
    dates = _load_trade_dates()
    import bisect
    idx = bisect.bisect_left(dates, trade_date)
    if idx >= len(dates) or dates[idx] != trade_date:
        raise ValueError(f"{trade_date} is not a trading day")
    target = idx + offset
    if target < 0 or target >= len(dates):
        raise ValueError(f"Offset {offset} from {trade_date} is out of calendar range")
    return dates[target]


# ---------------------------------------------------------------------------
# As-Of Context
# ---------------------------------------------------------------------------

# Forward return windows: label → trading days
HORIZON_WINDOWS: dict[str, int] = {
    "1m": 20,
    "3m": 60,
    "6m": 120,
    "1y": 250,
}

# Coverage gates: minimum non-null fraction to proceed
COVERAGE_GATES: dict[str, float] = {
    "roe_ttm": 0.60,
    "rs_60d": 0.70,
    "eps_yield": 0.50,
}


@dataclass(frozen=True)
class AsOfContext:
    """Immutable temporal context for a backtest slice.

    All dates are resolved trading days. All downstream code
    MUST use this context instead of computing dates independently.
    """

    # User-specified reference date (may be non-trading day)
    base_date: datetime.date

    # Resolved trading day: base_date or most recent before it
    trade_date: datetime.date

    # Financial data cutoff: reports with 报告期 <= this date are visible
    # Non-strict PIT: trade_date - 90 calendar days
    financial_cutoff: datetime.date

    # Forward return entry date: T+1 (next trading day after trade_date)
    entry_date: datetime.date

    # PIT mode identifier (for audit trail)
    pit_mode: str = "non_strict_90d"

    # Entry price type (for audit trail)
    entry_price_type: str = "t1_close"

    def __post_init__(self) -> None:
        assert self.trade_date <= self.base_date
        assert self.financial_cutoff < self.trade_date
        assert self.entry_date > self.trade_date

    @staticmethod
    def build(base_date: datetime.date) -> AsOfContext:
        """Construct an AsOfContext from a user-specified base_date.

        This is the ONLY way to create an AsOfContext. Direct construction
        is discouraged to prevent inconsistent date resolution.
        """
        trade_date = resolve_trade_date(base_date)
        financial_cutoff = trade_date - datetime.timedelta(days=90)
        entry_date = get_next_trade_date(trade_date)

        ctx = AsOfContext(
            base_date=base_date,
            trade_date=trade_date,
            financial_cutoff=financial_cutoff,
            entry_date=entry_date,
        )

        logger.info(
            f"AsOfContext built: base={base_date}, trade={trade_date}, "
            f"fin_cutoff={financial_cutoff}, entry={entry_date}, "
            f"pit={ctx.pit_mode}, price={ctx.entry_price_type}"
        )
        return ctx

    def to_dict(self) -> dict:
        """Serialize for manifest / audit trail."""
        return {
            "base_date": self.base_date.isoformat(),
            "trade_date": self.trade_date.isoformat(),
            "financial_cutoff": self.financial_cutoff.isoformat(),
            "entry_date": self.entry_date.isoformat(),
            "pit_mode": self.pit_mode,
            "entry_price_type": self.entry_price_type,
        }
