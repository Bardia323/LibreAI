#!/usr/bin/env python3
"""
Resolve a magnet's file list from the BitTorrent network and classify it.

The magnet URI itself does NOT contain the file list — only an infohash. To know
what a torrent actually contains (and therefore whether it's safe to auto-merge)
we must fetch the torrent's metadata from the DHT / swarm. That requires
libtorrent.

Output (JSON, stdout):
{
  "infohash": "....",
  "resolved": true|false,     # could we fetch metadata at all?
  "files":    ["model.safetensors", ...],
  "size":     12884901888,    # total bytes, or null
  "all_safe": true|false,     # every file has a safe extension
  "unsafe":   ["evil.ckpt"],  # offending files, if any
  "reason":   "..."           # human-readable status
}

Exit code is always 0 — the caller decides what to do with `resolved`/`all_safe`.
A torrent that can't be resolved (no seeders, libtorrent missing) is reported as
resolved=false so the submission pipeline routes it to manual review rather than
silently approving it.

Usage:
  python scripts/check_magnet.py "magnet:?xt=urn:btih:..." [--timeout 60]
"""

import argparse
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
import libindex as L


def resolve_with_libtorrent(uri: str, timeout: int) -> tuple[bool, list[str], int | None, str]:
    """Returns (resolved, files, total_size, reason)."""
    try:
        import libtorrent as lt
    except ImportError:
        return False, [], None, "libtorrent not installed — cannot resolve metadata"

    try:
        session = lt.session({
            "listen_interfaces": "0.0.0.0:6881",
            "enable_dht": True,
        })
        # Bootstrap DHT so we can find peers for magnets without live trackers.
        for host, port in [
            ("router.bittorrent.com", 6881),
            ("dht.transmissionbt.com", 6881),
            ("router.utorrent.com", 6881),
        ]:
            try:
                session.add_dht_node((host, port))
            except Exception:
                pass

        params = lt.parse_magnet_uri(uri)
        params.save_path = "."
        # Don't actually download — we only want the metadata.
        params.flags |= lt.torrent_flags.upload_mode
        handle = session.add_torrent(params)

        deadline = time.time() + timeout
        while not handle.has_metadata():
            if time.time() > deadline:
                session.remove_torrent(handle)
                return False, [], None, f"metadata not found within {timeout}s (no seeders?)"
            time.sleep(0.5)

        ti = handle.torrent_file()
        files = []
        fs = ti.files()
        for i in range(fs.num_files()):
            files.append(fs.file_path(i))
        total = ti.total_size()
        session.remove_torrent(handle)
        return True, files, total, "metadata resolved"
    except Exception as e:  # noqa: BLE001 — report any failure as unresolved
        return False, [], None, f"resolution error: {e}"


def check(uri: str, timeout: int) -> dict:
    parsed = L.parse_magnet(uri)
    if not parsed:
        return {
            "infohash": None, "resolved": False, "files": [], "size": None,
            "all_safe": False, "unsafe": [], "reason": "invalid magnet URI",
        }

    resolved, files, size, reason = resolve_with_libtorrent(uri, timeout)
    all_safe, unsafe = L.classify_files(files) if resolved else (False, [])

    return {
        "infohash": parsed["infohash"],
        "resolved": resolved,
        "files": files,
        "size": size,
        "all_safe": all_safe,
        "unsafe": unsafe,
        "reason": reason if not resolved else (
            "all files safe" if all_safe else f"unsafe files present: {unsafe}"
        ),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("magnet")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    result = check(args.magnet, args.timeout)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
