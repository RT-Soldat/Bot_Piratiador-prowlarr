from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
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
    registry_ttl_seconds: int = 60 * 60 * 24 * 7
    registry_purge_interval_seconds: int = 300
    registry_data_dir: Path = Path("/app/data/registry")
    rate_limit_calls: int = 5
    rate_limit_window_seconds: int = 60
    subtitle_enabled: bool = True
    opensubtitles_api_key: str = ""
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""
    subtitle_languages: list[str] = field(default_factory=lambda: ["es"])
    translation_enabled: bool = True
    translation_provider: str = "google"
    deepl_api_key: str = ""
    subtitle_fetch_timeout: float = 30.0


def configure_logging(config: Config) -> None:
    level = getattr(logging, config.log_level, logging.INFO)
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

    registry_ttl_seconds = parse_int_env("REGISTRY_TTL_SECONDS", 60 * 60 * 24 * 7)
    if registry_ttl_seconds <= 0:
        LOGGER.error("REGISTRY_TTL_SECONDS debe ser mayor que 0.")
        raise SystemExit(1)

    registry_purge_interval_seconds = parse_int_env("REGISTRY_PURGE_INTERVAL_SECONDS", 300)
    if registry_purge_interval_seconds <= 0:
        LOGGER.error("REGISTRY_PURGE_INTERVAL_SECONDS debe ser mayor que 0.")
        raise SystemExit(1)

    registry_data_dir = Path(os.getenv("REGISTRY_DATA_DIR", "/app/data/registry").strip() or "/app/data/registry")

    rate_limit_calls = parse_int_env("RATE_LIMIT_CALLS", 5)
    if rate_limit_calls <= 0:
        LOGGER.error("RATE_LIMIT_CALLS debe ser mayor que 0.")
        raise SystemExit(1)

    rate_limit_window_seconds = parse_int_env("RATE_LIMIT_WINDOW_SECONDS", 60)
    if rate_limit_window_seconds <= 0:
        LOGGER.error("RATE_LIMIT_WINDOW_SECONDS debe ser mayor que 0.")
        raise SystemExit(1)

    subtitle_enabled = parse_bool_env("SUBTITLE_ENABLED", True)
    opensubtitles_api_key = os.getenv("OPENSUBTITLES_API_KEY", "").strip()
    opensubtitles_username = os.getenv("OPENSUBTITLES_USERNAME", "").strip()
    opensubtitles_password = os.getenv("OPENSUBTITLES_PASSWORD", "").strip()

    subtitle_languages_raw = os.getenv("SUBTITLE_LANGUAGES", "es").strip()
    subtitle_languages = [
        lang.strip().lower()
        for lang in subtitle_languages_raw.split(",")
        if lang.strip()
    ]
    if not subtitle_languages:
        subtitle_languages = ["es"]

    translation_enabled = parse_bool_env("TRANSLATION_ENABLED", True)
    translation_provider = os.getenv("TRANSLATION_PROVIDER", "google").strip().lower()
    if translation_provider not in {"google", "deepl"}:
        LOGGER.error("TRANSLATION_PROVIDER debe ser 'google' o 'deepl'.")
        raise SystemExit(1)

    deepl_api_key = os.getenv("DEEPL_API_KEY", "").strip()

    subtitle_fetch_timeout = parse_float_env("SUBTITLE_FETCH_TIMEOUT", 30.0)
    if subtitle_fetch_timeout <= 0:
        LOGGER.error("SUBTITLE_FETCH_TIMEOUT debe ser mayor que 0.")
        raise SystemExit(1)

    if subtitle_enabled and (
        not opensubtitles_api_key
        or not opensubtitles_username
        or not opensubtitles_password
    ):
        LOGGER.warning(
            "SUBTITLE_ENABLED=true pero faltan OPENSUBTITLES_API_KEY, "
            "OPENSUBTITLES_USERNAME o OPENSUBTITLES_PASSWORD. Subtítulos desactivados."
        )
        subtitle_enabled = False

    if subtitle_enabled and translation_enabled and translation_provider == "deepl" and not deepl_api_key:
        LOGGER.warning("TRANSLATION_PROVIDER=deepl pero falta DEEPL_API_KEY. Traducción desactivada.")
        translation_enabled = False

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
        registry_ttl_seconds=registry_ttl_seconds,
        registry_purge_interval_seconds=registry_purge_interval_seconds,
        registry_data_dir=registry_data_dir,
        rate_limit_calls=rate_limit_calls,
        rate_limit_window_seconds=rate_limit_window_seconds,
        subtitle_enabled=subtitle_enabled,
        opensubtitles_api_key=opensubtitles_api_key,
        opensubtitles_username=opensubtitles_username,
        opensubtitles_password=opensubtitles_password,
        subtitle_languages=subtitle_languages,
        translation_enabled=translation_enabled,
        translation_provider=translation_provider,
        deepl_api_key=deepl_api_key,
        subtitle_fetch_timeout=subtitle_fetch_timeout,
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
