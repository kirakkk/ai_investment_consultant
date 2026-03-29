"""Dataset manifest and coverage verification.

Provides immutable, hashable dataset manifests for backtest reproducibility.
Each manifest records file paths, SHA256 hashes, coverage statistics, and
the AsOfContext used to generate the data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ai_investor.backtest.conventions import COVERAGE_GATES, AsOfContext

logger = logging.getLogger(__name__)


@dataclass
class FileSummary:
    """Summary of a single data file in the manifest."""
    path: str
    sha256: str
    rows: int
    columns: int
    size_bytes: int


@dataclass
class CoverageReport:
    """Coverage statistics for key fields."""
    field_name: str
    total_rows: int
    non_null_rows: int
    coverage_pct: float
    gate_threshold: float
    passed: bool


@dataclass
class DatasetManifest:
    """Immutable record of a backtest dataset."""
    created_at: str
    asof_context: dict
    files: list[FileSummary] = field(default_factory=list)
    coverage: list[CoverageReport] = field(default_factory=list)
    all_gates_passed: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")
        logger.info(f"Manifest saved to {path}")

    @staticmethod
    def load(path: Path) -> DatasetManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        files = [FileSummary(**f) for f in data.get("files", [])]
        coverage = [CoverageReport(**c) for c in data.get("coverage", [])]
        return DatasetManifest(
            created_at=data["created_at"],
            asof_context=data["asof_context"],
            files=files,
            coverage=coverage,
            all_gates_passed=data.get("all_gates_passed", False),
        )


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    data_dir: Path,
    asof: AsOfContext,
    snapshot_path: Path | None = None,
) -> DatasetManifest:
    """Build a manifest for all parquet files in data_dir.

    If snapshot_path is provided, also run coverage gates on it.
    """
    import polars as pl

    files: list[FileSummary] = []

    # Catalog all parquet files
    for p in sorted(data_dir.glob("*.parquet")):
        df = pl.read_parquet(p)
        files.append(FileSummary(
            path=str(p.name),
            sha256=_sha256_file(p),
            rows=df.height,
            columns=df.width,
            size_bytes=p.stat().st_size,
        ))

    # Coverage gates (if snapshot available)
    coverage: list[CoverageReport] = []
    all_passed = True

    if snapshot_path and snapshot_path.exists():
        df = pl.read_parquet(snapshot_path)
        total = df.height

        for field_name, threshold in COVERAGE_GATES.items():
            if field_name in df.columns:
                non_null = total - df[field_name].null_count()
            else:
                non_null = 0

            pct = non_null / total if total > 0 else 0.0
            passed = pct >= threshold

            if not passed:
                all_passed = False
                logger.warning(
                    f"Coverage gate FAILED: {field_name} = {pct:.1%} "
                    f"(threshold: {threshold:.0%})"
                )
            else:
                logger.info(
                    f"Coverage gate passed: {field_name} = {pct:.1%} "
                    f"(threshold: {threshold:.0%})"
                )

            coverage.append(CoverageReport(
                field_name=field_name,
                total_rows=total,
                non_null_rows=non_null,
                coverage_pct=round(pct, 4),
                gate_threshold=threshold,
                passed=passed,
            ))

    manifest = DatasetManifest(
        created_at=datetime.now().isoformat(),
        asof_context=asof.to_dict(),
        files=files,
        coverage=coverage,
        all_gates_passed=all_passed,
    )

    return manifest


def verify_manifest(data_dir: Path, manifest_path: Path) -> bool:
    """Verify that files in data_dir match the manifest hashes.

    Returns True if all files match, False otherwise.
    """
    manifest = DatasetManifest.load(manifest_path)
    all_ok = True

    for f in manifest.files:
        p = data_dir / f.path
        if not p.exists():
            logger.error(f"Manifest verify: MISSING {f.path}")
            all_ok = False
            continue

        actual_hash = _sha256_file(p)
        if actual_hash != f.sha256:
            logger.error(
                f"Manifest verify: HASH MISMATCH {f.path} "
                f"(expected {f.sha256[:12]}..., got {actual_hash[:12]}...)"
            )
            all_ok = False
        else:
            logger.info(f"Manifest verify: OK {f.path}")

    if all_ok:
        logger.info("Manifest verification: ALL PASSED")
    else:
        logger.error("Manifest verification: FAILED")

    return all_ok
