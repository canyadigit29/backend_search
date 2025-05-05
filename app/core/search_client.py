import httpx
import logging
from app.core.config import settings

logger = logging.getLogger("maxgpt")

async def search_documents(query: str, page: int = 1):
    try:
        url = f"{settings.SEARCH_BACKEND_URL}/api/search"
        payload = {
            "query": query,
            "page": page,
            "match_count": 10  # ✅ Get up to 10 chunks per query
        }

        logger.debug(f"🌐 Sending POST to: {url}")
        logger.debug(f"📤 Payload: {payload}")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=payload,
                timeout=30.0
            )
            logger.debug(f"📥 Raw response status: {response.status_code}")
            logger.debug(f"📥 Raw response content: {response.text}")

            response.raise_for_status()
            return response.json()

    except Exception as e:
        logger.exception("🔥 Error during semantic search request")
        raise
