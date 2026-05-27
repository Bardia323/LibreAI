#!/usr/bin/env python3
"""
Build the static, shardable index from data/models.json.

Outputs to data/index/:
  meta.json              counts, totals, page_size, build timestamp
  slim.json              one lean record per model — the browse + search dataset
  records/<bucket>.json  full records keyed by id, bucketed by id prefix

Why this shape: the slim index carries only the fields the listing/search/sort
need (no descriptions, magnets or file lists), so it stays small enough to load
fully in the browser across the entire git-hostable range (~100k models, a few
MB gzipped). Browse, search, sort and filter all run in memory over slim.json;
only the visible page is rendered. The heavy per-model detail (magnets, files,
description) loads on demand from a record shard. Beyond git's ~100k ceiling the
slim index itself would shard by prefix and distribute over IPFS — same client,
more shards.

Usage: python scripts/build_index.py
"""

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import libindex as L

INDEX_DIR = L.MODELS_PATH.parent / "index"
PAGE_SIZE = 50


def bucket_key(model_id: str) -> str:
    """First two alphanumeric-normalized chars of the id, padded. Must match the
    JS bucketKey() in static/app.js exactly so the client finds the right shard."""
    return re.sub(r"[^a-z0-9]", "_", model_id.lower())[:2].ljust(2, "_")


def slim(m: dict) -> dict:
    seeders = peers = 0
    for mag in m.get("magnets", []):
        sw = mag.get("swarm") or {}
        seeders += sw.get("seeders") or 0
        peers   += sw.get("peers") or 0
    return {
        "id": m["id"],
        "name": m.get("name", ""),
        "creator": m.get("creator"),
        "category": m.get("category"),
        "variant": m.get("variant"),
        "architecture": m.get("architecture"),
        "parameters": m.get("parameters"),
        "added": m.get("added"),
        "hfu": m.get("huggingface"),          # HF link target for the browse row
        "tg": m.get("tags", []),              # searchable
        "s": seeders, "p": peers, "m": len(m.get("magnets", [])),
    }


def write_json(path: Path, obj, compact: bool):
    sep = (",", ":") if compact else (", ", ": ")
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=sep,
                               indent=None if compact else 2) + "\n",
                    encoding="utf-8")


def main():
    data = L.load_index()
    models = data["models"]

    # Rebuild from scratch so removed/renamed models leave no orphan shards.
    if INDEX_DIR.exists():
        shutil.rmtree(INDEX_DIR)
    (INDEX_DIR / "records").mkdir(parents=True)

    # Slim dataset, canonical order = newest first.
    slims = sorted((slim(m) for m in models),
                   key=lambda r: r["added"] or "", reverse=True)
    write_json(INDEX_DIR / "slim.json", slims, compact=True)

    # Full records, bucketed by id prefix for on-demand detail loads.
    buckets: dict[str, dict] = {}
    for m in models:
        buckets.setdefault(bucket_key(m["id"]), {})[m["id"]] = m
    for key, recs in buckets.items():
        write_json(INDEX_DIR / "records" / f"{key}.json", recs, compact=True)

    # Counts for the landing page and filters.
    categories = {"all": len(models)}
    variants: dict[str, int] = {}
    for m in models:
        c = m.get("category")
        categories[c] = categories.get(c, 0) + 1
        v = m.get("variant")
        variants[v] = variants.get(v, 0) + 1

    meta = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(models),
        "page_size": PAGE_SIZE,
        "categories": categories,
        "variants": variants,
        "record_buckets": sorted(buckets.keys()),
    }
    write_json(INDEX_DIR / "meta.json", meta, compact=False)

    print(f"Built index: {len(models)} models, {len(buckets)} record bucket(s) "
          f"-> {INDEX_DIR}")


if __name__ == "__main__":
    main()
