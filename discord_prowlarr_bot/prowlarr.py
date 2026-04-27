from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

LOGGER = logging.getLogger("discord_prowlarr_bot.prowlarr")


@dataclass(slots=True)
class DownloadResource:
    torrent_bytes: bytes | None = None
    magnet_url: str | None = None


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            follow_redirects=True,
            headers={"X-Api-Key": api_key},
        )

    async def search(
        self,
        query: str,
        categories: list[int] | None = None,
        limit: int | None = None,
        indexer_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [("query", query), ("type", "search")]
        if categories:
            params.extend(("categories", str(cat)) for cat in categories)
        if limit is not None and limit > 0:
            params.append(("limit", str(limit)))
        if indexer_ids:
            params.extend(("indexerIds", str(indexer_id)) for indexer_id in indexer_ids)

        response = await self._client.get(
            f"{self.base_url}/api/v1/search",
            params=params,
        )
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            return payload
        if payload is None:
            return []
        raise ValueError("La respuesta de Prowlarr no es una lista de resultados.")

    async def list_indexers(self) -> list[dict[str, Any]]:
        response = await self._client.get(f"{self.base_url}/api/v1/indexer")
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            return payload
        if payload is None:
            return []
        raise ValueError("La respuesta de Prowlarr no es una lista de indexers.")

    async def ping(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/api/v1/system/status")
        except httpx.HTTPError:
            return False
        return response.status_code == httpx.codes.OK

    def _resolve_url(self, url: str) -> str:
        return urljoin(f"{self.base_url}/", url)

    def _sanitize_download_url(self, url: str) -> str:
        resolved_url = self._resolve_url(url)
        parsed = urlparse(resolved_url)
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() != "apikey"
        ]
        return urlunparse(parsed._replace(query=urlencode(filtered_query, doseq=True)))

    async def _fetch_download_resource(
        self,
        url: str,
        redirects_remaining: int = 5,
    ) -> DownloadResource | None:
        if redirects_remaining < 0:
            return None

        try:
            response = await self._client.get(url, follow_redirects=False)
            if response.status_code == httpx.codes.OK:
                return DownloadResource(torrent_bytes=response.content)

            if response.status_code in {
                httpx.codes.MOVED_PERMANENTLY,
                httpx.codes.FOUND,
                httpx.codes.SEE_OTHER,
                httpx.codes.TEMPORARY_REDIRECT,
                httpx.codes.PERMANENT_REDIRECT,
            }:
                location = (response.headers.get("Location") or "").strip()
                if not location:
                    return None

                if location.startswith("magnet:?"):
                    return DownloadResource(magnet_url=location)

                next_url = self._resolve_url(location)
                return await self._fetch_download_resource(
                    next_url,
                    redirects_remaining=redirects_remaining - 1,
                )
        except httpx.HTTPError as exc:
            LOGGER.warning("Fallo fetch de %s: %s", url, exc)
            return None

        return None

    async def download_resource(self, url: str) -> DownloadResource | None:
        sanitized_url = self._sanitize_download_url(url)
        return await self._fetch_download_resource(sanitized_url)

    async def close(self) -> None:
        await self._client.aclose()
