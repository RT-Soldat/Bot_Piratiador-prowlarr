from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

import discord

from .http_server import TorrentRegistry
from .magnet import slugify
from .prowlarr import ProwlarrClient

from .search_utils import (
    build_compact_magnet_url,
    format_timeout_seconds,
    get_download_url,
    get_magnet_url,
    get_title,
)
from .torrent_builder import TorrentBuilder

LOGGER = logging.getLogger("discord_prowlarr_bot")


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
    ) -> None:
        self.prowlarr_client = prowlarr_client
        self.torrent_builder = torrent_builder
        self.registry = registry
        self.public_base_url = public_base_url
        self.torrent_fetch_timeout = torrent_fetch_timeout
        self.attach_torrent_file = attach_torrent_file

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
        file: discord.File | None = None,
    ) -> None:
        send_kwargs: dict[str, Any] = {"content": content}

        if file is not None:
            send_kwargs["file"] = file

        if view is not None:
            send_kwargs["view"] = view

        await interaction.followup.send(**send_kwargs)

    async def deliver_result(
        self,
        interaction: discord.Interaction,
        result: dict[str, Any],
    ) -> None:
        title = get_title(result)
        filename = f"{slugify(title)}.torrent"
        download_url = get_download_url(result)
        original_magnet_url = get_magnet_url(result)
        magnet_url = build_compact_magnet_url(result, title, original_magnet_url)
        download_resource = None
        progress_message: discord.Message | None = None
        magnet_http_url: str | None = None

        should_resolve_torrent = self.attach_torrent_file
        should_fetch_download = download_url is not None and (
            should_resolve_torrent or magnet_url is None
        )
        if should_fetch_download:
            try:
                download_resource = await self.prowlarr_client.download_resource(download_url)
            except Exception:
                LOGGER.exception("No se pudo descargar el recurso desde Prowlarr para '%s'.", title)

        if magnet_url is None and download_resource is not None and download_resource.magnet_url:
            magnet_url = build_compact_magnet_url(
                result,
                title,
                download_resource.magnet_url,
            )

        torrent_bytes = (
            download_resource.torrent_bytes
            if download_resource is not None and download_resource.torrent_bytes is not None
            else None
        )

        if torrent_bytes is None and magnet_url is not None and should_resolve_torrent:
            progress_message = await interaction.followup.send(
                (
                    "⏳ Buscando metadata del torrent vía DHT. "
                    f"Esto puede tardar hasta {format_timeout_seconds(self.torrent_fetch_timeout)}s..."
                ),
                wait=True,
            )
            try:
                torrent_bytes = await self.torrent_builder.fetch_torrent_from_magnet(
                    magnet_url,
                    timeout=self.torrent_fetch_timeout,
                )
            except Exception:
                LOGGER.exception("Falló la generación local del .torrent para '%s'.", title)
            finally:
                await self.delete_message_quietly(progress_message)

        if self.public_base_url and magnet_url is not None:
            entry_id = await self.registry.register(
                torrent_bytes=torrent_bytes,
                magnet_url=magnet_url,
                filename=filename,
            )
            magnet_http_url = f"{self.public_base_url}/m/{entry_id}"

        if torrent_bytes is not None:
            should_attach_file = self.attach_torrent_file or magnet_url is None
            file = None
            if should_attach_file:
                file = discord.File(
                    fp=BytesIO(torrent_bytes),
                    filename=filename,
                )

            await self.send_result_message(
                interaction=interaction,
                content=self.build_result_content(
                    title=title,
                    raw_magnet_url=None if magnet_http_url else magnet_url,
                ),
                view=build_links_view(magnet_http_url),
                file=file,
            )
            return

        if magnet_url is not None:
            await self.send_result_message(
                interaction=interaction,
                content=self.build_result_content(
                    title=title,
                    raw_magnet_url=None if magnet_http_url else magnet_url,
                ),
                view=build_links_view(magnet_http_url),
            )
            return

        await self.send_result_message(
            interaction=interaction,
            content=f"❌ No se pudo obtener el torrent para **{title}**. Intentá con otro resultado.",
        )
