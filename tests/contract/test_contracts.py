"""Contract tests — validate that Pydantic models align with JSON Schema
and that sample outputs pass deserialization + schema validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_investor.models.score_result import ScoreResult

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "data" / "golden_results"
SCHEMA_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ai_investor"
    / "strategy"
    / "registry"
    / "ashare_quality_cashflow_regime_v1"
)


# --------------------------------------------------------------------------
# 1. Sample JSON files can be loaded into ScoreResult model
# --------------------------------------------------------------------------

class TestSampleDeserialization:
    """Verify all golden sample JSONs deserialize into ScoreResult."""

    @pytest.fixture(params=["ok", "degraded", "blocked"])
    def sample_path(self, request: pytest.FixtureRequest) -> Path:
        return GOLDEN_DIR / f"sample_score_result.{request.param}.json"

    def test_sample_round_trips(self, sample_path: Path) -> None:
        """Load sample JSON → ScoreResult → re-serialize → compare keys."""
        raw = json.loads(sample_path.read_text(encoding="utf-8"))
        result = ScoreResult.model_validate(raw)

        # Re-serialize and check top-level keys match
        exported = result.model_dump(mode="json")
        assert set(exported.keys()) == set(raw.keys()), (
            f"Key mismatch between original and re-serialized: "
            f"extra={set(exported.keys()) - set(raw.keys())}, "
            f"missing={set(raw.keys()) - set(exported.keys())}"
        )

    def test_ok_sample_has_score(self) -> None:
        raw = json.loads((GOLDEN_DIR / "sample_score_result.ok.json").read_text(encoding="utf-8"))
        result = ScoreResult.model_validate(raw)
        assert result.result_state.value == "ok"
        assert result.total_score is not None
        assert result.rank is not None
        assert result.state_reason_codes == []

    def test_blocked_sample_has_null_score(self) -> None:
        raw = json.loads((GOLDEN_DIR / "sample_score_result.blocked.json").read_text(encoding="utf-8"))
        result = ScoreResult.model_validate(raw)
        assert result.result_state.value == "blocked"
        assert result.total_score is None
        assert result.rank is None
        assert len(result.state_reason_codes) > 0

    def test_degraded_sample_has_reasons(self) -> None:
        raw = json.loads((GOLDEN_DIR / "sample_score_result.degraded.json").read_text(encoding="utf-8"))
        result = ScoreResult.model_validate(raw)
        assert result.result_state.value == "degraded"
        assert len(result.state_reason_codes) > 0
        assert result.total_score is not None


# --------------------------------------------------------------------------
# 2. Pydantic model required fields align with strategy.yaml
# --------------------------------------------------------------------------

class TestRequiredFieldsAlignment:
    """Verify required fields in schema.json and strategy.yaml match."""

    def test_schema_required_matches_strategy(self) -> None:
        import yaml

        schema = json.loads((SCHEMA_DIR / "score_result.schema.json").read_text(encoding="utf-8"))
        strategy = yaml.safe_load((SCHEMA_DIR / "strategy.yaml").read_text(encoding="utf-8"))

        schema_required = set(schema["required"])
        strategy_required = set(strategy["output_contract"]["required_fields"])

        assert schema_required == strategy_required, (
            f"Mismatch:\n"
            f"  In schema only: {schema_required - strategy_required}\n"
            f"  In strategy only: {strategy_required - schema_required}"
        )

    def test_pydantic_covers_schema_required(self) -> None:
        """All fields in schema required list exist on ScoreResult model."""
        schema = json.loads((SCHEMA_DIR / "score_result.schema.json").read_text(encoding="utf-8"))
        schema_required = set(schema["required"])
        model_fields = set(ScoreResult.model_fields.keys())

        missing = schema_required - model_fields
        assert not missing, f"ScoreResult is missing required fields: {missing}"


# --------------------------------------------------------------------------
# 3. JSON Schema self-validity
# --------------------------------------------------------------------------

class TestSchemaValidity:
    """Verify the JSON Schema file is valid."""

    def test_schema_is_valid_json(self) -> None:
        schema_path = SCHEMA_DIR / "score_result.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert "$schema" in schema
        assert "required" in schema
        assert "properties" in schema

    def test_strategy_yaml_is_valid(self) -> None:
        import yaml

        strategy_path = SCHEMA_DIR / "strategy.yaml"
        config = yaml.safe_load(strategy_path.read_text(encoding="utf-8"))
        assert config["strategy_id"] == "ashare_quality_cashflow_regime_v1"
        assert "feature_pipeline" in config
        assert "output_contract" in config
