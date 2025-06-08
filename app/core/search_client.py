import logging

from fastapi import Request

from app.api.file_ops.search_docs import semantic_search

logger = logging.getLogger("maxgpt")


async def search_documents(query: str, page: int = 1, match_count: int = 500):
    try:
        # Simulate a minimal internal request object
        class DummyRequest:
            client = type("client", (), {"host": "internal"})

        request = DummyRequest()

        payload = {
            "query": query,
            "page": page,
            "match_count": match_count,  # Increased default to 500, allow override
        }

        logger.debug(f"🔁 Calling internal semantic_search with payload: {payload}")
        response = await semantic_search(request=request, payload=payload)
        logger.debug(f"📥 Internal response: {response}")

        return response

    except Exception as e:
        logger.exception("🔥 Error during internal semantic search")
        raise
