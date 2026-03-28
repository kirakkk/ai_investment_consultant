"""Feature Engine — compute and normalize feature vectors from PIT data.

Takes raw PIT snapshot data and strategy feature_groups definitions,
performs peer-group normalization (winsorize + z-score), and outputs
normalized feature vectors for the scoring engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from ai_investor.models.strategy import StrategyConfig


@dataclass
class FeatureResult:
    """Feature computation result for a single ticker."""

    ticker: str
    normalized_features: dict[str, float] = field(default_factory=dict)
    missing_features: list[str] = field(default_factory=list)
    missing_critical: bool = False


def _winsorize_series(s: pl.Series, lower_pct: float, upper_pct: float) -> pl.Series:
    """Winsorize a numeric series at given percentile boundaries."""
    if s.len() == 0 or s.null_count() == s.len():
        return s
    lower_val = s.drop_nulls().quantile(lower_pct, interpolation="linear")
    upper_val = s.drop_nulls().quantile(upper_pct, interpolation="linear")
    if lower_val is None or upper_val is None:
        return s
    return s.clip(lower_val, upper_val)


def _zscore_series(s: pl.Series, clip: float) -> pl.Series:
    """Compute z-scores and clip to [-clip, +clip], then map to [0, 1]."""
    mean = s.drop_nulls().mean()
    std = s.drop_nulls().std()
    if mean is None or std is None or std == 0:
        # If no variance, return 0.5 for all non-null
        return s.map_elements(lambda x: 0.5 if x is not None else None, return_dtype=pl.Float64)
    z = (s - mean) / std
    z_clipped = z.clip(-clip, clip)
    # Map from [-clip, +clip] to [0, 1]
    return (z_clipped + clip) / (2 * clip)


def compute_features(
    snapshot: pl.DataFrame,
    config: StrategyConfig,
    included_tickers: set[str],
) -> list[FeatureResult]:
    """Compute normalized features for all included tickers.

    Expected snapshot columns include all metric IDs defined in
    strategy.yaml feature_groups, plus peer_group columns (board, industry_l1).

    Args:
        snapshot: PIT snapshot DataFrame.
        config: Loaded strategy configuration.
        included_tickers: Set of tickers that passed universe filtering.

    Returns:
        List of FeatureResult, one per included ticker.
    """
    norm_cfg = config.feature_pipeline.normalization
    lower_pct, upper_pct = norm_cfg.winsorize_pct
    zscore_clip = norm_cfg.zscore_clip
    peer_group_cols_config = norm_cfg.peer_group

    # Collect all metric IDs and their criticality
    all_metrics: dict[str, bool] = {}  # metric_id → is_critical
    for fg in config.feature_pipeline.feature_groups.values():
        for m in fg.metrics:
            all_metrics[m.id] = m.critical

    # Filter to included tickers only
    included_df = snapshot.filter(pl.col("ticker").is_in(list(included_tickers)))

    if included_df.height == 0:
        return []

    # Gracefully handle missing peer_group columns — fall back to available ones
    available_cols = set(included_df.columns)
    peer_group_cols = [c for c in peer_group_cols_config if c in available_cols]
    if not peer_group_cols:
        # No peer group columns available → normalize across entire universe
        peer_group_cols = []
    if len(peer_group_cols) < len(peer_group_cols_config):
        import logging
        missing_pg = set(peer_group_cols_config) - set(peer_group_cols)
        logging.getLogger(__name__).warning(
            f"Peer group columns missing from snapshot, falling back: "
            f"configured={peer_group_cols_config}, using={peer_group_cols}, "
            f"missing={missing_pg}"
        )

    # Normalize each metric within peer groups
    normalized_data: dict[str, dict[str, float | None]] = {}  # metric_id → {ticker: value}

    for metric_id in all_metrics:
        normalized_data[metric_id] = {}

        if metric_id not in included_df.columns:
            # Metric column missing entirely
            for ticker in included_tickers:
                normalized_data[metric_id][ticker] = None
            continue

        # Normalize within each peer group (or whole universe if no peer groups)
        if peer_group_cols:
            groups = included_df.group_by(peer_group_cols)
        else:
            # Single group = entire universe
            groups = [("__all__", included_df)]
        for _group_name, group_df in groups:
            if group_df.height == 0:
                continue

            series = group_df[metric_id].cast(pl.Float64, strict=False)

            # Winsorize → z-score → [0, 1]
            winsorized = _winsorize_series(series, lower_pct, upper_pct)
            normalized = _zscore_series(winsorized, zscore_clip)

            tickers = group_df["ticker"].to_list()
            values = normalized.to_list()
            for t, v in zip(tickers, values):
                normalized_data[metric_id][str(t)] = v

    # Assemble per-ticker results
    results: list[FeatureResult] = []
    for ticker in sorted(included_tickers):
        features: dict[str, float] = {}
        missing: list[str] = []
        has_critical_missing = False

        for metric_id, is_critical in all_metrics.items():
            val = normalized_data.get(metric_id, {}).get(ticker)
            if val is not None:
                features[metric_id] = float(val)
            else:
                missing.append(metric_id)
                if is_critical:
                    has_critical_missing = True

        results.append(FeatureResult(
            ticker=ticker,
            normalized_features=features,
            missing_features=missing,
            missing_critical=has_critical_missing,
        ))

    return results
