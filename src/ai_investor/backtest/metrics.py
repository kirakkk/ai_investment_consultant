"""Backtest evaluation metrics.

Provides stateless, pure-function metrics for evaluating a scoring
system's predictive power against realized forward returns.

All functions take simple arrays/lists, not domain objects,
to keep them testable and decoupled from the scoring engine.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def rank_ic(
    scores: list[float],
    returns: list[float | None],
) -> float | None:
    """Compute Spearman Rank IC between scores and forward returns.

    Rank IC = Spearman correlation between score ranks and return ranks.
    Only includes tickers where both score and return are non-None.

    Returns None if fewer than 10 valid pairs (not statistically meaningful).
    """
    # Filter to valid pairs
    pairs = [(s, r) for s, r in zip(scores, returns) if r is not None and not math.isnan(r)]

    if len(pairs) < 10:
        return None

    # Rank both series (average ranks for ties)
    score_vals = [p[0] for p in pairs]
    return_vals = [p[1] for p in pairs]

    score_ranks = _rank_array(score_vals)
    return_ranks = _rank_array(return_vals)

    # Spearman = Pearson on ranks
    n = len(pairs)
    mean_s = sum(score_ranks) / n
    mean_r = sum(return_ranks) / n

    cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(score_ranks, return_ranks))
    var_s = sum((s - mean_s) ** 2 for s in score_ranks)
    var_r = sum((r - mean_r) ** 2 for r in return_ranks)

    denom = math.sqrt(var_s * var_r)
    if denom == 0:
        return 0.0

    return cov / denom


def compute_icir(
    ic_values: list[float],
) -> float | None:
    """Compute ICIR = mean(IC) / std(IC).

    This is the information coefficient's t-statistic across multiple
    cross-sectional slices. Higher ICIR means more consistent predictive power.

    Returns None if fewer than 3 IC values.
    """
    valid = [v for v in ic_values if v is not None]
    if len(valid) < 3:
        return None

    mean_ic = sum(valid) / len(valid)
    var_ic = sum((v - mean_ic) ** 2 for v in valid) / (len(valid) - 1)
    std_ic = math.sqrt(var_ic)

    if std_ic == 0:
        return float("inf") if mean_ic > 0 else float("-inf")

    return mean_ic / std_ic


def topk_excess_return(
    scores: list[float],
    returns: list[float | None],
    tickers: list[str],
    industries: list[str],
    k_pct: float = 0.05,
    max_industry_pct: float = 0.25,
    benchmark_return: float = 0.0,
) -> float | None:
    """Compute excess return of the Top-K pool after industry cap.

    This mirrors the EXACT production logic:
    1. Sort by score descending
    2. Select top k_pct% as initial pool
    3. Apply industry cap (demote excess)
    4. Backfill from next tier to target capacity
    5. Compute mean forward return of final pool
    6. Subtract benchmark return

    Returns None if insufficient data.
    """
    # Build valid entries
    entries = []
    for s, r, t, ind in zip(scores, returns, tickers, industries):
        if r is not None and not math.isnan(r):
            entries.append({"score": s, "return": r, "ticker": t, "industry": ind})

    if len(entries) < 20:
        return None

    # Sort by score descending
    entries.sort(key=lambda x: x["score"], reverse=True)

    # Target capacity
    target_k = max(1, round(len(entries) * k_pct))
    max_per_ind = max(1, int(target_k * max_industry_pct))

    # Phase 1: assign raw top-k
    for i, e in enumerate(entries):
        e["tier"] = "top" if i < target_k else "rest"

    # Phase 2: industry cap on top tier
    ind_count: dict[str, int] = {}
    for e in entries:
        if e["tier"] != "top":
            continue
        ind = e["industry"] or "unknown"
        ind_count[ind] = ind_count.get(ind, 0) + 1
        if ind_count[ind] > max_per_ind:
            e["tier"] = "rest"  # demoted

    # Phase 3: backfill
    current_top = [e for e in entries if e["tier"] == "top"]
    current_count = len(current_top)

    if current_count < target_k:
        # Rebuild industry count
        ind_count_now: dict[str, int] = {}
        for e in current_top:
            ind = e["industry"] or "unknown"
            ind_count_now[ind] = ind_count_now.get(ind, 0) + 1

        rest = [e for e in entries if e["tier"] == "rest"]
        for e in rest:
            if current_count >= target_k:
                break
            ind = e["industry"] or "unknown"
            if ind_count_now.get(ind, 0) < max_per_ind:
                e["tier"] = "top"
                ind_count_now[ind] = ind_count_now.get(ind, 0) + 1
                current_count += 1

    # Compute mean return of final top pool
    final_top = [e for e in entries if e["tier"] == "top"]
    if not final_top:
        return None

    mean_return = sum(e["return"] for e in final_top) / len(final_top)
    return mean_return - benchmark_return


def disaster_rate(
    scores: list[float],
    returns: list[float | None],
    k_pct: float = 0.05,
    threshold: float = -0.20,
) -> float | None:
    """Compute disaster rate: fraction of top-K stocks with return < threshold.

    A "disaster" is a top-ranked stock that loses more than `threshold`
    (default: -20%). Lower is better.

    Returns None if insufficient data.
    """
    # Build valid entries
    pairs = [(s, r) for s, r in zip(scores, returns) if r is not None and not math.isnan(r)]

    if len(pairs) < 20:
        return None

    # Sort by score descending
    pairs.sort(key=lambda x: x[0], reverse=True)

    target_k = max(1, round(len(pairs) * k_pct))
    top_k = pairs[:target_k]

    disasters = sum(1 for _, r in top_k if r < threshold)
    return disasters / len(top_k)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rank_array(values: list[float]) -> list[float]:
    """Compute average ranks for a list of values (1-indexed, ascending)."""
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n

    i = 0
    while i < n:
        j = i
        # Find ties
        while j < n and indexed[j][1] == indexed[i][1]:
            j += 1
        # Average rank for ties
        avg_rank = (i + j + 1) / 2  # 1-indexed average
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j

    return ranks
