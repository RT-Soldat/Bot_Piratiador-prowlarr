from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable, Final

from aiohttp import web

LOGGER = logging.getLogger("discord_prowlarr_bot.http_server")
_DEFAULT_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 7

MessageDeleter = Callable[[int, int], Awaitable[None]]


@dataclass(slots=True)
class TorrentEntry:
    magnet_url: str | None
    filename: str
    expires_at: float
    channel_id: int | None = None
    message_id: int | None = None
    files_message_id: int | None = None


class TorrentRegistry:
    def __init__(
        self,
        data_dir: Path,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._entries: dict[str, TorrentEntry] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._data_dir = data_dir
        self._purge_task: asyncio.Task[None] | None = None
        self._message_deleter: MessageDeleter | None = None

        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_existing()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def _meta_path(self, entry_id: str) -> Path:
        return self._data_dir / f"{entry_id}.json"

    def _torrent_path(self, entry_id: str) -> Path:
        return self._data_dir / f"{entry_id}.torrent"

    def _load_existing(self) -> None:
        if not self._data_dir.exists():
            return

        now = time.time()
        for meta_file in self._data_dir.glob("*.json"):
            entry_id = meta_file.stem
            try:
                data = json.loads(meta_file.read_text())
                entry = TorrentEntry(
                    magnet_url=data.get("magnet_url"),
                    filename=data.get("filename", "torrent.torrent"),
                    expires_at=float(data.get("expires_at", 0)),
                    channel_id=data.get("channel_id"),
                    message_id=data.get("message_id"),
                    files_message_id=data.get("files_message_id"),
                )
            except (OSError, ValueError, json.JSONDecodeError):
                LOGGER.warning("Entry corrupta en %s, borrando.", meta_file)
                meta_file.unlink(missing_ok=True)
                self._torrent_path(entry_id).unlink(missing_ok=True)
                continue

            if entry.expires_at < now:
                meta_file.unlink(missing_ok=True)
                self._torrent_path(entry_id).unlink(missing_ok=True)
                continue

            self._entries[entry_id] = entry

        LOGGER.info("Registry rehidratado con %s entradas desde %s", len(self._entries), self._data_dir)

    async def register(
        self,
        torrent_bytes: bytes | None,
        magnet_url: str | None,
        filename: str,
    ) -> str:
        async with self._lock:
            entry_id = secrets.token_urlsafe(8)
            while entry_id in self._entries:
                entry_id = secrets.token_urlsafe(8)

            entry = TorrentEntry(
                magnet_url=magnet_url,
                filename=filename,
                expires_at=time.time() + self._ttl_seconds,
            )
            self._entries[entry_id] = entry

            await asyncio.to_thread(self._persist_entry, entry_id, entry, torrent_bytes)
            return entry_id

    async def get(self, entry_id: str) -> TorrentEntry | None:
        async with self._lock:
            return self._entries.get(entry_id)

    async def get_torrent_bytes(self, entry_id: str) -> bytes | None:
        return await asyncio.to_thread(self._read_torrent_bytes, entry_id)

    async def attach_message_reference(
        self,
        entry_id: str,
        channel_id: int,
        message_id: int,
        files_message_id: int | None = None,
    ) -> None:
        async with self._lock:
            entry = self._entries.get(entry_id)
            if entry is None:
                return

            entry.channel_id = channel_id
            entry.message_id = message_id
            entry.files_message_id = files_message_id
            await asyncio.to_thread(self._write_meta, entry_id, entry)

    async def count(self) -> int:
        async with self._lock:
            return len(self._entries)

    async def purge_channel(self, channel_id: int) -> int:
        async with self._lock:
            to_remove = [
                key for key, entry in self._entries.items()
                if entry.channel_id == channel_id
            ]
            for key in to_remove:
                self._entries.pop(key, None)
                self._meta_path(key).unlink(missing_ok=True)
                self._torrent_path(key).unlink(missing_ok=True)
            return len(to_remove)

    def _persist_entry(self, entry_id: str, entry: TorrentEntry, torrent_bytes: bytes | None) -> None:
        self._write_meta(entry_id, entry)
        if torrent_bytes is not None:
            self._torrent_path(entry_id).write_bytes(torrent_bytes)

    def _write_meta(self, entry_id: str, entry: TorrentEntry) -> None:
        self._meta_path(entry_id).write_text(json.dumps(asdict(entry)))

    def _read_torrent_bytes(self, entry_id: str) -> bytes | None:
        path = self._torrent_path(entry_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def start_purge_task(
        self,
        interval_seconds: int,
        message_deleter: MessageDeleter | None = None,
    ) -> None:
        if self._purge_task is not None and not self._purge_task.done():
            return

        self._message_deleter = message_deleter
        self._purge_task = asyncio.create_task(self._purge_loop(interval_seconds))

    async def stop_purge_task(self) -> None:
        if self._purge_task is None:
            return

        self._purge_task.cancel()
        try:
            await self._purge_task
        except (asyncio.CancelledError, Exception):
            pass
        self._purge_task = None

    async def _purge_loop(self, interval_seconds: int) -> None:
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self._purge_expired()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Error en el purge loop del registry.")

    async def _purge_expired(self) -> None:
        now = time.time()
        expired: list[tuple[str, TorrentEntry]] = []

        async with self._lock:
            for key, entry in list(self._entries.items()):
                if entry.expires_at < now:
                    expired.append((key, entry))
                    self._entries.pop(key, None)

            for entry_id, _ in expired:
                self._meta_path(entry_id).unlink(missing_ok=True)
                self._torrent_path(entry_id).unlink(missing_ok=True)

        if not expired:
            return

        LOGGER.info("Purga: %s entradas vencidas.", len(expired))

        deleter = self._message_deleter
        if deleter is None:
            return

        for _, entry in expired:
            if entry.channel_id is None:
                continue
            for msg_id in filter(None, [entry.message_id, entry.files_message_id]):
                try:
                    await deleter(entry.channel_id, msg_id)
                except Exception:
                    LOGGER.exception(
                        "No se pudo borrar el mensaje de Discord channel=%s message=%s.",
                        entry.channel_id,
                        msg_id,
                    )


class ChannelRegistry:
    """Persiste los channel IDs configurados dinámicamente vía /configurar-canal."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "channels.json"
        self._channel_ids: set[int] = self._load()

    def _load(self) -> set[int]:
        if not self._path.exists():
            return set()
        try:
            data = json.loads(self._path.read_text())
            return {int(x) for x in data.get("channel_ids", [])}
        except (OSError, ValueError, json.JSONDecodeError):
            LOGGER.warning("No se pudo cargar el registro de canales desde %s.", self._path)
            return set()

    def _save(self) -> None:
        self._path.write_text(json.dumps({"channel_ids": list(self._channel_ids)}))

    def add(self, channel_id: int) -> bool:
        if channel_id in self._channel_ids:
            return False
        self._channel_ids.add(channel_id)
        self._save()
        return True

    def remove(self, channel_id: int) -> bool:
        if channel_id not in self._channel_ids:
            return False
        self._channel_ids.discard(channel_id)
        self._save()
        return True

    def __contains__(self, channel_id: int) -> bool:
        return channel_id in self._channel_ids

    def all_ids(self) -> list[int]:
        return list(self._channel_ids)


async def start_http_server(host: str, port: int, registry: TorrentRegistry) -> web.AppRunner:
    app = web.Application()

    async def serve_torrent(request: web.Request) -> web.Response:
        entry_id = request.match_info["id"]
        entry = await registry.get(entry_id)
        if entry is None:
            return web.Response(status=404, text="not found")

        torrent_bytes = await registry.get_torrent_bytes(entry_id)
        if torrent_bytes is None:
            return web.Response(status=404, text="not found")

        return web.Response(
            body=torrent_bytes,
            headers={
                "Content-Type": "application/x-bittorrent",
                "Content-Disposition": f'attachment; filename="{entry.filename}"',
            },
        )

    async def serve_magnet(request: web.Request) -> web.Response:
        entry = await registry.get(request.match_info["id"])
        if entry is None or entry.magnet_url is None:
            return web.Response(status=404, text="not found")

        raise web.HTTPFound(entry.magnet_url)

    async def healthcheck(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/t/{id}", serve_torrent)
    app.router.add_get("/m/{id}", serve_magnet)
    app.router.add_get("/health", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    LOGGER.info("HTTP server escuchando en %s:%s", host, port)
    return runner
