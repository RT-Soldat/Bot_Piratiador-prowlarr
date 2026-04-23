from __future__ import annotations

import logging
import time
from typing import Any

import discord
from discord import app_commands

from .config import Config
from .rate_limit import RateLimiter
from .result_delivery import ResultDeliveryService
from .search_utils import (
    CATEGORY_CHOICES,
    apply_filters,
    dedupe_by_info_hash,
    extract_text_command,
    get_search_error_message,
    validate_query,
)
from .torrent_builder import TorrentBuilder
from .views import SearchView

LOGGER = logging.getLogger("discord_prowlarr_bot")


class ProwlarrDiscordClient(discord.Client):
    def __init__(
        self,
        config: Config,
        delivery_service: ResultDeliveryService,
        torrent_builder: TorrentBuilder,
        rate_limiter: RateLimiter,
        on_ready_once: Any = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.delivery_service = delivery_service
        self.prowlarr_client = delivery_service.prowlarr_client
        self.torrent_builder = torrent_builder
        self.rate_limiter = rate_limiter
        self.tree = app_commands.CommandTree(self)
        self._commands_synced = False
        self._on_ready_once = on_ready_once
        self.started_at = time.monotonic()

    async def on_ready(self) -> None:
        if not self._commands_synced:
            global_synced = await self.tree.sync()
            LOGGER.info("Se sincronizaron %s slash commands globales.", len(global_synced))

            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                guild_synced = await self.tree.sync(guild=guild)
                LOGGER.info(
                    "Se sincronizaron %s slash commands en el servidor %s (%s).",
                    len(guild_synced),
                    guild.name,
                    guild.id,
                )

            self._commands_synced = True

            if self._on_ready_once is not None:
                try:
                    await self._on_ready_once()
                except Exception:
                    LOGGER.exception("Error en el hook on_ready_once.")

        LOGGER.info("Bot conectado como %s", self.user)

    async def close(self) -> None:
        self.delivery_service.close()
        await self.prowlarr_client.close()
        await super().close()

    def create_search_view(
        self,
        results: list[dict[str, Any]],
        query: str,
        author_id: int | None,
        ephemeral: bool = False,
    ) -> SearchView:
        return SearchView(
            results=results,
            query=query,
            delivery_service=self.delivery_service,
            author_id=author_id,
            ephemeral=ephemeral,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content:
            return

        parsed_command = extract_text_command(message.content)
        if parsed_command is None:
            return

        command_name, raw_query = parsed_command
        LOGGER.info(
            "Comando por texto detectado: /%s user=%s channel=%s",
            command_name,
            message.author.id,
            message.channel.id,
        )

        if message.channel.id != self.config.allowed_channel_id:
            await message.reply(
                "Este comando solo funciona en el canal designado.",
                mention_author=False,
            )
            return

        if not self.rate_limiter.allow(message.author.id):
            await message.reply(
                "Demasiadas búsquedas seguidas. Esperá unos segundos.",
                mention_author=False,
            )
            return

        validation_error = validate_query(raw_query)
        if validation_error:
            await message.reply(validation_error, mention_author=False)
            return

        cleaned_query = raw_query.strip()

        async with message.channel.typing():
            try:
                results = await self.prowlarr_client.search(cleaned_query)
            except Exception as exc:
                LOGGER.exception("Error consultando Prowlarr para la query '%s'.", cleaned_query)
                await message.reply(
                    get_search_error_message(exc, self.config.prowlarr_timeout),
                    mention_author=False,
                )
                return

        if not results:
            await message.channel.send(f"No se encontraron resultados para: {cleaned_query}")
            return

        deduped = dedupe_by_info_hash(results)

        view = self.create_search_view(
            results=deduped,
            query=cleaned_query,
            author_id=message.author.id,
        )
        sent_message = await message.channel.send(embed=view.build_embed(), view=view)
        view.message = sent_message


async def execute_search(
    interaction: discord.Interaction,
    client: ProwlarrDiscordClient,
    query: str,
    categoria: str | None = None,
    min_seeders: int = 0,
    año: int | None = None,
    privada: bool = False,
) -> None:
    LOGGER.info(
        "Slash command ejecutado: user=%s channel=%s query=%r categoria=%s min_seeders=%s año=%s privada=%s",
        interaction.user.id,
        interaction.channel_id,
        query,
        categoria,
        min_seeders,
        año,
        privada,
    )

    if interaction.channel_id != client.config.allowed_channel_id:
        await interaction.response.send_message(
            "Este comando solo funciona en el canal designado.",
            ephemeral=True,
        )
        return

    if not client.rate_limiter.allow(interaction.user.id):
        await interaction.response.send_message(
            "Demasiadas búsquedas seguidas. Esperá unos segundos.",
            ephemeral=True,
        )
        return

    validation_error = validate_query(query)
    if validation_error:
        await interaction.response.send_message(validation_error, ephemeral=True)
        return

    cleaned_query = query.strip()
    await interaction.response.defer(thinking=True, ephemeral=privada)

    categories = CATEGORY_CHOICES.get(categoria) if categoria else None

    try:
        results = await client.prowlarr_client.search(cleaned_query, categories=categories)
    except Exception as exc:
        LOGGER.exception("Error consultando Prowlarr para la query '%s'.", cleaned_query)
        await interaction.followup.send(
            get_search_error_message(exc, client.config.prowlarr_timeout),
            ephemeral=True,
        )
        return

    filtered = apply_filters(results, min_seeders=min_seeders, year=año)
    deduped = dedupe_by_info_hash(filtered)

    if not deduped:
        await interaction.followup.send(
            f"No se encontraron resultados para: {cleaned_query}",
            ephemeral=privada,
        )
        return

    view = client.create_search_view(
        results=deduped,
        query=cleaned_query,
        author_id=interaction.user.id,
        ephemeral=privada,
    )
    message = await interaction.followup.send(
        embed=view.build_embed(),
        view=view,
        wait=True,
        ephemeral=privada,
    )
    view.message = message


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def register_commands(client: ProwlarrDiscordClient) -> None:
    categoria_choices = [
        app_commands.Choice(name="Películas", value="peliculas"),
        app_commands.Choice(name="Series", value="series"),
        app_commands.Choice(name="Música", value="musica"),
        app_commands.Choice(name="Software", value="software"),
        app_commands.Choice(name="Libros", value="libros"),
    ]

    @client.tree.command(name="buscar", description="Busca torrents usando Prowlarr")
    @app_commands.describe(
        query="Texto a buscar",
        categoria="Filtrar por categoría",
        min_seeders="Mínimo de seeders",
        año="Filtrar por año en el título",
        privada="Mostrar los resultados solo a vos",
    )
    @app_commands.choices(categoria=categoria_choices)
    async def buscar(
        interaction: discord.Interaction,
        query: str,
        categoria: app_commands.Choice[str] | None = None,
        min_seeders: int = 0,
        año: int | None = None,
        privada: bool = False,
    ) -> None:
        await execute_search(
            interaction,
            client,
            query,
            categoria=categoria.value if categoria else None,
            min_seeders=min_seeders,
            año=año,
            privada=privada,
        )

    @client.tree.command(name="piratear", description="Alias de /buscar para buscar torrents")
    @app_commands.describe(
        query="Texto a buscar",
        categoria="Filtrar por categoría",
        min_seeders="Mínimo de seeders",
        año="Filtrar por año en el título",
        privada="Mostrar los resultados solo a vos",
    )
    @app_commands.choices(categoria=categoria_choices)
    async def piratear(
        interaction: discord.Interaction,
        query: str,
        categoria: app_commands.Choice[str] | None = None,
        min_seeders: int = 0,
        año: int | None = None,
        privada: bool = False,
    ) -> None:
        await execute_search(
            interaction,
            client,
            query,
            categoria=categoria.value if categoria else None,
            min_seeders=min_seeders,
            año=año,
            privada=privada,
        )

    @client.tree.command(name="status", description="Muestra el estado del bot y de Prowlarr")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        prowlarr_ok = await client.prowlarr_client.ping()
        libtorrent_ok = client.torrent_builder._session is not None
        uptime_seconds = time.monotonic() - client.started_at
        registry_count = await client.delivery_service.registry.count()

        embed = discord.Embed(
            title="Estado del bot",
            color=discord.Color.green() if prowlarr_ok else discord.Color.red(),
        )
        embed.add_field(name="Prowlarr", value="✅ OK" if prowlarr_ok else "❌ no responde")
        embed.add_field(name="libtorrent", value="✅ activo" if libtorrent_ok else "⚠️ deshabilitado")
        embed.add_field(name="Uptime", value=_format_uptime(uptime_seconds))
        embed.add_field(name="Entradas activas", value=str(registry_count))
        embed.add_field(
            name="Links TTL",
            value=f"{client.delivery_service.registry.ttl_seconds // 3600}h",
        )

        await interaction.followup.send(embed=embed, ephemeral=True)
