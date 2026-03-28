"""Common type aliases used across the strategy kernel."""

from __future__ import annotations

from typing import TypeAlias

# Ticker → feature_id → normalized_value
FeatureVector: TypeAlias = dict[str, dict[str, float]]

# Ticker string, e.g. "600519"
Ticker: TypeAlias = str

# Reason code string, e.g. "INC_FULL_MARKET_SEED"
ReasonCode: TypeAlias = str
