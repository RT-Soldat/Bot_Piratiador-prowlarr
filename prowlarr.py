from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx


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

    async def search(self, query: str) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self.base_url}/api/v1/search",
            params={"query": query, "type": "search"},
        )
        response.raise_for_status()

        payload = response.json()
        if isinstance(payload, list):
            return payload
        if payload is None:
            return []
        raise ValueError("La respuesta de Prowlarr no es una lista de resultados.")

    async def download_torrent(self, url: str) -> bytes | None:
        try:
            resolved_url = urljoin(f"{self.base_url}/", url)
            response = await self._client.get(resolved_url)
            if response.status_code == httpx.codes.OK:
                return response.content
        except httpx.HTTPError:
            return None
        return None

    async def close(self) -> None:
        await self._client.aclose()
