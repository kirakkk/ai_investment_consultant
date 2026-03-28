"""Golden output regression tests — run the CLI pipeline on frozen
snapshots and validate outputs match expected structure and invariants."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from ai_investor.features.engine import compute_features
from ai_investor.models.score_result import ScoreResult
from ai_investor.policy.checker import check_policy
from ai_investor.scoring.engine import score_universe
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
SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data" / "sample_snapshots" / "snapshot_20260324.parquet"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"


class TestGoldenPipelineOutputs:
    """Validate that CLI pipeline outputs produced from frozen snapshots
    satisfy structural invariants and state machine rules."""

    def test_output_files_exist(self) -> None:
        """All 20 sample tickers should have output files."""
        if not OUTPUT_DIR.exists():
            # Re-run pipeline if output dir missing
            self._run_pipeline()
        files = list(OUTPUT_DIR.glob("*.json"))
        assert len(files) == 20, f"Expected 20 outputs, got {len(files)}"

    def test_all_outputs_are_valid_score_results(self) -> None:
        """Every output JSON should deserialize into a valid ScoreResult."""
        if not OUTPUT_DIR.exists():
            self._run_pipeline()
        for f in sorted(OUTPUT_DIR.glob("*.json")):
            raw = json.loads(f.read_text(encoding="utf-8"))
            result = ScoreResult.model_validate(raw)
            assert result.strategy_id == "ashare_quality_cashflow_regime_v1"

    def test_state_distribution(self) -> None:
        """Verify expected state distribution from the 20-stock sample."""
        if not OUTPUT_DIR.exists():
            self._run_pipeline()
        states = {"ok": 0, "degraded": 0, "blocked": 0}
        for f in OUTPUT_DIR.glob("*.json"):
            raw = json.loads(f.read_text(encoding="utf-8"))
            states[raw["result_state"]] += 1

        assert states["ok"] >= 10, f"Expected >=10 ok, got {states['ok']}"
        assert states["degraded"] >= 1, f"Expected >=1 degraded, got {states['degraded']}"
        assert states["blocked"] >= 3, f"Expected >=3 blocked, got {states['blocked']}"
        assert sum(states.values()) == 20

    def test_blocked_tickers_are_expected(self) -> None:
        """Known blocked tickers from our sample data."""
        if not OUTPUT_DIR.exists():
            self._run_pipeline()
        expected_blocked = {"301505", "510050", "600978", "600985", "600900"}
        actual_blocked = set()
        for f in OUTPUT_DIR.glob("*.json"):
            raw = json.loads(f.read_text(encoding="utf-8"))
            if raw["result_state"] == "blocked":
                actual_blocked.add(raw["security"]["ticker"])

        assert actual_blocked == expected_blocked, (
            f"Expected blocked: {expected_blocked}, got: {actual_blocked}"
        )

    def test_ranking_is_valid(self) -> None:
        """All ok/degraded results should have unique ranks within [1, N_scored].

        Note: ranks may have gaps because tickers that pass universe building
        but get blocked at policy check (e.g. missing critical data) have
        their ranks nulled out, leaving holes in the sequence.
        """
        if not OUTPUT_DIR.exists():
            self._run_pipeline()
        ranks = []
        for f in OUTPUT_DIR.glob("*.json"):
            raw = json.loads(f.read_text(encoding="utf-8"))
            if raw["result_state"] != "blocked":
                assert raw["rank"] is not None
                ranks.append(raw["rank"])

        # Ranks should be unique
        assert len(ranks) == len(set(ranks)), f"Duplicate ranks: {ranks}"
        # All ranks should be >= 1
        assert all(r >= 1 for r in ranks), f"Ranks < 1 found: {ranks}"
        # Should have at least 10 ranked results
        assert len(ranks) >= 10

    def test_audit_has_applied_reason_codes(self) -> None:
        """Every output should have at least one applied reason code in audit."""
        if not OUTPUT_DIR.exists():
            self._run_pipeline()
        for f in OUTPUT_DIR.glob("*.json"):
            raw = json.loads(f.read_text(encoding="utf-8"))
            codes = raw["audit"]["applied_reason_codes"]
            assert len(codes) > 0, f"{raw['security']['ticker']}: no applied_reason_codes"

    def _run_pipeline(self) -> None:
        """Helper to re-run the pipeline if outputs are missing."""
        import subprocess
        subprocess.run(
            ["uv", "run", "ai-investor",
             "--strategy-dir", str(STRATEGY_DIR),
             "--snapshot", str(SNAPSHOT_PATH),
             "--output", str(OUTPUT_DIR)],
            cwd=str(Path(__file__).resolve().parents[2]),
            check=True,
        )


class TestPipelineInternals:
    """Test pipeline stages directly on frozen snapshot data."""

    def setup_method(self) -> None:
        self.config = load_strategy(STRATEGY_DIR)
        self.df = pl.read_parquet(SNAPSHOT_PATH)

    def test_universe_split(self) -> None:
        memberships = build_universe(self.df, self.config)
        included = [m for m in memberships if m.included]
        excluded = [m for m in memberships if not m.included]
        assert len(included) + len(excluded) == 20
        assert len(included) >= 12
        assert len(excluded) >= 3

    def test_feature_computation(self) -> None:
        memberships = build_universe(self.df, self.config)
        included_tickers = {m.ticker for m in memberships if m.included}
        features = compute_features(self.df, self.config, included_tickers)
        assert len(features) == len(included_tickers)

    def test_scoring_produces_ranks(self) -> None:
        memberships = build_universe(self.df, self.config)
        included_tickers = {m.ticker for m in memberships if m.included}
        features = compute_features(self.df, self.config, included_tickers)
        rows = {str(r["ticker"]): dict(r) for r in self.df.iter_rows(named=True)}
        scores = score_universe(features, rows, self.config)
        assert len(scores) == len(included_tickers)
        assert scores[0].rank == 1
