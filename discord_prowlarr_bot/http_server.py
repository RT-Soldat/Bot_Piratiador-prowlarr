from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Final

from aiohttp import web

LOGGER = logging.getLogger("discord_prowlarr_bot.http_server")
_DEFAULT_TTL_SECONDS: Final[int] = 60 * 60 * 24


@dataclass(slots=True)
class TorrentEntry:
    torrent_bytes: bytes | None
    magnet_url: str | None
    filename: str
    expires_at: float


class TorrentRegistry:
    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._entries: dict[str, TorrentEntry] = {}
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def register(
        self,
        torrent_bytes: bytes | None,
        magnet_url: str | None,
        filename: str,
    ) -> str:
        async with self._lock:
            self._purge_expired_locked()

            entry_id = secrets.token_urlsafe(8)
            while entry_id in self._entries:
                entry_id = secrets.token_urlsafe(8)

            self._entries[entry_id] = TorrentEntry(
                torrent_bytes=torrent_bytes,
                magnet_url=magnet_url,
                filename=filename,
                expires_at=time.time() + self._ttl_seconds,
            )
            return entry_id

    async def get(self, entry_id: str) -> TorrentEntry | None:
        async with self._lock:
            self._purge_expired_locked()
            return self._entries.get(entry_id)

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired_keys = [key for key, value in self._entries.items() if value.expires_at < now]
        for key in expired_keys:
            self._entries.pop(key, None)


async def start_http_server(host: str, port: int, registry: TorrentRegistry) -> web.AppRunner:
    app = web.Application()

    async def serve_torrent(request: web.Request) -> web.Response:
        entry = await registry.get(request.match_info["id"])
        if entry is None or entry.torrent_bytes is None:
            return web.Response(status=404, text="not found")

        return web.Response(
            body=entry.torrent_bytes,
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
