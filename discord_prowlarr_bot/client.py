from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands

from .config import Config
from .result_delivery import ResultDeliveryService
from .search_utils import (
    extract_text_command,
    get_search_error_message,
    parse_positive_int,
    validate_query,
)
from .views import SearchView

LOGGER = logging.getLogger("discord_prowlarr_bot")


class ProwlarrDiscordClient(discord.Client):
    def __init__(
        self,
        config: Config,
        delivery_service: ResultDeliveryService,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.delivery_service = delivery_service
        self.prowlarr_client = delivery_service.prowlarr_client
        self.tree = app_commands.CommandTree(self)
        self._commands_synced = False

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
    ) -> SearchView:
        return SearchView(
            results=results,
            query=query,
            delivery_service=self.delivery_service,
            author_id=author_id,
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

        sorted_results = sorted(
            results,
            key=lambda result: parse_positive_int(result.get("seeders")),
            reverse=True,
        )

        view = self.create_search_view(
            results=sorted_results,
            query=cleaned_query,
            author_id=message.author.id,
        )
        sent_message = await message.channel.send(embed=view.build_embed(), view=view)
        view.message = sent_message


async def execute_search(
    interaction: discord.Interaction,
    client: ProwlarrDiscordClient,
    query: str,
) -> None:
    LOGGER.info(
        "Slash command ejecutado: user=%s channel=%s query=%r",
        interaction.user.id,
        interaction.channel_id,
        query,
    )

    if interaction.channel_id != client.config.allowed_channel_id:
        await interaction.response.send_message(
            "Este comando solo funciona en el canal designado.",
            ephemeral=True,
        )
        return

    validation_error = validate_query(query)
    if validation_error:
        await interaction.response.send_message(validation_error, ephemeral=True)
        return

    cleaned_query = query.strip()
    await interaction.response.defer(thinking=True)

    try:
        results = await client.prowlarr_client.search(cleaned_query)
    except Exception as exc:
        LOGGER.exception("Error consultando Prowlarr para la query '%s'.", cleaned_query)
        await interaction.followup.send(
            get_search_error_message(exc, client.config.prowlarr_timeout),
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

    view = client.create_search_view(
        results=sorted_results,
        query=cleaned_query,
        author_id=interaction.user.id,
    )
    message = await interaction.followup.send(
        embed=view.build_embed(),
        view=view,
        wait=True,
    )
    view.message = message


def register_commands(client: ProwlarrDiscordClient) -> None:
    @client.tree.command(name="buscar", description="Busca torrents usando Prowlarr")
    @app_commands.describe(query="Texto a buscar")
    async def buscar(interaction: discord.Interaction, query: str) -> None:
        await execute_search(interaction, client, query)

    @client.tree.command(name="piratear", description="Alias de /buscar para buscar torrents")
    @app_commands.describe(query="Texto a buscar")
    async def piratear(interaction: discord.Interaction, query: str) -> None:
        await execute_search(interaction, client, query)
