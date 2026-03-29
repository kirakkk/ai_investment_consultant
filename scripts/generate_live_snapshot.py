import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# Add src to Python path
sys.path.insert(0, os.path.abspath('src'))

from ai_investor.connectors.akshare_connector import AKShareConnector
from ai_investor.connectors.snapshot_builder import build_snapshot

logging.basicConfig(level=logging.INFO, format='%(message)s')

def main() -> None:
    print("\U0001f310 Live mode: pulling data from AKShare (Low Rate Limit)....")
    
    # We will use the patched AKShareConnector. 
    # It already has max_workers=4 and _THROTTLE_SECONDS=1.0 with random jitter.
    # We pass throttle=0.5 to slow down the single-threaded parts further just to be safe.
    connector = AKShareConnector(throttle=0.5)
    
    asof_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    snapshot_path = Path(f"data/live_snapshots/snapshot_{asof_str}_safe.parquet")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    
    df = build_snapshot(
        connector,
        max_tickers=None,
        save_path=snapshot_path,
    )
    
    print(f"\n\U0001f4be Snapshot saved to {snapshot_path}")
    print(f"   Collected rows: {df.height}")

if __name__ == "__main__":
    main()
