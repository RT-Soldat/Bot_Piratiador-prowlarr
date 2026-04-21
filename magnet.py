from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

PUBLIC_TRACKERS: list[str] = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://9.rarbg.com:2810/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://open.stealth.si:80/announce",
    "udp://exodus.desync.com:6969/announce",
]


def format_size(bytes_: int | None) -> str:
    if not bytes_:
        return "?"

    try:
        size = float(bytes_)
    except (TypeError, ValueError):
        return "?"

    if size <= 0:
        return "?"

    units = ["KB", "MB", "GB", "TB"]
    value = size / 1024

    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024

    return "?"


def build_magnet(info_hash: str, title: str, trackers: list[str]) -> str:
    magnet = f"magnet:?xt=urn:btih:{info_hash.strip()}&dn={quote(title.strip(), safe='')}"
    for tracker in trackers:
        magnet += f"&tr={quote(tracker, safe='')}"
    return magnet


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace(" ", "_")
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    normalized = normalized.strip("_-")

    if not normalized:
        return "torrent"

    return normalized[:80].rstrip("_-") or "torrent"
