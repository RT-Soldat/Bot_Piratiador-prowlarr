from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

try:
    import libtorrent as lt
except ModuleNotFoundError:  # pragma: no cover - depende del runtime
    lt = None

LOGGER = logging.getLogger("discord_prowlarr_bot.torrent_builder")
_POLL_INTERVAL: Final[float] = 0.5


class TorrentBuilder:
    def __init__(self, listen_port: int = 6881, save_path: str = "/tmp") -> None:
        self.listen_port = listen_port
        self.save_path = save_path
        self._session: Any | None = None
        self._create_session()

    def _create_session(self) -> None:
        if lt is None:
            LOGGER.warning("libtorrent no está instalado. La generación local de .torrent quedará deshabilitada.")
            return

        try:
            session = lt.session()
            session.apply_settings(
                {
                    "listen_interfaces": f"0.0.0.0:{self.listen_port}",
                    "enable_dht": True,
                    "enable_lsd": True,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                }
            )
            if hasattr(session, "start_dht"):
                session.start_dht()

            for host, port in [
                ("router.bittorrent.com", 6881),
                ("router.utorrent.com", 6881),
                ("dht.transmissionbt.com", 6881),
                ("dht.libtorrent.org", 25401),
            ]:
                session.add_dht_router(host, port)

            self._session = session
        except Exception:
            LOGGER.exception("No se pudo inicializar la sesión de libtorrent.")
            self._session = None

    def _build_add_torrent_params(self, magnet_url: str) -> Any:
        if lt is None:
            raise RuntimeError("libtorrent no está disponible")

        params = lt.parse_magnet_uri(magnet_url)
        upload_flag = getattr(getattr(lt, "torrent_flags", None), "upload_mode", None)

        if isinstance(params, dict):
            params["save_path"] = self.save_path
            if upload_flag is not None:
                params["flags"] = params.get("flags", 0) | upload_flag
            else:
                params["upload_mode"] = True
            return params

        params.save_path = self.save_path
        if upload_flag is not None:
            params.flags |= upload_flag
        elif hasattr(params, "upload_mode"):
            params.upload_mode = True
        return params

    async def fetch_torrent_from_magnet(self, magnet_url: str, timeout: float = 45.0) -> bytes | None:
        if self._session is None:
            LOGGER.warning("La sesión de libtorrent no está disponible. No se puede resolver metadata.")
            return None

        try:
            params = self._build_add_torrent_params(magnet_url)
            handle = self._session.add_torrent(params)
        except Exception:
            LOGGER.exception("No se pudo agregar el magnet a libtorrent.")
            return None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        try:
            while True:
                try:
                    status = handle.status()
                except Exception:
                    LOGGER.exception("No se pudo consultar el estado del torrent en libtorrent.")
                    return None

                if getattr(status, "has_metadata", False):
                    break
                if loop.time() >= deadline:
                    LOGGER.warning("Timeout esperando metadata DHT para %s", magnet_url[:80])
                    return None
                await asyncio.sleep(_POLL_INTERVAL)

            try:
                torrent_info = handle.torrent_file()
                if torrent_info is None:
                    return None

                creator = lt.create_torrent(torrent_info)
                if hasattr(creator, "generate_buf"):
                    try:
                        return bytes(creator.generate_buf())
                    except TypeError:
                        LOGGER.debug(
                            "generate_buf() no devolvió un tipo convertible a bytes. Reintentando con bencode(generate()).",
                            exc_info=True,
                        )

                bencoded = lt.bencode(creator.generate())
                if isinstance(bencoded, bytes):
                    return bencoded
                if isinstance(bencoded, bytearray):
                    return bytes(bencoded)
                if isinstance(bencoded, str):
                    return bencoded.encode()
                return bytes(bencoded)
            except Exception:
                LOGGER.exception("No se pudo convertir la metadata descargada en un archivo .torrent.")
                return None
        finally:
            try:
                self._session.remove_torrent(handle)
            except Exception:
                LOGGER.debug("No se pudo remover el torrent temporal de la sesión.", exc_info=True)

    def close(self) -> None:
        self._session = None
