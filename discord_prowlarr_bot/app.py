from __future__ import annotations

import asyncio
import logging

import discord
from dotenv import load_dotenv

from .client import ProwlarrDiscordClient, register_commands
from .config import configure_logging, load_config
from .http_server import ChannelRegistry, TorrentRegistry, start_http_server
from .prowlarr import ProwlarrClient
from .rate_limit import RateLimiter
from .result_delivery import ResultDeliveryService
from .subtitles import SubtitleService
from .torrent_builder import TorrentBuilder

LOGGER = logging.getLogger("discord_prowlarr_bot")


async def async_main() -> None:
    load_dotenv()
    config = load_config()
    configure_logging(config)

    prowlarr_client = ProwlarrClient(
        base_url=config.prowlarr_url,
        api_key=config.prowlarr_api_key,
        timeout=config.prowlarr_timeout,
    )
    torrent_builder = TorrentBuilder(listen_port=config.libtorrent_listen_port)
    registry = TorrentRegistry(
        data_dir=config.registry_data_dir,
        ttl_seconds=config.registry_ttl_seconds,
    )
    channel_registry = ChannelRegistry(data_dir=config.registry_data_dir)
    http_runner = await start_http_server(
        config.http_listen_host,
        config.http_listen_port,
        registry,
    )

    if config.public_base_url:
        LOGGER.info("Links HTTP habilitados con base pública %s", config.public_base_url)
    else:
        LOGGER.info("BOT_PUBLIC_BASE_URL está vacío. El bot solo enviará adjuntos o magnet en texto plano.")

    subtitle_service: SubtitleService | None = None
    if config.subtitle_enabled:
        subtitle_service = SubtitleService(config)
        LOGGER.info("Servicio de subtítulos habilitado (idiomas: %s).", ", ".join(config.subtitle_languages))

    delivery_service = ResultDeliveryService(
        prowlarr_client=prowlarr_client,
        torrent_builder=torrent_builder,
        registry=registry,
        public_base_url=config.public_base_url,
        torrent_fetch_timeout=config.torrent_fetch_timeout,
        attach_torrent_file=config.attach_torrent_file,
        subtitle_service=subtitle_service,
        subtitle_fetch_timeout=config.subtitle_fetch_timeout,
    )
    rate_limiter = RateLimiter(
        max_calls=config.rate_limit_calls,
        window_seconds=config.rate_limit_window_seconds,
    )

    client: ProwlarrDiscordClient

    async def on_ready_once() -> None:
        async def message_deleter(channel_id: int, message_id: int) -> None:
            channel = client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await client.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.debug("Canal %s no accesible para borrar mensaje %s.", channel_id, message_id)
                    return

            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                return
            except discord.HTTPException:
                LOGGER.debug(
                    "No se pudo borrar el mensaje %s en canal %s.",
                    message_id,
                    channel_id,
                    exc_info=True,
                )

        registry.start_purge_task(
            interval_seconds=config.registry_purge_interval_seconds,
            message_deleter=message_deleter,
        )

    client = ProwlarrDiscordClient(
        config=config,
        delivery_service=delivery_service,
        torrent_builder=torrent_builder,
        rate_limiter=rate_limiter,
        channel_registry=channel_registry,
        on_ready_once=on_ready_once,
    )
    register_commands(client)

    try:
        await client.start(config.discord_token)
    finally:
        await registry.stop_purge_task()
        if not client.is_closed():
            await client.close()
        if subtitle_service is not None:
            await subtitle_service.close()
        await http_runner.cleanup()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("Bot detenido manualmente.")
