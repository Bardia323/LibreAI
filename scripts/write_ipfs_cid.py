#!/usr/bin/env python3
"""
Record the latest IPFS publish into data/ipfs.json so the site/README/mirrors
can point at the current content-addressed snapshot.

Usage:
  python scripts/write_ipfs_cid.py --cid bafy... --models 91000 --bytes 80123456
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).parent.parent / "data" / "ipfs.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True)
    ap.add_argument("--models", type=int, default=0)
    ap.add_argument("--bytes", type=int, default=0)
    args = ap.parse_args()

    OUT.write_text(json.dumps({
        "cid": args.cid,
        "gateway": f"https://{args.cid}.ipfs.dweb.link",
        "dnslink": f"/ipfs/{args.cid}",
        "models": args.models,
        "bytes": args.bytes,
        "published": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} -> {args.cid}")


if __name__ == "__main__":
    main()
