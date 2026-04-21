from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from typing import Any

import discord
from discord import app_commands
from dotenv import load_dotenv

from magnet import PUBLIC_TRACKERS, build_magnet, format_size, slugify
from prowlarr import ProwlarrClient

LOGGER = logging.getLogger("discord_prowlarr_bot")
RESULTS_PER_PAGE = 10


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


@dataclass(slots=True)
class Config:
    discord_token: str
    allowed_channel_id: int
    prowlarr_url: str
    prowlarr_api_key: str
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

    if missing:
        LOGGER.error("Faltan variables de entorno obligatorias: %s", ", ".join(missing))
        raise SystemExit(1)

    return Config(
        discord_token=discord_token,
        allowed_channel_id=allowed_channel_id,
        prowlarr_url=prowlarr_url.rstrip("/"),
        prowlarr_api_key=prowlarr_api_key,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )


class SearchSelect(discord.ui.Select):
    def __init__(self, view: "SearchView") -> None:
        self.search_view = view
        super().__init__(
            placeholder="Elegí un resultado de esta página",
            min_values=1,
            max_values=1,
            options=view.build_options(),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_index = int(self.values[0])
        await self.search_view.handle_selection(interaction, selected_index)


class SearchView(discord.ui.View):
    def __init__(
        self,
        results: list[dict[str, Any]],
        query: str,
        prowlarr_client: ProwlarrClient,
        author_id: int | None = None,
    ) -> None:
        super().__init__(timeout=600)
        self.results = results
        self.query = query
        self.prowlarr_client = prowlarr_client
        self.author_id = author_id
        self.current_page = 0
        self.message: discord.Message | None = None

        self.previous_button = discord.ui.Button(
            label="⬅️ Anterior",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.previous_button.callback = self.on_previous
        self.add_item(self.previous_button)

        self.next_button = discord.ui.Button(
            label="➡️ Siguiente",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        self.next_button.callback = self.on_next
        self.add_item(self.next_button)

        self.result_select: SearchSelect | None = None
        self.refresh_components()

    @property
    def total_pages(self) -> int:
        return max(1, ceil(len(self.results) / RESULTS_PER_PAGE))

    def page_bounds(self) -> tuple[int, int]:
        start = self.current_page * RESULTS_PER_PAGE
        end = start + RESULTS_PER_PAGE
        return start, end

    def build_options(self) -> list[discord.SelectOption]:
        start, end = self.page_bounds()
        options: list[discord.SelectOption] = []

        for index, result in enumerate(self.results[start:end], start=start):
            title = get_title(result)
            seeders = parse_positive_int(result.get("seeders"))
            size_human = format_size(result.get("size"))
            description = truncate(
                f"{size_human} · {seeders} seeders · {get_indexer_name(result)}",
                100,
            )

            options.append(
                discord.SelectOption(
                    label=truncate(title, 100),
                    description=description,
                    value=str(index),
                )
            )

        return options

    def refresh_components(self) -> None:
        self.previous_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

        if self.result_select is not None:
            self.remove_item(self.result_select)

        self.result_select = SearchSelect(self)
        self.add_item(self.result_select)

    def build_embed(self) -> discord.Embed:
        start, end = self.page_bounds()
        lines: list[str] = []

        for display_index, result in enumerate(self.results[start:end], start=start + 1):
            title = truncate(get_title(result), 200)
            size_human = format_size(result.get("size"))
            seeders = parse_positive_int(result.get("seeders"))
            indexer = get_indexer_name(result)

            lines.append(
                f"`{display_index}.` **{title}**\n"
                f"📦 {size_human} · 🌱 {seeders} · 🧲 {indexer}"
            )

        embed = discord.Embed(
            title=truncate(f"Resultados para: {self.query}", 256),
            description="\n\n".join(lines),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"Página {self.current_page + 1}/{self.total_pages} · {len(self.results)} resultados"
        )
        return embed

    async def on_previous(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_next(self, interaction: discord.Interaction) -> None:
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        self.refresh_components()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def handle_selection(self, interaction: discord.Interaction, selected_index: int) -> None:
        if selected_index < 0 or selected_index >= len(self.results):
            await interaction.response.send_message(
                "❌ Ese resultado ya no está disponible. Ejecutá la búsqueda de nuevo.",
                ephemeral=True,
            )
            return

        result = self.results[selected_index]
        await interaction.response.defer(thinking=True)
        await self.deliver_result(interaction, result)

    async def deliver_result(
        self,
        interaction: discord.Interaction,
        result: dict[str, Any],
    ) -> None:
        title = get_title(result)

        magnet_url = get_magnet_url(result)
        if magnet_url:
            await interaction.followup.send(f"🧲 **{title}**\n{magnet_url}")
            return

        info_hash = get_info_hash(result)
        if info_hash:
            magnet = build_magnet(info_hash, title, PUBLIC_TRACKERS)
            await interaction.followup.send(f"🧲 **{title}**\n{magnet}")
            return

        download_url = get_download_url(result)
        if download_url:
            torrent_bytes = await self.prowlarr_client.download_torrent(download_url)
            if torrent_bytes is not None:
                filename = f"{slugify(title)}.torrent"
                file = discord.File(fp=BytesIO(torrent_bytes), filename=filename)
                await interaction.followup.send(
                    content=(
                        f"📎 **{title}**\n"
                        "No hay magnet disponible. Adjunto el archivo .torrent:"
                    ),
                    file=file,
                )
                return

        await interaction.followup.send(
            f"❌ No se pudo obtener el torrent para **{title}**. Intentá con otro resultado."
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

        if self.message is None:
            return

        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            LOGGER.warning("No se pudo deshabilitar la vista expirada para '%s'.", self.query)


class ProwlarrDiscordClient(discord.Client):
    def __init__(self, config: Config, prowlarr_client: ProwlarrClient) -> None:
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.prowlarr_client = prowlarr_client
        self.tree = app_commands.CommandTree(self)
        self._commands_synced = False

    async def on_ready(self) -> None:
        if not self._commands_synced:
            synced = await self.tree.sync()
            LOGGER.info("Se sincronizaron %s slash commands.", len(synced))
            self._commands_synced = True
        LOGGER.info("Bot conectado como %s", self.user)

    async def close(self) -> None:
        await self.prowlarr_client.close()
        await super().close()


def register_commands(client: ProwlarrDiscordClient) -> None:
    async def handle_search_command(interaction: discord.Interaction, query: str) -> None:
        if interaction.channel_id != client.config.allowed_channel_id:
            await interaction.response.send_message(
                "Este comando solo funciona en el canal designado.",
                ephemeral=True,
            )
            return

        cleaned_query = query.strip()
        if not cleaned_query:
            await interaction.response.send_message(
                "La búsqueda no puede estar vacía.",
                ephemeral=True,
            )
            return

        if len(cleaned_query) > 200:
            await interaction.response.send_message(
                "La búsqueda no puede superar los 200 caracteres.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            results = await client.prowlarr_client.search(cleaned_query)
        except Exception:
            LOGGER.exception("Error consultando Prowlarr para la query '%s'.", cleaned_query)
            await interaction.followup.send(
                "Error consultando Prowlarr. Revisá los logs del bot.",
                ephemeral=True,
            )
            return

        if not results:
            await interaction.followup.send(f"No se encontraron resultados para: {cleaned_query}")
            return

        sorted_results = sorted(
            results,
            key=lambda result: parse_positive_int(result.get("seeders")),
            reverse=True,
        )

        view = SearchView(
            results=sorted_results,
            query=cleaned_query,
            prowlarr_client=client.prowlarr_client,
            author_id=interaction.user.id,
        )
        message = await interaction.followup.send(
            embed=view.build_embed(),
            view=view,
            wait=True,
        )
        view.message = message

    @client.tree.command(name="buscar", description="Busca torrents usando Prowlarr")
    @app_commands.describe(query="Texto a buscar")
    async def buscar(interaction: discord.Interaction, query: str) -> None:
        await handle_search_command(interaction, query)

    @client.tree.command(name="piratear", description="Alias de /buscar para buscar torrents")
    @app_commands.describe(query="Texto a buscar")
    async def piratear(interaction: discord.Interaction, query: str) -> None:
        await handle_search_command(interaction, query)


async def async_main() -> None:
    load_dotenv()
    configure_logging()
    config = load_config()

    prowlarr_client = ProwlarrClient(
        base_url=config.prowlarr_url,
        api_key=config.prowlarr_api_key,
        timeout=30.0,
    )
    client = ProwlarrDiscordClient(config=config, prowlarr_client=prowlarr_client)
    register_commands(client)

    try:
        await client.start(config.discord_token)
    finally:
        if not client.is_closed():
            await client.close()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("Bot detenido manualmente.")


if __name__ == "__main__":
    main()
