from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

LOGGER = logging.getLogger("discord_prowlarr_bot.progress")
EditCallback = Callable[[str], Awaitable[None]]


class ProgressReporter:
    def __init__(
        self,
        title: str,
        edit_callback: EditCallback,
        *,
        logger: logging.Logger | None = None,
        max_lines: int = 14,
    ) -> None:
        self.title = title
        self._edit_callback = edit_callback
        self._logger = logger or LOGGER
        self._max_lines = max_lines
        self._started_at = time.monotonic()
        self._lines: list[str] = []

    def elapsed(self) -> float:
        return time.monotonic() - self._started_at

    async def mark(self, message: str) -> None:
        line = f"{self.elapsed():5.1f}s {message}"
        self._lines.append(line)
        self._logger.info("%s | %s", self.title, line)
        await self.flush()

    async def flush(self) -> None:
        try:
            await self._edit_callback(self.render())
        except Exception:
            self._logger.debug("No se pudo actualizar el mensaje de progreso.", exc_info=True)

    def render(self, heading: str | None = None) -> str:
        visible_lines = self._lines[-self._max_lines :]
        if not visible_lines:
            visible_lines = [f"{self.elapsed():5.1f}s Iniciando..."]

        header = heading or self.title
        content = f"⏱️ **{_truncate(header, 180)}**\n```text\n"
        content += "\n".join(visible_lines)
        content += "\n```"
        return _truncate(content, 1900)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."
