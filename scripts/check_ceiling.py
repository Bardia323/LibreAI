#!/usr/bin/env python3
"""
Decide whether the index has grown big enough to need IPFS failover.

git refuses to store a file larger than 100 MB. data/models.json is the source
of truth and the file that hits that wall first, so we gate on its size with a
safety margin (default 80 MB ~ 90k models) — crossing the margin means publish
to IPFS *now*, while git pushes still work, rather than after they start failing.

Below the threshold this prints "under" and the IPFS workflow does nothing.
Emits to $GITHUB_OUTPUT: over (true/false), models_bytes, data_bytes, models.

Usage: python scripts/check_ceiling.py --threshold 80000000
"""

import argparse
import json
import os
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"


def dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, required=True,
                    help="bytes; trigger IPFS when models.json reaches this")
    args = ap.parse_args()

    mj = DATA / "models.json"
    models_bytes = mj.stat().st_size if mj.exists() else 0
    data_bytes = dir_bytes(DATA) if DATA.exists() else 0
    models = 0
    if mj.exists():
        models = len(json.loads(mj.read_text(encoding="utf-8")).get("models", []))

    over = models_bytes >= args.threshold

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"over={'true' if over else 'false'}\n")
            f.write(f"models_bytes={models_bytes}\n")
            f.write(f"data_bytes={data_bytes}\n")
            f.write(f"models={models}\n")

    pct = (models_bytes / args.threshold * 100) if args.threshold else 0
    print(f"models.json: {models_bytes:,} bytes ({models:,} models) | "
          f"data/: {data_bytes:,} bytes | threshold: {args.threshold:,} "
          f"({pct:.1f}%) -> {'OVER — activate IPFS' if over else 'under — IPFS dormant'}")


if __name__ == "__main__":
    main()
