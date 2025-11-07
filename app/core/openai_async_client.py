import asyncio
import os
import httpx
import logging
from typing import Optional, List, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)

class AsyncOpenAIClient:
    def __init__(self, timeout: float = 15.0, retries: int = 3):
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "OpenAI-Beta": "assistants=v2",
        }

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Optional[Exception] = None
        backoff = 0.5
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(self.retries):
                try:
                    full_url = f"{self.base_url}{url}" if not url.startswith("http") else url
                    resp = await client.request(method, full_url, headers=self._headers(), **kwargs)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        last_exc = Exception(f"OpenAI API returned {resp.status_code}: {resp.text}")
                        await asyncio.sleep(backoff)
                        backoff = min(4.0, backoff * 2)
                        continue
                    resp.raise_for_status()
                    return resp
                except httpx.RequestError as e:
                    last_exc = e
                    await asyncio.sleep(backoff)
                    backoff = min(4.0, backoff * 2)
                    continue
        raise Exception(f"OpenAI HTTP request failed after {self.retries} retries: {last_exc}")

    async def list_vector_store_files(self, vector_store_id: str) -> List[Dict]:
        url = f"/v1/vector_stores/{vector_store_id}/files?limit=100"
        resp = await self._request("GET", url)
        return (resp.json() or {}).get("data", [])

    async def delete_vector_store_attachment(self, vector_store_id: str, file_id: str) -> bool:
        url = f"/v1/vector_stores/{vector_store_id}/files/{file_id}"
        try:
            await self._request("DELETE", url)
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return True # Already deleted
            raise

    async def delete_file(self, file_id: str) -> bool:
        url = f"/v1/files/{file_id}"
        try:
            await self._request("DELETE", url)
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return True # Already deleted
            raise

    async def retrieve_file(self, file_id: str) -> Optional[Dict]:
        url = f"/v1/files/{file_id}"
        try:
            resp = await self._request("GET", url)
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
