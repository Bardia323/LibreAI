#!/usr/bin/env python3
"""
Process a submission and decide: auto-merge or open a review PR.

A submission is a JSON object found inside a GitHub issue body (fenced as
```json ... ``` or as the first bare {...} block). Two shapes are accepted:

  New model:
    { "type": "model", "name": "...", "category": "llm", "variant": "instruct",
      ... , "magnets": [ { "label": "...", "url": "magnet:?..." } ] }

  Add a magnet to an existing model:
    { "type": "magnet", "target": "<model-id-or-hf-url>",
      "magnet": { "label": "Q4_K_M", "url": "magnet:?..." } }

Decision rules
--------------
- Duplicate HF URL (for a new model)            -> rejected
- Duplicate infohash (any magnet)               -> rejected
- New model, declared formats all safe, and
  every magnet resolves to safe files           -> AUTO_MERGE
- New model with no magnets but safe formats     -> AUTO_MERGE (metadata only)
- Add-magnet whose metadata resolves to safe     -> AUTO_MERGE
- Anything unresolved / unsafe / uncertain       -> MANUAL (review PR)

Side effects
------------
- On any non-rejected decision the script writes the mutated data/models.json
  (magnets get verified/files/size/infohash filled in). The workflow commits it
  to main (auto-merge) or to a branch + PR (manual).
- Emits machine-readable outputs to $GITHUB_OUTPUT: decision, message, model_id.

Usage:
  python scripts/process_submission.py --body-file issue.txt [--timeout 60]
  echo "$ISSUE_BODY" | python scripts/process_submission.py --stdin
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import libindex as L
import check_magnet

AUTO_MERGE = "auto_merge"
MANUAL     = "manual"
REJECTED   = "rejected"


# ── Issue-body parsing ──────────────────────────────────────────────────────

def extract_json(body: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        brace = re.search(r"(\{.*\})", body, re.DOTALL)
        raw = brace.group(1) if brace else None
    if raw is None:
        raise ValueError("no JSON object found in submission body")
    return json.loads(raw)


# ── Magnet enrichment ───────────────────────────────────────────────────────

def enrich_magnet(mag: dict, timeout: int) -> tuple[dict, bool, str]:
    """Resolve + classify a single magnet. Returns (magnet, ok_to_auto, reason)."""
    url = mag.get("url", "")
    result = check_magnet.check(url, timeout)

    mag = dict(mag)
    mag["infohash"] = result["infohash"]
    mag.setdefault("label", mag.get("label") or "Download")
    mag["files"] = result["files"]
    if result["size"]:
        mag["size"] = human_size(result["size"])
    mag["verified"] = bool(result["resolved"] and result["all_safe"])
    mag.setdefault("swarm", {"seeders": 0, "peers": 0, "checked": None})

    if not result["infohash"]:
        return mag, False, "invalid magnet URI"
    if not result["resolved"]:
        return mag, False, f"could not verify magnet metadata ({result['reason']})"
    if not result["all_safe"]:
        return mag, False, f"contains non-safe files: {result['unsafe']}"
    return mag, True, "verified safe"


def human_size(n: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} PB"


# ── Decision logic ──────────────────────────────────────────────────────────

def process(body: str, timeout: int) -> dict:
    """Returns {decision, message, model_id, data} where data is the (possibly)
    mutated index ready to be written, or None if rejected."""
    index  = L.load_index()
    models = index["models"]
    seen_infohashes = L.all_infohashes(models)

    try:
        sub = extract_json(body)
    except (ValueError, json.JSONDecodeError) as e:
        return {"decision": REJECTED, "message": f"Could not parse submission JSON: {e}",
                "model_id": "", "data": None}

    sub_type = sub.get("type")
    if not sub_type:
        sub_type = "magnet" if ("target" in sub and "magnet" in sub) else "model"

    if sub_type == "magnet":
        return process_magnet(sub, models, index, seen_infohashes, timeout)
    return process_model(sub, models, index, seen_infohashes, timeout)


def process_model(sub, models, index, seen_infohashes, timeout) -> dict:
    errors = L.validate_model(sub)
    if errors:
        return {"decision": REJECTED, "message": "Validation failed:\n- " + "\n- ".join(errors),
                "model_id": "", "data": None}

    # Dedup by HF URL and by id
    if sub.get("huggingface") and L.find_by_hf(models, sub["huggingface"]):
        existing = L.find_by_hf(models, sub["huggingface"])
        return {"decision": REJECTED,
                "message": f"Duplicate: that HuggingFace URL already exists as `{existing['id']}`. "
                           f"To add a download, submit a magnet to `{existing['id']}` instead.",
                "model_id": existing["id"], "data": None}

    model_id = sub.get("id") or L.slugify(sub["name"])
    if L.find_by_id(models, model_id):
        return {"decision": REJECTED,
                "message": f"Duplicate: a model with id `{model_id}` already exists.",
                "model_id": model_id, "data": None}

    # Build the new model record
    record = {
        "id": model_id,
        "name": sub["name"],
        "creator": sub.get("creator"),
        "category": sub["category"],
        "variant": sub.get("variant", "base"),
        "architecture": sub.get("architecture", "other"),
        "parameters": sub.get("parameters"),
        "context": sub.get("context"),
        "formats": sub.get("formats", []),
        "license": sub.get("license"),
        "description": sub.get("description", ""),
        "magnets": [],
        "huggingface": sub.get("huggingface"),
        "tags": sub.get("tags", []),
        "added": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    notes, all_auto = [], True

    for mag in sub.get("magnets", []):
        ih = (L.parse_magnet(mag.get("url", "")) or {}).get("infohash")
        if ih and ih in seen_infohashes:
            return {"decision": REJECTED,
                    "message": f"Duplicate magnet: infohash `{ih}` already exists "
                               f"under `{seen_infohashes[ih]}`.",
                    "model_id": model_id, "data": None}
        enriched, ok, reason = enrich_magnet(mag, timeout)
        record["magnets"].append(enriched)
        notes.append(f"`{enriched['label']}`: {reason}")
        all_auto = all_auto and ok

    formats_safe = L.formats_are_safe(record["formats"]) if record["formats"] else True
    decision = AUTO_MERGE if (all_auto and formats_safe) else MANUAL

    models.append(record)
    index["models"] = models
    index["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    msg = [f"**New model:** `{model_id}`", ""]
    if record["magnets"]:
        msg.append("Magnet checks:")
        msg += [f"- {n}" for n in notes]
    else:
        msg.append("No magnets included — metadata-only entry.")
    if not formats_safe:
        msg.append(f"\nDeclared formats `{record['formats']}` include non-safe types → manual review.")
    msg.append(f"\n**Decision: {'auto-merge' if decision == AUTO_MERGE else 'manual review'}**")

    return {"decision": decision, "message": "\n".join(msg), "model_id": model_id, "data": index}


def process_magnet(sub, models, index, seen_infohashes, timeout) -> dict:
    target = sub.get("target", "")
    mag = sub.get("magnet")
    if not target or not mag:
        return {"decision": REJECTED, "message": "Magnet submission needs `target` and `magnet`.",
                "model_id": "", "data": None}

    model = L.find_by_id(models, target) or L.find_by_hf(models, target)
    if not model:
        return {"decision": REJECTED,
                "message": f"No model found for target `{target}`. Submit it as a new model first.",
                "model_id": "", "data": None}

    ih = (L.parse_magnet(mag.get("url", "")) or {}).get("infohash")
    if not ih:
        return {"decision": REJECTED, "message": "Invalid magnet URI.",
                "model_id": model["id"], "data": None}
    if ih in seen_infohashes:
        return {"decision": REJECTED,
                "message": f"Duplicate magnet: infohash `{ih}` already exists under "
                           f"`{seen_infohashes[ih]}`.",
                "model_id": model["id"], "data": None}

    enriched, ok, reason = enrich_magnet(mag, timeout)
    model.setdefault("magnets", []).append(enriched)
    index["models"] = models
    index["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    decision = AUTO_MERGE if ok else MANUAL
    msg = (f"**Add magnet to `{model['id']}`**\n\n"
           f"- `{enriched['label']}`: {reason}\n\n"
           f"**Decision: {'auto-merge' if decision == AUTO_MERGE else 'manual review'}**")
    return {"decision": decision, "message": msg, "model_id": model["id"], "data": index}


# ── GitHub Actions glue ─────────────────────────────────────────────────────

def emit_outputs(result: dict):
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    # message is multi-line → use the heredoc delimiter form
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"decision={result['decision']}\n")
        f.write(f"model_id={result['model_id']}\n")
        f.write("message<<__LIBREAI_EOF__\n")
        f.write(result["message"] + "\n")
        f.write("__LIBREAI_EOF__\n")


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file")
    src.add_argument("--stdin", action="store_true")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    body = sys.stdin.read() if args.stdin else Path(args.body_file).read_text(encoding="utf-8")

    result = process(body, args.timeout)

    if result["data"] is not None and result["decision"] != REJECTED:
        L.save_index(result["data"])

    emit_outputs(result)
    print(f"decision={result['decision']}")
    print(result["message"])
    # Rejections shouldn't fail the job (the workflow comments + closes), so exit 0.


if __name__ == "__main__":
    main()
