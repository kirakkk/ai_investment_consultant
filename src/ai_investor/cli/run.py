"""CLI entry point — orchestrate the full strategy kernel pipeline.

Usage:
    ai-investor --strategy-dir <path> --snapshot <path> [--output <dir>]
    ai-investor --strategy-dir <path> --live [--max-tickers N] [--output <dir>]
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import click
import polars as pl

from ai_investor.common.version import make_run_id
from ai_investor.features.engine import FeatureResult, compute_features
from ai_investor.models.score_result import (
    Audit,
    DataQuality,
    ScoreResult,
    Security,
    UniverseMembership,
)
from ai_investor.policy.checker import check_policy
from ai_investor.scoring.engine import build_sub_scores_model, score_universe
from ai_investor.scoring.evidence import (
    assemble_drivers,
    assemble_invalidation,
    assemble_risks,
    assemble_triggers,
)
from ai_investor.strategy.loader import load_strategy
from ai_investor.universe.builder import build_universe


def _snapshot_row_to_security(row: dict[str, object]) -> Security:
    """Extract Security model from a snapshot row."""
    return Security(
        ticker=str(row["ticker"]),
        exchange=str(row.get("exchange", "SSE")),
        name=str(row.get("name", "")),
        board=str(row.get("board", "main")),
        security_type=str(row.get("security_type", "common_stock")),
        industry_l1=str(row.get("industry_l1", "")),
        industry_l2=row.get("industry_l2") if row.get("industry_l2") else None,
    )


def _build_score_result(
    ticker: str,
    membership_decision: object,
    feature_result: FeatureResult | None,
    scoring_result: object | None,
    policy_decision: object,
    snapshot_row: dict[str, object],
    config: object,
    run_id: str,
    request_id: str,
    asof: datetime,
) -> ScoreResult:
    """Assemble a complete ScoreResult from all pipeline outputs."""
    from ai_investor.models.strategy import StrategyConfig
    from ai_investor.policy.checker import PolicyDecision
    from ai_investor.scoring.engine import ScoringResult
    from ai_investor.universe.builder import MembershipDecision

    assert isinstance(membership_decision, MembershipDecision)
    assert isinstance(policy_decision, PolicyDecision)
    assert isinstance(config, StrategyConfig)

    security = _snapshot_row_to_security(snapshot_row)

    universe_membership = UniverseMembership(
        included=membership_decision.included,
        inclusion_reasons=membership_decision.inclusion_reasons,
        exclusion_reasons=membership_decision.exclusion_reasons,
    )

    # Collect all applied reason codes for audit
    all_applied_codes: list[str] = list(membership_decision.inclusion_reasons)
    all_applied_codes.extend(membership_decision.exclusion_reasons)
    all_applied_codes.extend(policy_decision.state_reason_codes)

    if policy_decision.result_state == "blocked":
        # Blocked: null scores, blocked label
        total_score = None
        rank = None
        percentile_rank = None
        sub_scores = build_sub_scores_model({}, 0.0)
        penalties_list = []
        label = config.label_mapping.blocked_label
        drivers = []
        risks = []
        confidence = 0.0
    else:
        assert isinstance(scoring_result, ScoringResult)
        assert feature_result is not None
        total_score = scoring_result.total_score
        rank = scoring_result.rank
        percentile_rank = scoring_result.percentile_rank
        sub_scores = build_sub_scores_model(
            scoring_result.sub_scores, scoring_result.risk_penalty_total
        )
        penalties_list = scoring_result.penalties
        label = scoring_result.label
        confidence = policy_decision.confidence
        drivers = assemble_drivers(feature_result, scoring_result, config, snapshot_row)
        risks = assemble_risks(scoring_result, snapshot_row)

        # Add penalty codes to applied_reason_codes
        for p in penalties_list:
            all_applied_codes.append(p.code)

    triggers = assemble_triggers()
    invalidation = assemble_invalidation()

    data_quality = DataQuality(
        critical_complete=policy_decision.critical_complete,
        missing_features=policy_decision.missing_features,
        stale_sources=policy_decision.stale_sources,
        degraded_reasons=policy_decision.degraded_reasons,
    )

    audit = Audit(
        render_tier="R0",
        disclaimer_id=config.output_contract.disclaimer_id,
        policy_passed=policy_decision.result_state != "blocked",
        banned_terms_checked=True,
        source_hashes=[],  # TODO: populate from actual source hashes in MVP1
        applied_reason_codes=all_applied_codes,
    )

    return ScoreResult(
        request_id=request_id,
        run_id=run_id,
        asof=asof,
        strategy_id=config.strategy_id,
        strategy_version=config.strategy_version,
        universe_version=f"uv-{asof.strftime('%Y%m%d')}-001",
        data_version=f"dv-{asof.strftime('%Y%m%d')}-001",
        feature_version=f"fv-{asof.strftime('%Y%m%d')}-001",
        policy_version=f"pv-{asof.strftime('%Y%m%d')}-001",
        security=security,
        universe_membership=universe_membership,
        result_state=policy_decision.result_state,
        state_reason_codes=policy_decision.state_reason_codes,
        total_score=total_score,
        rank=rank,
        percentile_rank=percentile_rank,
        sub_scores=sub_scores,
        penalties=penalties_list,
        label=label,
        confidence=confidence,
        drivers=drivers,
        risks=risks,
        triggers=triggers,
        invalidation=invalidation,
        data_quality=data_quality,
        audit=audit,
    )


@click.command()
@click.option(
    "--strategy-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help="Path to strategy directory containing strategy.yaml.",
)
@click.option(
    "--snapshot",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to PIT snapshot file (Parquet or CSV). Required if --live is not set.",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Pull live data from AKShare instead of loading a snapshot file.",
)
@click.option(
    "--max-tickers",
    type=int,
    default=None,
    help="Limit number of tickers in --live mode (for testing).",
)
@click.option(
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("output"),
    help="Output directory for score results.",
)
def main(
    strategy_dir: Path,
    snapshot: Path | None,
    live: bool,
    max_tickers: int | None,
    output: Path,
) -> None:
    """Run the strategy kernel pipeline end-to-end."""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    click.echo(f"\U0001f4cb Loading strategy from {strategy_dir}")
    config = load_strategy(strategy_dir)
    click.echo(f"   Strategy: {config.display_name} ({config.strategy_id} v{config.strategy_version})")

    if live:
        # MVP1: Pull live data from AKShare
        from ai_investor.connectors.akshare_connector import AKShareConnector
        from ai_investor.connectors.snapshot_builder import build_snapshot

        click.echo("\U0001f310 Live mode: pulling data from AKShare...")
        connector = AKShareConnector(throttle=0.3)
        asof_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        snapshot_path = Path(f"data/live_snapshots/snapshot_{asof_str}.parquet")
        df = build_snapshot(
            connector,
            max_tickers=max_tickers,
            save_path=snapshot_path,
        )
        click.echo(f"\U0001f4be Snapshot saved to {snapshot_path}")
    elif snapshot is not None:
        click.echo(f"\U0001f4ca Loading snapshot from {snapshot}")
        if snapshot.suffix == ".parquet":
            df = pl.read_parquet(snapshot)
        else:
            df = pl.read_csv(snapshot)
    else:
        raise click.UsageError("Either --snapshot or --live is required.")

    click.echo(f"   Loaded {df.height} rows \u00d7 {df.width} columns")

    # --- Pipeline ---
    run_id = make_run_id()
    request_id = f"req-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    asof = datetime.now(tz=timezone.utc)

    # Step 1: Universe Builder
    click.echo("🔍 Building universe...")
    memberships = build_universe(df, config)
    included = [m for m in memberships if m.included]
    excluded = [m for m in memberships if not m.included]
    click.echo(f"   Included: {len(included)}, Excluded: {len(excluded)}")

    # Step 2: Feature Engine
    click.echo("⚙️  Computing features...")
    included_tickers = {m.ticker for m in included}
    feature_results = compute_features(df, config, included_tickers)
    click.echo(f"   Computed features for {len(feature_results)} tickers")

    # Step 3: Scoring Engine
    click.echo("📈 Scoring universe...")
    snapshot_rows: dict[str, dict[str, object]] = {}
    for row in df.iter_rows(named=True):
        snapshot_rows[str(row["ticker"])] = dict(row)
    scoring_results = score_universe(feature_results, snapshot_rows, config)
    click.echo(f"   Scored {len(scoring_results)} tickers")

    # Build lookup maps
    membership_map = {m.ticker: m for m in memberships}
    feature_map = {fr.ticker: fr for fr in feature_results}
    scoring_map = {sr.ticker: sr for sr in scoring_results}

    # Step 4: Policy + Assembly → ScoreResult
    click.echo("📝 Assembling results...")
    output.mkdir(parents=True, exist_ok=True)
    result_count = 0

    for membership in memberships:
        ticker = membership.ticker
        fr = feature_map.get(ticker)
        sr = scoring_map.get(ticker)

        policy = check_policy(membership, fr, config)

        row = snapshot_rows.get(ticker, {})
        result = _build_score_result(
            ticker=ticker,
            membership_decision=membership,
            feature_result=fr,
            scoring_result=sr,
            policy_decision=policy,
            snapshot_row=row,
            config=config,
            run_id=run_id,
            request_id=request_id,
            asof=asof,
        )

        # Write individual result
        out_file = output / f"{ticker}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        result_count += 1

    click.echo(f"✅ Done! Wrote {result_count} results to {output}/")


if __name__ == "__main__":
    main()
