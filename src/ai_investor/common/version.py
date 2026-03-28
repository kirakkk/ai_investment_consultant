"""Version chain generation utilities."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def make_run_id(prefix: str = "run") -> str:
    """Generate a run_id like 'run-20260324-postclose-001'."""
    now = datetime.now(tz=timezone.utc)
    return f"{prefix}-{now.strftime('%Y%m%d-%H%M%S')}"


def compute_source_hash(data: bytes) -> str:
    """Compute a sha256 hash for a data blob."""
    return f"sha256:{hashlib.sha256(data).hexdigest()[:12]}"
