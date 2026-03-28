"""Universe Builder — construct the research universe from PIT snapshots.

Takes a Polars DataFrame of PIT snapshot data plus a StrategyConfig,
applies hard exclusions and eligibility rules, and returns per-ticker
membership decisions with reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from ai_investor.models.strategy import StrategyConfig


@dataclass
class MembershipDecision:
    """Result of universe membership decision for a single ticker."""

    ticker: str
    included: bool
    inclusion_reasons: list[str] = field(default_factory=list)
    exclusion_reasons: list[str] = field(default_factory=list)


def build_universe(
    snapshot: pl.DataFrame,
    config: StrategyConfig,
) -> list[MembershipDecision]:
    """Build the research universe from a PIT snapshot.

    Expected snapshot columns:
        - ticker: str
        - name: str
        - exchange: str ("SSE" / "SZSE")
        - board: str ("main" / "chinext" / "star" / "other")
        - security_type: str ("common_stock", "etf", ...)
        - industry_l1: str
        - listed_trading_days: int
        - adv20_amount_cny: float
        - total_equity: float
        - has_latest_financials: bool
        - has_standard_unqualified_audit: bool
        - is_st: bool
        - is_delisting: bool
        - is_suspended: bool

    Returns:
        List of MembershipDecision, one per ticker in the snapshot.
    """
    decisions: list[MembershipDecision] = []
    allowed_boards = set(config.market.boards)
    allowed_types = set(config.market.security_types)
    allowed_exchanges = set(config.market.exchanges)
    elig = config.universe.eligibility_rules

    hard_exclusion_set = set(config.universe.hard_exclusions)

    for row in snapshot.iter_rows(named=True):
        ticker = str(row["ticker"])
        inclusion_reasons: list[str] = []
        exclusion_reasons: list[str] = []

        # --- Hard exclusions ---
        if row.get("security_type") != "common_stock" and "EXC_NOT_COMMON_STOCK" in hard_exclusion_set:
            exclusion_reasons.append("EXC_NOT_COMMON_STOCK")
        if row.get("security_type") == "etf" and "EXC_ETF" in hard_exclusion_set:
            exclusion_reasons.append("EXC_ETF")
        if row.get("security_type") == "reit" and "EXC_REIT" in hard_exclusion_set:
            exclusion_reasons.append("EXC_REIT")
        if row.get("security_type") == "b_share" and "EXC_B_SHARE" in hard_exclusion_set:
            exclusion_reasons.append("EXC_B_SHARE")
        if row.get("security_type") == "bond_like" and "EXC_BOND_LIKE" in hard_exclusion_set:
            exclusion_reasons.append("EXC_BOND_LIKE")
        if row.get("is_st", False) and "EXC_ST_OR_RISK_WARNING" in hard_exclusion_set:
            exclusion_reasons.append("EXC_ST_OR_RISK_WARNING")
        if row.get("is_delisting", False) and "EXC_DELISTING_PHASE" in hard_exclusion_set:
            exclusion_reasons.append("EXC_DELISTING_PHASE")
        if row.get("is_suspended", False) and "EXC_SUSPENDED" in hard_exclusion_set:
            exclusion_reasons.append("EXC_SUSPENDED")
        if row.get("board") not in allowed_boards and "EXC_BOARD_NOT_ALLOWED" in hard_exclusion_set:
            exclusion_reasons.append("EXC_BOARD_NOT_ALLOWED")

        # --- Eligibility rules ---
        min_days = elig.get("min_listed_trading_days", 0)
        list_days = row.get("listed_trading_days")
        if isinstance(min_days, (int, float)) and (list_days is None or list_days < min_days):
            exclusion_reasons.append("EXC_INSUFFICIENT_LISTING_DAYS")

        min_adv = elig.get("min_adv20_amount_cny", 0)
        adv = row.get("adv20_amount_cny")
        if isinstance(min_adv, (int, float)) and (adv is None or adv < min_adv):
            exclusion_reasons.append("EXC_LOW_LIQUIDITY")

        eq = row.get("total_equity")
        if elig.get("require_positive_equity") and (eq is None or eq <= 0):
            exclusion_reasons.append("EXC_NEGATIVE_EQUITY")

        if elig.get("require_latest_financials") and not row.get("has_latest_financials", False):
            exclusion_reasons.append("EXC_NO_FINANCIALS")

        if elig.get("require_standard_unqualified_audit") and not row.get("has_standard_unqualified_audit", False):
            exclusion_reasons.append("EXC_AUDIT_ISSUE")

        # --- Determine inclusion ---
        if not exclusion_reasons:
            # Check exchange
            exchange = row.get("exchange", "")
            if exchange in allowed_exchanges:
                inclusion_reasons.append("INC_FULL_MARKET_SEED")

                # Record specific pass reasons
                if row.get("security_type") == "common_stock":
                    inclusion_reasons.append("INC_SECURITY_TYPE_COMMON_STOCK")
                if row.get("board") in allowed_boards:
                    inclusion_reasons.append("INC_BOARD_ALLOWED")
                if isinstance(min_days, (int, float)) and row.get("listed_trading_days", 0) >= min_days:
                    inclusion_reasons.append("INC_LISTED_DAYS_PASS")
                if isinstance(min_adv, (int, float)) and row.get("adv20_amount_cny", 0) >= min_adv:
                    inclusion_reasons.append("INC_LIQUIDITY_PASS")
                if row.get("has_latest_financials", False):
                    inclusion_reasons.append("INC_FINANCIALS_READY")
                if row.get("has_standard_unqualified_audit", False):
                    inclusion_reasons.append("INC_AUDIT_PASS")
                if row.get("total_equity", 0) > 0:
                    inclusion_reasons.append("INC_POSITIVE_EQUITY_PASS")

        included = len(exclusion_reasons) == 0 and len(inclusion_reasons) > 0

        decisions.append(MembershipDecision(
            ticker=ticker,
            included=included,
            inclusion_reasons=inclusion_reasons,
            exclusion_reasons=exclusion_reasons,
        ))

    return decisions
