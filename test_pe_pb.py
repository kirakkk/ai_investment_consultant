"""Test PE/PB and percentile calculations."""
import polars as pl
from ai_investor.connectors.akshare_connector import AKShareConnector
from ai_investor.connectors.snapshot_builder import build_snapshot

connector = AKShareConnector()
# We will test on 20 stocks
df = build_snapshot(connector, max_tickers=20)

print("\n=== Snapshot Result ===")
print("Columns:", df.columns)

print("\n=== Valuation Percentiles ===")
cols_to_show = ["ticker", "name", "industry_l1", "pe_pctile_in_industry", "pb_pctile_in_industry"]
print(df.select(cols_to_show))

# Also check if raw columns pe_ttm_raw, pb_raw still exist (they should be dropped by _compute_cross_sectional_metrics)
print("\nRaw columns exist:", [c for c in ["pe_ttm_raw", "pb_raw", "eps_ttm", "bps"] if c in df.columns])
