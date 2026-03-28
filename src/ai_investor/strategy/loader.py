"""Strategy loader — load and validate strategy.yaml and associated files."""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_investor.models.strategy import StrategyConfig


def load_strategy(strategy_dir: Path) -> StrategyConfig:
    """Load a strategy from a registry directory.

    Args:
        strategy_dir: Path to a strategy directory containing strategy.yaml.

    Returns:
        Validated StrategyConfig instance.

    Raises:
        FileNotFoundError: If strategy.yaml is missing.
        pydantic.ValidationError: If the YAML content fails validation.
    """
    strategy_path = strategy_dir / "strategy.yaml"
    if not strategy_path.exists():
        raise FileNotFoundError(f"strategy.yaml not found in {strategy_dir}")

    with open(strategy_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # V2: Merge shared_rules from strategy_family.yaml if exists
    family_path = strategy_dir.parent / "strategy_family.yaml"
    if family_path.exists():
        with open(family_path, encoding="utf-8") as f:
            family_raw = yaml.safe_load(f) or {}
            shared = family_raw.get("shared_rules", {})

            # Keys that belong inside label_mapping (not top-level)
            _LABEL_MAPPING_KEYS = {"label_mode", "label_percentiles"}

            for k, v in shared.items():
                if k in _LABEL_MAPPING_KEYS:
                    # Inject into the nested label_mapping dict
                    lm = raw.setdefault("label_mapping", {})
                    if k not in lm:
                        lm[k] = v
                elif k not in raw:
                    raw[k] = v

    return StrategyConfig.model_validate(raw)


def discover_strategies(registry_root: Path) -> dict[str, Path]:
    """Discover all strategy directories under a registry root.

    Returns:
        dict mapping strategy_id → directory path.
    """
    strategies: dict[str, Path] = {}
    if not registry_root.is_dir():
        return strategies

    for child in registry_root.iterdir():
        if child.is_dir() and (child / "strategy.yaml").exists():
            try:
                config = load_strategy(child)
                strategies[config.strategy_id] = child
            except Exception:  # noqa: BLE001
                # Skip invalid strategies during discovery
                continue

    return strategies
