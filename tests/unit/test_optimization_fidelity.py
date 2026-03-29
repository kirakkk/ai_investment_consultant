"""Golden fidelity tests: prove optimization path ≡ production path.

These tests guarantee that `score_with_weights()` produces the exact
same rankings as `score_universe()` for any given weight configuration.
If these tests fail, the optimization results are NOT trustworthy.
"""

import math
from pathlib import Path

import pytest

from ai_investor.features.engine import FeatureResult, compute_features
from ai_investor.scoring.engine import ScoringResult, score_universe
from ai_investor.strategy.loader import load_strategy
from ai_investor.backtest.optimizer import (
    PrecomputedSlice,
    precompute_slice,
    score_with_weights,
    logits_to_weights,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal synthetic data
# ---------------------------------------------------------------------------

def _make_feature_results() -> list[FeatureResult]:
    """Create synthetic FeatureResults for 20 tickers across 4 groups."""
    results = []
    for i in range(20):
        # Simulate normalized features [0, 1]
        features = {
            "revenue_cagr_3y": 0.1 * (i % 10),
            "profit_cagr_3y": 0.05 * (i % 8),
            "roe_ttm": 0.8 - 0.03 * i,
            "cfo_to_net_profit_ttm": 0.6 + 0.02 * (i % 5),
            "rs_60d": 0.5 + 0.05 * (i % 4),
            "peg_ratio": 0.3 + 0.04 * (i % 6),
        }
        missing_critical = i >= 18  # last 2 are critical-missing
        results.append(FeatureResult(
            ticker=f"{600000 + i}",
            normalized_features=features if not missing_critical else {},
            missing_features=[] if not missing_critical else ["roe_ttm", "revenue_cagr_3y"],
            missing_critical=missing_critical,
        ))
    return results


def _make_snapshot_rows(n: int = 20) -> dict[str, dict]:
    """Create synthetic snapshot rows."""
    industries = ["基础化工", "医药生物", "电子", "机械设备", "电力设备"]
    rows = {}
    for i in range(n):
        ticker = f"{600000 + i}"
        rows[ticker] = {
            "ticker": ticker,
            "industry_anchor": industries[i % len(industries)],
            "industry_l1": industries[i % len(industries)],
            "board": "main",
            "size_bucket": "large",
            "risk_goodwill_high": i == 5,  # one penalty
        }
    return rows


@pytest.fixture
def growth_config():
    return load_strategy(
        Path("src/ai_investor/strategy/registry/growth_capture_12m_v1")
    )


# ---------------------------------------------------------------------------
# Test 1: score_with_weights ≡ score_universe for production weights
# ---------------------------------------------------------------------------

class TestOptimizationFidelity:
    """Prove that the optimization path produces identical results
    to the production scoring path."""

    def test_score_equivalence_with_production_weights(self, growth_config):
        """score_with_weights MUST produce identical scores and rankings
        as score_universe when given the same weights."""
        features = _make_feature_results()
        snapshot_rows = _make_snapshot_rows()

        # --- Production path ---
        prod_results = score_universe(
            features, snapshot_rows, growth_config,
            label_mode="percentile",
        )
        # Only scorable (not missing_critical)
        prod_scorable = [r for r in prod_results if r.rank is not None]

        # --- Optimization path ---
        fwd_map = {f"{600000 + i}": {} for i in range(20)}
        bm = {}
        precomputed = precompute_slice(
            features, snapshot_rows, growth_config,
            fwd_map, bm, "2025-01-02",
        )

        # Extract production weights from config
        prod_weights = {
            gname: gdef.weight
            for gname, gdef in growth_config.feature_pipeline.feature_groups.items()
        }
        opt_tickers, opt_scores, opt_industries = score_with_weights(precomputed, prod_weights)

        # --- Assert equivalence ---
        # Same number of scorable tickers
        assert len(opt_tickers) == len(prod_scorable), (
            f"Scorable count mismatch: opt={len(opt_tickers)} vs prod={len(prod_scorable)}"
        )

        # Same ranking order
        prod_ranking = [r.ticker for r in prod_scorable]
        assert opt_tickers == prod_ranking, (
            f"Ranking order mismatch:\n  opt={opt_tickers[:5]}\n  prod={prod_ranking[:5]}"
        )

        # Same scores (within float precision)
        for i, (prod_r, opt_s) in enumerate(zip(prod_scorable, opt_scores)):
            assert abs(prod_r.total_score - opt_s) < 0.01, (
                f"Score mismatch for {prod_r.ticker}: "
                f"prod={prod_r.total_score} vs opt={opt_s}"
            )

    def test_missing_critical_excluded(self, growth_config):
        """Missing-critical tickers must be excluded from both paths."""
        features = _make_feature_results()
        snapshot_rows = _make_snapshot_rows()

        # Production
        prod_results = score_universe(features, snapshot_rows, growth_config)
        prod_blocked = [r for r in prod_results if r.rank is None]

        # Optimization
        fwd_map = {f"{600000 + i}": {} for i in range(20)}
        precomputed = precompute_slice(
            features, snapshot_rows, growth_config,
            fwd_map, {}, "2025-01-02",
        )
        opt_tickers, _, _ = score_with_weights(precomputed, {"growth_acceleration": 40, "growth_quality": 35, "market_confirmation": 15, "valuation_constraint": 10})

        # Both should exclude the same tickers
        prod_blocked_tickers = {r.ticker for r in prod_blocked}
        opt_included = set(opt_tickers)
        assert prod_blocked_tickers.isdisjoint(opt_included), (
            f"Blocked tickers leaked into optimization: "
            f"{prod_blocked_tickers & opt_included}"
        )

    def test_score_varies_with_weights(self, growth_config):
        """Different weights MUST produce different rankings."""
        features = _make_feature_results()
        snapshot_rows = _make_snapshot_rows()
        fwd_map = {f"{600000 + i}": {} for i in range(20)}

        precomputed = precompute_slice(
            features, snapshot_rows, growth_config,
            fwd_map, {}, "2025-01-02",
        )

        # Weight config A: heavy on growth_acceleration
        weights_a = {"growth_acceleration": 60, "growth_quality": 20, "market_confirmation": 10, "valuation_constraint": 10}
        _, scores_a, _ = score_with_weights(precomputed, weights_a)

        # Weight config B: heavy on valuation_constraint
        weights_b = {"growth_acceleration": 10, "growth_quality": 20, "market_confirmation": 10, "valuation_constraint": 60}
        _, scores_b, _ = score_with_weights(precomputed, weights_b)

        # Scores should differ
        assert scores_a != scores_b, "Different weights produced identical scores"


# ---------------------------------------------------------------------------
# Test 2: Softmax weight constraints
# ---------------------------------------------------------------------------

class TestSoftmaxConstraints:
    """Verify softmax parameterization respects constraints."""

    def test_weights_sum_to_budget(self):
        logits = {"a": 1.0, "b": -0.5, "c": 0.3, "d": -0.8}
        weights = logits_to_weights(logits, total_budget=100.0)
        assert abs(sum(weights.values()) - 100.0) < 1.0  # within rounding

    def test_weights_respect_min_max(self):
        logits = {"a": 5.0, "b": -5.0, "c": 0.0, "d": 0.0}
        weights = logits_to_weights(logits, min_pct=5.0, max_pct=60.0)
        for w in weights.values():
            assert w >= 4.9, f"Weight {w} below minimum"  # small tolerance
            assert w <= 60.1, f"Weight {w} above maximum"

    def test_translational_invariance_removed(self):
        """Adding a constant to all logits should give same weights."""
        logits_a = {"x": 1.0, "y": 2.0, "z": 3.0}
        logits_b = {"x": 11.0, "y": 12.0, "z": 13.0}  # shift by 10
        weights_a = logits_to_weights(logits_a)
        weights_b = logits_to_weights(logits_b)
        for k in logits_a:
            assert abs(weights_a[k] - weights_b[k]) < 0.1, (
                f"Translational invariance not removed: {k}: "
                f"{weights_a[k]} vs {weights_b[k]}"
            )
