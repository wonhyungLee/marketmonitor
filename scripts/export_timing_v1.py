#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Allow running as `python scripts/export_timing_v1.py` without PYTHONPATH.
sys.path.insert(0, str(BASE_DIR))

from app.timing_v1 import export_timing_v1_daily

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None, help="Project data directory (default: ./data)")
    ap.add_argument("--in-name", default="fear_euphoria_daily.csv")
    ap.add_argument("--out-name", default="timing_v1_daily.csv")
    args = ap.parse_args()
    data_dir = Path(args.data_dir) if args.data_dir else None
    out = export_timing_v1_daily(data_dir=data_dir, in_name=args.in_name, out_name=args.out_name)
    if out is None:
        raise SystemExit("FAILED: input fear_euphoria_daily.csv not found or output empty")
    print(f"OK: wrote {out}")
