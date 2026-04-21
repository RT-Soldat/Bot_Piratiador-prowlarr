from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

LOGGER = logging.getLogger("discord_prowlarr_bot")


@dataclass(slots=True)
class Config:
    discord_token: str
    allowed_channel_id: int
    prowlarr_url: str
    prowlarr_api_key: str
    prowlarr_timeout: float = 90.0
    http_listen_host: str = "0.0.0.0"
    http_listen_port: int = 9987
    public_base_url: str = ""
    torrent_fetch_timeout: float = 45.0
    libtorrent_listen_port: int = 6881
    attach_torrent_file: bool = False
    log_level: str = "INFO"


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_config() -> Config:
    missing: list[str] = []

    discord_token = os.getenv("DISCORD_TOKEN", "").strip()
    if not discord_token:
        missing.append("DISCORD_TOKEN")

    channel_raw = os.getenv("ALLOWED_CHANNEL_ID", "").strip()
    if not channel_raw:
        missing.append("ALLOWED_CHANNEL_ID")
        allowed_channel_id = 0
    else:
        try:
            allowed_channel_id = int(channel_raw)
        except ValueError:
            LOGGER.error("ALLOWED_CHANNEL_ID debe ser un entero valido.")
            raise SystemExit(1) from None

    prowlarr_url = os.getenv("PROWLARR_URL", "").strip()
    if not prowlarr_url:
        missing.append("PROWLARR_URL")

    prowlarr_api_key = os.getenv("PROWLARR_API_KEY", "").strip()
    if not prowlarr_api_key:
        missing.append("PROWLARR_API_KEY")

    timeout_raw = os.getenv("PROWLARR_TIMEOUT", "90").strip()
    try:
        prowlarr_timeout = float(timeout_raw)
    except ValueError:
        LOGGER.error("PROWLARR_TIMEOUT debe ser un numero valido.")
        raise SystemExit(1) from None

    if prowlarr_timeout <= 0:
        LOGGER.error("PROWLARR_TIMEOUT debe ser mayor que 0.")
        raise SystemExit(1)

    http_listen_host = os.getenv("BOT_HTTP_LISTEN_HOST", "0.0.0.0").strip() or "0.0.0.0"
    http_listen_port = parse_int_env("BOT_HTTP_LISTEN_PORT", 9987)
    validate_port("BOT_HTTP_LISTEN_PORT", http_listen_port)

    public_base_url = normalize_public_base_url(os.getenv("BOT_PUBLIC_BASE_URL", ""))
    torrent_fetch_timeout = parse_float_env("TORRENT_FETCH_TIMEOUT", 45.0)
    if torrent_fetch_timeout <= 0:
        LOGGER.error("TORRENT_FETCH_TIMEOUT debe ser mayor que 0.")
        raise SystemExit(1)

    libtorrent_listen_port = parse_int_env("LIBTORRENT_LISTEN_PORT", 6881)
    validate_port("LIBTORRENT_LISTEN_PORT", libtorrent_listen_port)

    attach_torrent_file = parse_bool_env("ATTACH_TORRENT_FILE", False)

    if missing:
        LOGGER.error("Faltan variables de entorno obligatorias: %s", ", ".join(missing))
        raise SystemExit(1)

    return Config(
        discord_token=discord_token,
        allowed_channel_id=allowed_channel_id,
        prowlarr_url=prowlarr_url.rstrip("/"),
        prowlarr_api_key=prowlarr_api_key,
        prowlarr_timeout=prowlarr_timeout,
        http_listen_host=http_listen_host,
        http_listen_port=http_listen_port,
        public_base_url=public_base_url,
        torrent_fetch_timeout=torrent_fetch_timeout,
        libtorrent_listen_port=libtorrent_listen_port,
        attach_torrent_file=attach_torrent_file,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


def parse_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False

    LOGGER.error("%s debe ser true/false, yes/no, on/off o 1/0.", name)
    raise SystemExit(1)


def parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    try:
        return int(raw)
    except ValueError:
        LOGGER.error("%s debe ser un entero valido.", name)
        raise SystemExit(1) from None


def parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    try:
        return float(raw)
    except ValueError:
        LOGGER.error("%s debe ser un numero valido.", name)
        raise SystemExit(1) from None


def validate_port(name: str, value: int) -> None:
    if 1 <= value <= 65535:
        return

    LOGGER.error("%s debe estar entre 1 y 65535.", name)
    raise SystemExit(1)


def normalize_public_base_url(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return ""

    normalized = cleaned.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        LOGGER.error("BOT_PUBLIC_BASE_URL debe ser una URL http(s) valida, por ejemplo http://errete.ddns.net:9987")
        raise SystemExit(1)

    return normalized
