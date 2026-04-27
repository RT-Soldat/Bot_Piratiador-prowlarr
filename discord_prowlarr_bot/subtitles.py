from __future__ import annotations

import asyncio
import gzip
import logging
import re
import time
import zipfile
from io import BytesIO
from typing import TYPE_CHECKING, Any, Callable

import httpx

if TYPE_CHECKING:
    from .config import Config

LOGGER = logging.getLogger("discord_prowlarr_bot.subtitles")

_OPENSUBTITLES_API_ROOT = "https://api.opensubtitles.com/api/v1"
_TOKEN_TTL_SECONDS = 23 * 60 * 60
_USER_AGENT = "DiscordProwlarrBot v1.0"
_TRANSLATION_CHUNK_LIMIT = 3500
_TRANSLATION_SEPARATOR = "\n<|DPB_SUBTITLE_BLOCK|>\n"


class SubtitleService:
    def __init__(self, config: Config) -> None:
        self._api_key = config.opensubtitles_api_key
        self._username = config.opensubtitles_username
        self._password = config.opensubtitles_password
        self._languages = list(dict.fromkeys(config.subtitle_languages))
        self._translation_enabled = config.translation_enabled
        self._translation_provider = config.translation_provider
        self._deepl_api_key = config.deepl_api_key
        self._base_url = _OPENSUBTITLES_API_ROOT
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_api_request_at = 0.0

        connect_timeout = min(10.0, config.subtitle_fetch_timeout)
        self._timeout = httpx.Timeout(config.subtitle_fetch_timeout, connect=connect_timeout)
        self._client = httpx.AsyncClient(
            headers={
                "Api-Key": self._api_key,
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )

    async def find_for_title(self, title: str) -> list[tuple[str, bytes]]:
        """
        Busca subtítulos para un título de torrent.

        Retorna una lista de pares (codigo_idioma, srt_bytes). Si no encuentra nada,
        devuelve una lista vacía sin exponer errores al usuario.
        """
        try:
            import guessit

            guessed: dict[str, Any] = dict(guessit.guessit(title))
        except ImportError:
            LOGGER.warning("guessit no está instalado. Subtítulos desactivados.")
            return []
        except Exception:
            LOGGER.exception("guessit no pudo parsear el título '%s'.", title)
            return []

        LOGGER.debug("guessit result for '%s': %s", title, guessed)

        try:
            token = await self._ensure_token()
        except Exception:
            LOGGER.exception("No se pudo autenticar en OpenSubtitles.")
            return []

        results: list[tuple[str, bytes]] = []
        downloaded_by_language: dict[str, bytes] = {}
        primary_language = self._languages[0] if self._languages else "es"

        for language in self._languages:
            try:
                srt_bytes = await self._find_native_subtitle(token, language, guessed, title)
            except Exception:
                LOGGER.exception("Error buscando subtítulos '%s' para '%s'.", language, title)
                continue

            if srt_bytes is None:
                continue

            downloaded_by_language[language] = srt_bytes
            results.append((language, srt_bytes))
            LOGGER.info("Subtítulo '%s' encontrado para '%s'.", language, title)

        if (
            primary_language not in downloaded_by_language
            and self._translation_enabled
            and primary_language != "en"
        ):
            english_srt = downloaded_by_language.get("en")
            if english_srt is None:
                try:
                    english_srt = await self._find_native_subtitle(token, "en", guessed, title)
                except Exception:
                    LOGGER.exception("Error buscando subtítulos en inglés para '%s'.", title)

            if english_srt is not None:
                LOGGER.info(
                    "Subtítulo en inglés encontrado para '%s'. Traduciendo a '%s'...",
                    title,
                    primary_language,
                )
                translated = await asyncio.to_thread(
                    self._translate_srt,
                    english_srt,
                    "en",
                    primary_language,
                )
                results.insert(0, (primary_language, translated))

        return results

    async def close(self) -> None:
        await self._client.aclose()

    async def _find_native_subtitle(
        self,
        token: str,
        language: str,
        guessed: dict[str, Any],
        original_title: str,
    ) -> bytes | None:
        hits = await self._search(token, language, guessed)
        for hit in hits:
            file_id = self._get_file_id(hit)
            if file_id is None:
                continue

            srt_bytes = await self._download_srt(token, file_id)
            if srt_bytes is not None:
                return srt_bytes

        LOGGER.debug("No se encontraron subtítulos '%s' descargables para '%s'.", language, original_title)
        return None

    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token

            response = await self._api_request(
                "POST",
                f"{_OPENSUBTITLES_API_ROOT}/login",
                json={"username": self._username, "password": self._password},
            )
            response.raise_for_status()
            data = response.json()

            token = data.get("token")
            if not isinstance(token, str) or not token:
                raise RuntimeError("OpenSubtitles no devolvió token de autenticación.")

            base_url = data.get("base_url")
            if isinstance(base_url, str) and base_url.strip():
                self._base_url = self._normalize_api_root(base_url)

            self._token = token
            self._token_expires_at = time.time() + _TOKEN_TTL_SECONDS
            return token

    async def _search(
        self,
        token: str,
        language: str,
        guessed: dict[str, Any],
    ) -> list[dict[str, Any]]:
        title = str(guessed.get("title") or "").strip()
        if not title:
            return []

        params: dict[str, Any] = {
            "languages": language,
            "query": title,
        }

        season = _to_int(guessed.get("season"))
        episode = _to_int(guessed.get("episode"))
        year = _to_int(guessed.get("year"))

        if season is not None:
            params["season_number"] = season
        if episode is not None:
            params["episode_number"] = episode
        if year is not None and season is None and episode is None:
            params["year"] = year

        response = await self._api_request(
            "GET",
            f"{self._base_url}/subtitles",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            self._token = None
            LOGGER.warning("OpenSubtitles rechazó el token al buscar '%s'.", title)
            return []
        if response.status_code == 429:
            LOGGER.warning("OpenSubtitles: rate limit alcanzado al buscar '%s'.", title)
            return []

        response.raise_for_status()
        data = response.json()
        hits = data.get("data") or []
        if not isinstance(hits, list):
            return []

        hits = [hit for hit in hits if isinstance(hit, dict)]
        hits.sort(key=self._download_count, reverse=True)
        return hits

    async def _download_srt(self, token: str, file_id: int) -> bytes | None:
        response = await self._api_request(
            "POST",
            f"{self._base_url}/download",
            json={"file_id": file_id, "sub_format": "srt"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code in {406, 429}:
            LOGGER.warning("OpenSubtitles: cuota o rate limit alcanzado al descargar subtítulo.")
            return None
        if response.status_code == 401:
            self._token = None
            LOGGER.warning("OpenSubtitles rechazó el token al descargar subtítulo.")
            return None

        response.raise_for_status()
        link = response.json().get("link")
        if not isinstance(link, str) or not link:
            return None

        async with httpx.AsyncClient(timeout=self._timeout, headers={"User-Agent": _USER_AGENT}) as downloader:
            download = await downloader.get(link)
            download.raise_for_status()
            return _normalize_subtitle_bytes(download.content)

    async def _api_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async with self._request_lock:
            elapsed = time.monotonic() - self._last_api_request_at
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

            response = await self._client.request(method, url, **kwargs)
            self._last_api_request_at = time.monotonic()
            return response

    def _translate_srt(self, srt_bytes: bytes, source_lang: str, target_lang: str) -> bytes:
        try:
            make_translator = self._build_translator_factory(source_lang, target_lang)
        except ImportError:
            LOGGER.warning("deep-translator no está instalado. Traducción omitida.")
            return srt_bytes

        text = _decode_subtitle_text(srt_bytes)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return srt_bytes

        blocks = re.split(r"\n{2,}", normalized)
        text_groups: list[str] = []
        parsed_blocks: list[tuple[list[str], tuple[int, int] | None]] = []

        for block in blocks:
            lines = block.splitlines()
            timestamp_index = _find_timestamp_line(lines)
            if timestamp_index is None or timestamp_index >= len(lines) - 1:
                parsed_blocks.append((lines, None))
                continue

            text_group = "\n".join(lines[timestamp_index + 1 :])
            group_index = len(text_groups)
            text_groups.append(text_group)
            parsed_blocks.append((lines, (timestamp_index, group_index)))

        translated_groups = self._translate_text_groups(text_groups, make_translator)

        translated_blocks: list[str] = []
        for lines, metadata in parsed_blocks:
            if metadata is None:
                translated_blocks.append("\n".join(lines))
                continue

            timestamp_index, group_index = metadata
            translated_text = translated_groups[group_index]
            translated_lines = translated_text.splitlines() or [translated_text]
            translated_blocks.append("\n".join(lines[: timestamp_index + 1] + translated_lines))

        return ("\n\n".join(translated_blocks) + "\n").encode("utf-8")

    def _build_translator_factory(
        self,
        source_lang: str,
        target_lang: str,
    ) -> Callable[[], Any]:
        if self._translation_provider == "deepl":
            from deep_translator import DeeplTranslator

            return lambda: DeeplTranslator(
                api_key=self._deepl_api_key,
                source=source_lang,
                target=target_lang,
            )

        from deep_translator import GoogleTranslator

        return lambda: GoogleTranslator(source=source_lang, target=target_lang)

    def _translate_text_groups(
        self,
        text_groups: list[str],
        make_translator: Callable[[], Any],
    ) -> list[str]:
        translated_groups: list[str] = []
        batch: list[str] = []
        batch_size = 0

        def translate_one(value: str) -> str:
            try:
                translated = make_translator().translate(value)
            except Exception:
                LOGGER.exception("Error traduciendo bloque de SRT.")
                return value

            return str(translated) if translated else value

        def flush_batch() -> None:
            nonlocal batch, batch_size
            if not batch:
                return

            combined = _TRANSLATION_SEPARATOR.join(batch)
            translated = translate_one(combined)
            parts = translated.split(_TRANSLATION_SEPARATOR)
            if len(parts) == len(batch):
                translated_groups.extend(parts)
            else:
                LOGGER.debug("El separador de traducción no se preservó; traduciendo bloques uno a uno.")
                translated_groups.extend(translate_one(item) for item in batch)

            batch = []
            batch_size = 0

        for text_group in text_groups:
            additional_size = len(text_group) + (len(_TRANSLATION_SEPARATOR) if batch else 0)
            if batch and batch_size + additional_size > _TRANSLATION_CHUNK_LIMIT:
                flush_batch()
                additional_size = len(text_group)

            batch.append(text_group)
            batch_size += additional_size

        flush_batch()
        return translated_groups

    def _get_file_id(self, hit: dict[str, Any]) -> int | None:
        attributes = hit.get("attributes")
        if not isinstance(attributes, dict):
            return None

        files = attributes.get("files")
        if not isinstance(files, list):
            return None

        for file_data in files:
            if not isinstance(file_data, dict):
                continue

            file_id = _to_int(file_data.get("file_id"))
            if file_id is not None:
                return file_id

        return None

    def _download_count(self, hit: dict[str, Any]) -> int:
        attributes = hit.get("attributes")
        if not isinstance(attributes, dict):
            return 0

        return _to_int(attributes.get("download_count")) or 0

    def _normalize_api_root(self, base_url: str) -> str:
        cleaned = base_url.strip().rstrip("/")
        if "://" not in cleaned:
            cleaned = f"https://{cleaned}"
        if not cleaned.endswith("/api/v1"):
            cleaned = f"{cleaned}/api/v1"
        return cleaned


def _to_int(value: Any) -> int | None:
    if isinstance(value, list):
        value = value[0] if value else None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_timestamp_line(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if "-->" in line:
            return index
    return None


def _decode_subtitle_text(srt_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return srt_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    return srt_bytes.decode("utf-8", errors="replace")


def _normalize_subtitle_bytes(content: bytes) -> bytes:
    if content.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(content)
        except OSError:
            LOGGER.debug("No se pudo descomprimir subtítulo gzip.", exc_info=True)
            return content

    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = [
                    name
                    for name in archive.namelist()
                    if not name.endswith("/")
                    and name.lower().endswith((".srt", ".sub", ".vtt", ".ass"))
                ]
                if not names:
                    names = [name for name in archive.namelist() if not name.endswith("/")]
                if names:
                    return archive.read(names[0])
        except zipfile.BadZipFile:
            LOGGER.debug("No se pudo leer subtítulo zip.", exc_info=True)

    return content
