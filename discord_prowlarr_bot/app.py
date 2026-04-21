from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from .client import ProwlarrDiscordClient, register_commands
from .config import configure_logging, load_config
from .http_server import TorrentRegistry, start_http_server
from .prowlarr import ProwlarrClient
from .result_delivery import ResultDeliveryService
from .torrent_builder import TorrentBuilder

LOGGER = logging.getLogger("discord_prowlarr_bot")


async def async_main() -> None:
    load_dotenv()
    configure_logging()
    config = load_config()

    prowlarr_client = ProwlarrClient(
        base_url=config.prowlarr_url,
        api_key=config.prowlarr_api_key,
        timeout=config.prowlarr_timeout,
    )
    torrent_builder = TorrentBuilder(listen_port=config.libtorrent_listen_port)
    registry = TorrentRegistry()
    http_runner = await start_http_server(
        config.http_listen_host,
        config.http_listen_port,
        registry,
    )

    if config.public_base_url:
        LOGGER.info("Links HTTP habilitados con base pública %s", config.public_base_url)
    else:
        LOGGER.info("BOT_PUBLIC_BASE_URL está vacío. El bot solo enviará adjuntos o magnet en texto plano.")

    delivery_service = ResultDeliveryService(
        prowlarr_client=prowlarr_client,
        torrent_builder=torrent_builder,
        registry=registry,
        public_base_url=config.public_base_url,
        torrent_fetch_timeout=config.torrent_fetch_timeout,
        attach_torrent_file=config.attach_torrent_file,
    )
    client = ProwlarrDiscordClient(
        config=config,
        delivery_service=delivery_service,
    )
    register_commands(client)

    try:
        await client.start(config.discord_token)
    finally:
        if not client.is_closed():
            await client.close()
        await http_runner.cleanup()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        LOGGER.info("Bot detenido manualmente.")
