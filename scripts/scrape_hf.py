#!/usr/bin/env python3
"""
Scrapes HuggingFace for models and merges them into data/models.json.
Usage: python scripts/scrape_hf.py [--limit N] [--dry-run]

Requires: pip install requests
"""

import argparse
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import libindex as L

HF_API = "https://huggingface.co/api/models"

# pipeline_tag → (category, architecture hint)
PIPELINE_MAP = {
    "text-generation":       ("llm",       "transformer"),
    "text2text-generation":  ("llm",       "transformer"),
    "feature-extraction":    ("llm",       "transformer"),
    "text-to-image":         ("diffusion", "dit"),
    "text-to-video":         ("diffusion", "dit"),
    "image-to-video":        ("diffusion", "dit"),
    "text-to-audio":         ("diffusion", "other"),
    "audio-generation":      ("diffusion", "other"),
}

# Tags that indicate a specific architecture
ARCH_TAGS = {
    "mamba":           "mamba",
    "rwkv":            "rwkv",
    "state-space":     "mamba",
    "ssm":             "mamba",
    "unet":            "unet",
    "dit":             "dit",
    "flow-matching":   "flow-matching",
    "mixture-of-experts": "moe",
    "moe":             "moe",
    "hybrid":          "hybrid",
}

# Tags that indicate UNet-based diffusion (legacy SD)
UNET_HINTS = {"stable-diffusion", "stable-diffusion-xl", "controlnet"}


def fetch_hf(pipeline_tag: str, limit: int) -> list[dict]:
    params = {
        "pipeline_tag": pipeline_tag,
        "sort":         "downloads",
        "direction":    -1,
        "limit":        limit,
        "full":         "true",
    }
    r = __import__("requests").get(HF_API, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def detect_arch(hf_model: dict, default: str) -> str:
    tags = [t.lower() for t in (hf_model.get("tags") or [])]
    tag_set = set(tags)

    for tag, arch in ARCH_TAGS.items():
        if tag in tag_set:
            return arch

    if tag_set & UNET_HINTS:
        return "unet"

    return default


def detect_category(hf_model: dict, default: str) -> str:
    tags = [t.lower() for t in (hf_model.get("tags") or [])]
    if "mamba" in tags or "rwkv" in tags or "ssm" in tags:
        return "misc"
    return default


def extract_params(hf_model: dict) -> str | None:
    name = hf_model.get("id", "")
    # common patterns: 7b, 70b, 1.5b, 8x7b, 671b
    m = re.search(r"(\d+(?:[x×]\d+)?(?:\.\d+)?[bBmM])", name)
    if m:
        return m.group(1).upper()
    return None


def detect_variant(hf_model: dict) -> str:
    hf_id = hf_model.get("id", "").lower()
    tags  = {t.lower() for t in (hf_model.get("tags") or [])}

    if "lora" in tags or "lora" in hf_id or "peft" in tags:
        return "lora"
    if {"rlhf", "rlaif", "dpo", "ppo"} & tags or "-r1" in hf_id or "reasoning" in tags:
        return "rlhf"
    if any(k in hf_id for k in ("instruct", "-it", "chat")) or {"instruct", "conversational"} & tags:
        return "instruct"
    if "finetune" in tags or "fine-tune" in hf_id:
        return "finetune"
    return "base"


def hf_to_model(hf: dict, pipeline_tag: str) -> dict:
    hf_id   = hf.get("id", "")
    cat_def, arch_def = PIPELINE_MAP.get(pipeline_tag, ("misc", "other"))
    category = detect_category(hf, cat_def)
    arch     = detect_arch(hf, arch_def)

    tags_raw = hf.get("tags") or []
    useful   = {"instruct", "chat", "code", "multilingual", "reasoning",
                "math", "vision", "audio", "rlhf", "dpo", "moe",
                "text-to-image", "text-to-video", "fast", "distilled"}
    tags = [t for t in tags_raw if t.lower() in useful][:8]

    added = (hf.get("lastModified") or hf.get("createdAt") or datetime.now(timezone.utc).isoformat())[:10]

    slug = hf_id.replace("/", "--").lower()
    slug = re.sub(r"[^a-z0-9.\-]", "-", slug).strip("-")

    return {
        "id":           slug,
        "name":         hf_id.split("/")[-1].replace("-", " ").replace("_", " "),
        "creator":      hf_id.split("/")[0] if "/" in hf_id else None,
        "category":     category,
        "variant":      detect_variant(hf),
        "architecture": arch,
        "parameters":   extract_params(hf),
        "context":      None,
        "formats":      ["safetensors"],
        "license":      (hf.get("cardData") or {}).get("license") or None,
        "description":  "",
        "magnets":      [],
        "huggingface":  f"https://huggingface.co/{hf_id}",
        "tags":         tags,
        "added":        added,
    }


def merge(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    seen  = {m["id"] for m in existing}
    added = 0
    for m in new:
        if m["id"] not in seen:
            existing.append(m)
            seen.add(m["id"])
            added += 1
    return existing, added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int, default=50, help="Models per pipeline tag")
    parser.add_argument("--dry-run", action="store_true",  help="Print without saving")
    args = parser.parse_args()

    data   = L.load_index()
    models = data["models"]
    total  = 0

    for pipeline, (cat, arch) in PIPELINE_MAP.items():
        print(f"  Fetching {pipeline} (up to {args.limit})…", end=" ", flush=True)
        try:
            hf_models = fetch_hf(pipeline, args.limit)
            converted = [hf_to_model(h, pipeline) for h in hf_models]
            models, added = merge(models, converted)
            print(f"+{added} new")
            total += added
            time.sleep(0.5)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)

    data["models"]  = models
    data["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if args.dry_run:
        print(f"\nDry run — would add {total} models ({len(models)} total).")
    else:
        L.save_index(data)
        print(f"\nDone. +{total} new models → {len(models)} total in {L.MODELS_PATH}")


if __name__ == "__main__":
    main()
