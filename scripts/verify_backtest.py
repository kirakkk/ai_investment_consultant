import json, polars as pl
from pathlib import Path

d = Path('data/backtest/2024-12-31')

# Manifest
m = json.load(open(d / 'manifest.json', encoding='utf-8'))
print('=== Manifest ===')
print('Created:', m['created_at'])
print('As-of:', json.dumps(m['asof_context'], indent=2))
for f in m['files']:
    print(f"  {f['path']}: {f['rows']} rows, sha256={f['sha256'][:16]}...")

# Forward returns
df = pl.read_parquet(d / 'forward_returns.parquet')
print()
print('=== Forward Returns (sample) ===')
print(df.select(['ticker', 'fwd_1m', 'fwd_3m', 'fwd_6m', 'fwd_1y', '_status']).head(10).to_pandas().to_string())

# Benchmark
bm = json.load(open(d / 'benchmark_returns.json'))
print()
print('=== Benchmark (000985) ===')
for k, v in bm.items():
    print(f'  {k}: {v}')
