"""
Shared helpers for the LibreAI index.

Pure standard library — no third-party deps — so it imports cleanly in any CI
environment. Magnet metadata resolution (which needs libtorrent) lives in
check_magnet.py; this module only does string-level parsing and bookkeeping.
"""

import base64
import binascii
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

MODELS_PATH = Path(__file__).parent.parent / "data" / "models.json"

# File extensions safe enough to auto-merge without human review.
# safetensors / gguf are tensor containers that cannot execute code; the rest
# are plain text / config. Anything NOT in this set forces manual review.
SAFE_EXTS = {
    ".safetensors", ".gguf",
    ".txt", ".md", ".json", ".yaml", ".yml",
    ".model", ".vocab", ".tokenizer", ".merges",
}

# Extensions known to be capable of arbitrary code execution (pickle-backed) or
# opaque archives. Listed for clarity / messaging; anything outside SAFE_EXTS is
# treated as unsafe regardless.
UNSAFE_EXTS = {
    ".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle", ".h5", ".pb",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".exe", ".sh", ".py", ".dll", ".so",
}

VARIANTS      = {"base", "instruct", "rlhf", "finetune", "lora"}
CATEGORIES    = {"llm", "diffusion", "misc"}
ARCHITECTURES = {"transformer", "dit", "unet", "flow-matching",
                 "mamba", "rwkv", "hybrid", "moe", "other"}


# ── Magnet parsing ──────────────────────────────────────────────────────────

def normalize_infohash(raw: str) -> str | None:
    """Return a lowercase hex infohash from a hex (40), base32 (32) or v2 (64)
    representation, or None if it can't be parsed."""
    if not raw:
        return None
    raw = raw.strip()
    # btmh v2 multihash prefix (1220...) — keep the hex as-is
    if re.fullmatch(r"[0-9a-fA-F]{40}", raw):
        return raw.lower()
    if re.fullmatch(r"[0-9a-fA-F]{64}", raw):
        return raw.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", raw):
        try:
            return binascii.hexlify(base64.b32decode(raw.upper())).decode().lower()
        except (binascii.Error, ValueError):
            return None
    return None


def parse_magnet(uri: str) -> dict | None:
    """Extract infohash, display name, trackers and declared length from a
    magnet URI. Returns None if no valid btih/btmh is present."""
    if not uri or not uri.startswith("magnet:"):
        return None

    query = uri[len("magnet:"):].lstrip("?")
    params = parse_qs(query)

    infohash = None
    for xt in params.get("xt", []):
        m = re.match(r"urn:bt(?:ih|mh):(.+)$", xt, re.IGNORECASE)
        if m:
            candidate = m.group(1)
            # btmh v2: strip the multihash prefix "1220" if present
            if candidate.lower().startswith("1220") and len(candidate) == 68:
                candidate = candidate[4:]
            infohash = normalize_infohash(candidate)
            if infohash:
                break

    if not infohash:
        return None

    trackers = [unquote(t) for t in params.get("tr", [])]
    name = params.get("dn", [None])[0]
    length = None
    if params.get("xl"):
        try:
            length = int(params["xl"][0])
        except ValueError:
            pass

    return {
        "infohash": infohash,
        "name": unquote(name) if name else None,
        "trackers": trackers,
        "length": length,
    }


# ── File safety classification ──────────────────────────────────────────────

def _ext(filename: str) -> str:
    name = filename.lower().rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def classify_files(files: list[str]) -> tuple[bool, list[str]]:
    """Return (all_safe, unsafe_files). An empty file list is NOT considered
    safe — it means metadata could not be resolved."""
    if not files:
        return False, []
    unsafe = [f for f in files if _ext(f) not in SAFE_EXTS]
    return (len(unsafe) == 0), unsafe


def formats_are_safe(formats: list[str]) -> bool:
    """Whether the declared `formats` array contains only safe container types."""
    if not formats:
        return False
    return all(f"." + f.lower().lstrip(".") in SAFE_EXTS for f in formats)


# ── Index I/O ───────────────────────────────────────────────────────────────

def load_index(path: Path = MODELS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(data: dict, path: Path = MODELS_PATH) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def all_infohashes(models: list[dict]) -> dict[str, str]:
    """Map infohash -> model id for every magnet in the index."""
    out = {}
    for m in models:
        for mag in m.get("magnets", []):
            ih = mag.get("infohash") or (parse_magnet(mag.get("url", "")) or {}).get("infohash")
            if ih:
                out[ih.lower()] = m["id"]
    return out


def find_by_hf(models: list[dict], url: str) -> dict | None:
    if not url:
        return None
    norm = url.rstrip("/").lower()
    for m in models:
        if (m.get("huggingface") or "").rstrip("/").lower() == norm:
            return m
    return None


def find_by_id(models: list[dict], model_id: str) -> dict | None:
    return next((m for m in models if m["id"] == model_id), None)


# ── Validation ──────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9.]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def validate_model(obj: dict) -> list[str]:
    errors = []
    if not obj.get("name"):
        errors.append("missing required field: name")
    cat = obj.get("category")
    if cat not in CATEGORIES:
        errors.append(f"invalid category: {cat!r} (expected one of {sorted(CATEGORIES)})")
    var = obj.get("variant", "base")
    if var not in VARIANTS:
        errors.append(f"invalid variant: {var!r} (expected one of {sorted(VARIANTS)})")
    arch = obj.get("architecture")
    if arch and arch not in ARCHITECTURES:
        errors.append(f"invalid architecture: {arch!r}")
    hf = obj.get("huggingface")
    if hf and not hf.startswith(("http://", "https://")):
        errors.append("huggingface must be a URL")
    return errors
