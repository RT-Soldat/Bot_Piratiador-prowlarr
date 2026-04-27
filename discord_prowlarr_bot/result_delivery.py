from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from .http_server import TorrentRegistry
from .magnet import slugify
from .progress import ProgressReporter
from .prowlarr import ProwlarrClient

from .search_utils import (
    build_compact_magnet_url,
    get_download_url,
    get_magnet_url,
    get_title,
)
from .torrent_builder import TorrentBuilder

if TYPE_CHECKING:
    from .subtitles import SubtitleService

LOGGER = logging.getLogger("discord_prowlarr_bot")
_LAST_RESULT_CAP = 200
_MAX_DISCORD_FILES = 10
_FINAL_PROGRESS_KEYWORDS = (
    "finalizada",
    "finalizado",
    "falló",
    "fallo",
    "timeout",
    "listos",
    "listas",
    "disponible",
    "encontraron",
)
FilePayload = tuple[str, bytes]


def build_links_view(
    magnet_http_url: str | None = None,
) -> discord.ui.View | None:
    if magnet_http_url is None:
        return None

    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label="Abrir magnet",
            style=discord.ButtonStyle.link,
            url=magnet_http_url,
        )
    )
    return view


class ResultDeliveryService:
    def __init__(
        self,
        prowlarr_client: ProwlarrClient,
        torrent_builder: TorrentBuilder,
        registry: TorrentRegistry,
        public_base_url: str,
        torrent_fetch_timeout: float,
        attach_torrent_file: bool,
        subtitle_service: SubtitleService | None = None,
        subtitle_fetch_timeout: float = 30.0,
    ) -> None:
        self.prowlarr_client = prowlarr_client
        self.torrent_builder = torrent_builder
        self.registry = registry
        self.public_base_url = public_base_url
        self.torrent_fetch_timeout = torrent_fetch_timeout
        self.attach_torrent_file = attach_torrent_file
        self.subtitle_service = subtitle_service
        self._subtitle_fetch_timeout = subtitle_fetch_timeout
        self._last_result_messages: OrderedDict[tuple[int, int], discord.Message] = OrderedDict()

    def close(self) -> None:
        self.torrent_builder.close()

    def build_result_content(
        self,
        title: str,
        raw_magnet_url: str | None,
    ) -> str:
        lines = [f"🧲 **{title}**"]

        if raw_magnet_url:
            lines.append("<" + raw_magnet_url + ">")

        return "\n".join(lines)

    async def delete_message_quietly(self, message: discord.Message | None) -> None:
        if message is None:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            LOGGER.debug("No se pudo borrar el mensaje de progreso.", exc_info=True)

    async def send_result_message(
        self,
        interaction: discord.Interaction,
        content: str,
        view: discord.ui.View | None = None,
        files: list[discord.File] | None = None,
        ephemeral: bool = False,
    ) -> discord.Message:
        send_kwargs: dict[str, Any] = {"content": content, "ephemeral": ephemeral}

        if files:
            send_kwargs["files"] = files

        if view is not None:
            send_kwargs["view"] = view

        return await interaction.followup.send(wait=True, **send_kwargs)

    async def replace_last_result_message(
        self,
        interaction: discord.Interaction,
        author_id: int | None,
        new_message: discord.Message,
    ) -> None:
        channel_id = interaction.channel_id
        if channel_id is None:
            return

        owner_id = author_id or interaction.user.id
        key = (channel_id, owner_id)
        previous_message = self._last_result_messages.pop(key, None)
        self._last_result_messages[key] = new_message

        while len(self._last_result_messages) > _LAST_RESULT_CAP:
            self._last_result_messages.popitem(last=False)

        if previous_message is None or previous_message.id == new_message.id:
            return

        await self.delete_message_quietly(previous_message)

    async def deliver_result(
        self,
        interaction: discord.Interaction,
        result: dict[str, Any],
        *,
        author_id: int | None = None,
        search_message: discord.Message | None = None,
        progress_message: discord.Message | None = None,
        search_timing_lines: tuple[str, ...] = (),
        ephemeral: bool = False,
    ) -> None:
        title = get_title(result)
        if progress_message is None:
            progress_message = await interaction.followup.send(
                f"⏱️ **Preparando entrega: {title}**",
                wait=True,
                ephemeral=ephemeral,
            )

        progress = ProgressReporter(
            f"Entrega: {title}",
            lambda content: progress_message.edit(content=content, embed=None, view=None),
            logger=LOGGER,
        )

        await progress.mark("Selección recibida")

        subtitle_task = self._start_subtitle_task(title)
        if subtitle_task is not None:
            await progress.mark("Búsqueda de subtítulos iniciada")
        else:
            await progress.mark("Subtítulos desactivados")

        filename = f"{slugify(title)}.torrent"
        download_url = get_download_url(result)
        original_magnet_url = get_magnet_url(result)
        magnet_url = build_compact_magnet_url(result, title, original_magnet_url)
        download_resource = None
        magnet_http_url: str | None = None
        entry_id: str | None = None

        await progress.mark("Datos del resultado parseados")

        should_resolve_torrent = self.attach_torrent_file
        should_fetch_download = download_url is not None and (
            should_resolve_torrent or magnet_url is None
        )
        if should_fetch_download:
            await progress.mark("Descarga directa desde Prowlarr iniciada")
            try:
                download_resource = await self.prowlarr_client.download_resource(download_url)
                await progress.mark("Descarga directa desde Prowlarr finalizada")
            except Exception:
                LOGGER.exception("No se pudo descargar el recurso desde Prowlarr para '%s'.", title)
                await progress.mark("Descarga directa desde Prowlarr falló")
        elif magnet_url is not None:
            await progress.mark("Magnet disponible desde el resultado")

        if magnet_url is None and download_resource is not None and download_resource.magnet_url:
            magnet_url = build_compact_magnet_url(
                result,
                title,
                download_resource.magnet_url,
            )
            await progress.mark("Magnet extraído desde el recurso descargado")

        torrent_bytes = (
            download_resource.torrent_bytes
            if download_resource is not None and download_resource.torrent_bytes is not None
            else None
        )

        if torrent_bytes is None and magnet_url is not None and should_resolve_torrent:
            await progress.mark("Resolución de metadata vía DHT iniciada")
            try:
                torrent_bytes = await self.torrent_builder.fetch_torrent_from_magnet(
                    magnet_url,
                    timeout=self.torrent_fetch_timeout,
                )
                await progress.mark("Resolución de metadata vía DHT finalizada")
            except Exception:
                LOGGER.exception("Falló la generación local del .torrent para '%s'.", title)
                await progress.mark("Resolución de metadata vía DHT falló")

        if self.public_base_url and magnet_url is not None:
            await progress.mark("Registro del link HTTP iniciado")
            entry_id = await self.registry.register(
                torrent_bytes=torrent_bytes,
                magnet_url=magnet_url,
                filename=filename,
            )
            magnet_http_url = f"{self.public_base_url}/m/{entry_id}"
            await progress.mark("Registro del link HTTP finalizado")

        sent_message: discord.Message | None = None

        result_delivered = torrent_bytes is not None or magnet_url is not None

        if result_delivered:
            subtitles = await self._collect_subtitles(subtitle_task, title, progress)
            file_payloads: list[FilePayload] = []
            subtitle_languages: list[str] = []
            should_attach_file = self.attach_torrent_file or magnet_url is None
            if torrent_bytes is not None and should_attach_file:
                file_payloads.append((filename, torrent_bytes))

            for language, srt_bytes in subtitles:
                if len(file_payloads) >= _MAX_DISCORD_FILES:
                    LOGGER.warning("Se omitió subtítulo '%s' para '%s': límite de adjuntos.", language, title)
                    continue

                subtitle_languages.append(language)
                file_payloads.append((f"{slugify(title)}.{language}.srt", srt_bytes))

            content = self.build_result_content(
                title=title,
                raw_magnet_url=None if magnet_http_url else magnet_url,
            )
            if subtitle_languages:
                content += "\n" + f"📄 Subtítulos adjuntos: {', '.join(subtitle_languages)}"
            content = self._append_progress_summary(content, progress, search_timing_lines)

            sent_message = await self._edit_or_send_final_message(
                interaction=interaction,
                progress_message=progress_message,
                content=content,
                view=build_links_view(magnet_http_url),
                file_payloads=file_payloads,
                ephemeral=ephemeral,
            )

        else:
            self._cancel_subtitle_task(subtitle_task)
            sent_message = await self._edit_or_send_final_message(
                interaction=interaction,
                progress_message=progress_message,
                content=f"❌ No se pudo obtener el torrent para **{title}**. Intentá con otro resultado.",
                view=None,
                file_payloads=[],
                ephemeral=ephemeral,
            )

        if entry_id is not None and sent_message is not None and not ephemeral:
            channel_id = sent_message.channel.id if sent_message.channel else interaction.channel_id
            if channel_id is not None:
                await self.registry.attach_message_reference(
                    entry_id,
                    channel_id,
                    sent_message.id,
                )

        if not ephemeral:
            await self.replace_last_result_message(interaction, author_id, sent_message)

        if search_message is not None and (
            sent_message is None or search_message.id != sent_message.id
        ):
            await self.delete_message_quietly(search_message)

    def _start_subtitle_task(
        self,
        title: str,
    ) -> asyncio.Task[list[tuple[str, bytes]]] | None:
        if self.subtitle_service is None:
            return None

        return asyncio.create_task(
            asyncio.wait_for(
                self.subtitle_service.find_for_title(title),
                timeout=self._subtitle_fetch_timeout,
            )
        )

    async def _collect_subtitles(
        self,
        subtitle_task: asyncio.Task[list[tuple[str, bytes]]] | None,
        title: str,
        progress: ProgressReporter,
    ) -> list[tuple[str, bytes]]:
        if subtitle_task is None:
            return []

        await progress.mark("Esperando resultado de subtítulos")
        try:
            subtitles = await subtitle_task
        except asyncio.TimeoutError:
            LOGGER.warning("Timeout buscando subtítulos para '%s'.", title)
            await progress.mark("Timeout buscando subtítulos")
            return []
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Error buscando subtítulos para '%s'.", title)
            await progress.mark("Búsqueda de subtítulos falló")
            return []

        if subtitles:
            await progress.mark("Subtítulos listos: " + ", ".join(language for language, _ in subtitles))
        else:
            await progress.mark("No se encontraron subtítulos")

        return subtitles

    def _cancel_subtitle_task(self, subtitle_task: asyncio.Task[list[tuple[str, bytes]]] | None) -> None:
        if subtitle_task is not None and not subtitle_task.done():
            subtitle_task.cancel()

    async def _edit_or_send_final_message(
        self,
        interaction: discord.Interaction,
        progress_message: discord.Message | None,
        content: str,
        view: discord.ui.View | None,
        file_payloads: list[FilePayload],
        ephemeral: bool,
    ) -> discord.Message:
        if progress_message is not None:
            try:
                return await progress_message.edit(
                    content=content,
                    view=view,
                    attachments=self._build_files(file_payloads),
                )
            except (TypeError, discord.HTTPException):
                LOGGER.debug("No se pudo convertir el progreso en mensaje final.", exc_info=True)

        if not ephemeral and interaction.channel is not None:
            sent_message = await interaction.channel.send(
                content=content,
                view=view,
                files=self._build_files(file_payloads) or None,
            )
        else:
            sent_message = await self.send_result_message(
                interaction=interaction,
                content=content,
                view=view,
                files=self._build_files(file_payloads),
                ephemeral=ephemeral,
            )

        await self.delete_message_quietly(progress_message)
        return sent_message

    def _build_files(self, file_payloads: list[FilePayload]) -> list[discord.File]:
        return [
            discord.File(fp=BytesIO(file_bytes), filename=filename)
            for filename, file_bytes in file_payloads
        ]

    def _append_progress_summary(
        self,
        content: str,
        progress: ProgressReporter,
        search_timing_lines: tuple[str, ...],
    ) -> str:
        summary_parts: list[str] = []
        if search_timing_lines:
            summary_parts.append(
                progress.render_lines(search_timing_lines, heading="Tiempos de búsqueda")
            )
        summary_parts.append(
            progress.render_filtered(_FINAL_PROGRESS_KEYWORDS, heading="Tiempos de entrega")
        )
        summary = "\n".join(summary_parts)
        full_content = content + "\n\n" + summary
        if len(full_content) <= 1900:
            return full_content

        available = 1900 - len(content) - 2
        if available < 80:
            return content

        return content + "\n\n" + summary[: available - 3] + "..."
