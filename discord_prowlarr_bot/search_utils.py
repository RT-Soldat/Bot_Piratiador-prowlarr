from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .magnet import PUBLIC_TRACKERS, build_magnet


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def parse_positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def validate_query(query: str) -> str | None:
    cleaned_query = query.strip()
    if not cleaned_query:
        return "La búsqueda no puede estar vacía."
    if len(cleaned_query) > 200:
        return "La búsqueda no puede superar los 200 caracteres."
    return None


def extract_text_command(content: str) -> tuple[str, str] | None:
    stripped = content.strip()
    if not stripped.startswith("/"):
        return None

    parts = stripped.split(None, 1)
    command_name = parts[0].lower()
    if command_name not in {"/buscar", "/piratear"}:
        return None

    query = parts[1] if len(parts) > 1 else ""
    return command_name[1:], query


def get_indexer_name(result: dict[str, Any]) -> str:
    indexer = result.get("indexer")
    if isinstance(indexer, dict):
        return str(indexer.get("name") or "Desconocido")
    return str(indexer or "Desconocido")


def get_title(result: dict[str, Any]) -> str:
    return str(result.get("title") or "Sin título")


def get_magnet_url(result: dict[str, Any]) -> str | None:
    magnet_url = result.get("magnetUrl") or result.get("magnet_url")
    if isinstance(magnet_url, str) and magnet_url.strip():
        return magnet_url.strip()
    return None


def get_info_hash(result: dict[str, Any]) -> str | None:
    info_hash = result.get("infoHash") or result.get("info_hash") or result.get("hash")
    if isinstance(info_hash, str) and info_hash.strip():
        return info_hash.strip()
    return None


def get_download_url(result: dict[str, Any]) -> str | None:
    download_url = result.get("downloadUrl") or result.get("download_url") or result.get("guid")
    if isinstance(download_url, str) and download_url.strip():
        return download_url.strip()
    return None


def format_timeout_seconds(seconds: float) -> str:
    if seconds.is_integer():
        return str(int(seconds))
    return f"{seconds:g}"


def get_search_error_message(exc: Exception, timeout_seconds: float) -> str:
    if isinstance(exc, httpx.TimeoutException):
        formatted = format_timeout_seconds(timeout_seconds)
        return (
            f"Prowlarr tardó más de {formatted}s en responder. "
            "Probá de nuevo o aumentá PROWLARR_TIMEOUT en el .env."
        )
    return "Error consultando Prowlarr. Revisá los logs del bot."


def extract_info_hash_from_magnet(magnet_url: str) -> str | None:
    parsed = urlparse(magnet_url)
    if parsed.scheme != "magnet":
        return None

    xt_values = parse_qs(parsed.query).get("xt", [])
    for xt_value in xt_values:
        prefix = "urn:btih:"
        if xt_value.lower().startswith(prefix):
            return xt_value[len(prefix) :].strip() or None
    return None


def build_compact_magnet_url(
    result: dict[str, Any],
    title: str,
    fallback_magnet_url: str | None = None,
) -> str | None:
    info_hash = get_info_hash(result)
    if info_hash is None and fallback_magnet_url:
        info_hash = extract_info_hash_from_magnet(fallback_magnet_url)

    if info_hash:
        return build_magnet(info_hash, truncate(title, 80), PUBLIC_TRACKERS)

    if fallback_magnet_url:
        return fallback_magnet_url

    return None
