#!/usr/bin/env python3
"""
Refresh seeder / peer counts for every magnet in the index.

Seeder counts are inherently live data — they change minute to minute — so we
cannot bake them into the static index permanently. Instead a scheduled CI job
runs this script, scrapes the trackers listed in each magnet, and writes the
latest numbers (plus a timestamp) into data/models.json. The site shows them as
"as of <checked>".

Supports HTTP(S) tracker scrape (BEP 48) and UDP tracker scrape (BEP 15) using
only the standard library — a tiny bencode decoder is included. UDP is important
because most public trackers (opentrackr, demonii, etc.) are UDP-only.

Usage:
  python scripts/update_swarm.py [--timeout 5] [--dry-run]
"""

import argparse
import binascii
import random
import socket
import struct
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import libindex as L


# ── Minimal bencode decoder ─────────────────────────────────────────────────

def bdecode(data: bytes):
    def parse(i):
        c = data[i:i + 1]
        if c == b"i":
            j = data.index(b"e", i)
            return int(data[i + 1:j]), j + 1
        if c.isdigit():
            colon = data.index(b":", i)
            n = int(data[i:colon])
            start = colon + 1
            return data[start:start + n], start + n
        if c == b"l":
            i += 1
            out = []
            while data[i:i + 1] != b"e":
                v, i = parse(i)
                out.append(v)
            return out, i + 1
        if c == b"d":
            i += 1
            out = {}
            while data[i:i + 1] != b"e":
                k, i = parse(i)
                v, i = parse(i)
                out[k] = v
            return out, i + 1
        raise ValueError(f"bad bencode at {i}")
    value, _ = parse(0)
    return value


# ── HTTP(S) scrape (BEP 48) ─────────────────────────────────────────────────

def scrape_http(tracker: str, infohash_hex: str, timeout: int):
    # announce URL -> scrape URL: replace the last path segment "announce"
    if "announce" not in tracker:
        return None
    scrape_url = tracker.replace("announce", "scrape")
    raw = binascii.unhexlify(infohash_hex)
    sep = "&" if "?" in scrape_url else "?"
    url = f"{scrape_url}{sep}info_hash={quote(raw)}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            decoded = bdecode(resp.read())
        files = decoded.get(b"files", {})
        for _, stats in files.items():
            return {"seeders": int(stats.get(b"complete", 0)),
                    "peers": int(stats.get(b"incomplete", 0))}
    except Exception:
        return None
    return None


# ── UDP scrape (BEP 15) ─────────────────────────────────────────────────────

UDP_MAGIC = 0x41727101980  # protocol id for the connect handshake


def scrape_udp(tracker: str, infohash_hex: str, timeout: int):
    parsed = urlparse(tracker)
    host, port = parsed.hostname, parsed.port or 80
    if not host:
        return None
    try:
        addr = (socket.gethostbyname(host), port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)

        # 1) connect
        tx = random.randint(0, 0xFFFFFFFF)
        sock.sendto(struct.pack(">QII", UDP_MAGIC, 0, tx), addr)
        resp, _ = sock.recvfrom(16)
        action, rtx, conn_id = struct.unpack(">IIQ", resp)
        if action != 0 or rtx != tx:
            return None

        # 2) scrape
        tx = random.randint(0, 0xFFFFFFFF)
        packet = struct.pack(">QII", conn_id, 2, tx) + binascii.unhexlify(infohash_hex)
        sock.sendto(packet, addr)
        resp, _ = sock.recvfrom(8 + 12)
        action, rtx = struct.unpack(">II", resp[:8])
        if action != 2 or rtx != tx:
            return None
        complete, downloaded, incomplete = struct.unpack(">III", resp[8:20])
        return {"seeders": complete, "peers": incomplete}
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass


def scrape_one(magnet: dict, timeout: int):
    parsed = L.parse_magnet(magnet.get("url", ""))
    if not parsed:
        return None
    infohash = magnet.get("infohash") or parsed["infohash"]
    if len(infohash) != 40:  # scrape protocols here are v1 (SHA-1) only
        return None

    best = None
    for tracker in parsed["trackers"]:
        scheme = urlparse(tracker).scheme
        result = (scrape_udp if scheme == "udp" else scrape_http)(tracker, infohash, timeout)
        if result and (best is None or result["seeders"] > best["seeders"]):
            best = result
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    index = L.load_index()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    updated = 0

    for model in index["models"]:
        for mag in model.get("magnets", []):
            result = scrape_one(mag, args.timeout)
            if result is not None:
                mag["swarm"] = {**result, "checked": now}
                updated += 1
                print(f"  {model['id']} / {mag.get('label')}: "
                      f"{result['seeders']}▲ {result['peers']}▼")
            else:
                # keep stale numbers but mark when we last tried
                sw = mag.setdefault("swarm", {"seeders": 0, "peers": 0, "checked": None})
                sw["checked"] = now

    if args.dry_run:
        print(f"\nDry run — would update {updated} magnet(s).")
    else:
        L.save_index(index)
        print(f"\nUpdated swarm stats for {updated} magnet(s) → {L.MODELS_PATH}")


if __name__ == "__main__":
    main()
