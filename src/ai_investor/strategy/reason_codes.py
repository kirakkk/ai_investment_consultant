"""Reason code registry — load and query universe_reason_codes.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ReasonCodeInfo(BaseModel):
    """A single reason code entry from the registry."""

    code: str
    category: str
    severity: str | None = None
    display_name: str
    description: str
    status: str = "active"
    introduced_in: str | None = None
    deprecated_in: str | None = None
    replaced_by: str | None = None

    model_config = {"extra": "allow"}


class ReasonCodeRegistry:
    """In-memory registry of reason codes loaded from YAML."""

    def __init__(self, codes: dict[str, ReasonCodeInfo]) -> None:
        self._codes = codes

    @classmethod
    def from_yaml(cls, path: Path) -> "ReasonCodeRegistry":
        """Load reason codes from a YAML file.

        The YAML is expected to have top-level category keys
        (e.g. inclusion_reasons, exclusion_reasons) each containing
        a list of code definitions.
        """
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        codes: dict[str, ReasonCodeInfo] = {}
        if isinstance(raw, dict):
            for category_key, entries in raw.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict) and "code" in entry:
                        info = ReasonCodeInfo(
                            code=entry["code"],
                            category=category_key,
                            severity=entry.get("severity"),
                            display_name=entry.get("display_name", entry["code"]),
                            description=entry.get("description", ""),
                            status=entry.get("status", "active"),
                            introduced_in=entry.get("introduced_in"),
                            deprecated_in=entry.get("deprecated_in"),
                            replaced_by=entry.get("replaced_by"),
                        )
                        codes[info.code] = info

        return cls(codes)

    def validate_code(self, code: str) -> bool:
        """Check if a code exists and is active."""
        info = self._codes.get(code)
        return info is not None and info.status == "active"

    def get_code_info(self, code: str) -> ReasonCodeInfo | None:
        """Return info for a code, or None if not found."""
        return self._codes.get(code)

    def get_codes_by_category(self, category: str) -> list[ReasonCodeInfo]:
        """Return all codes in a given category."""
        return [c for c in self._codes.values() if c.category == category]

    @property
    def all_codes(self) -> dict[str, ReasonCodeInfo]:
        return dict(self._codes)
