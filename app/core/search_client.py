import logging

from fastapi import Request

from app.api.file_ops.search_docs import semantic_search

logger = logging.getLogger("maxgpt")


async def search_documents(query: str, page: int = 1):
    try:
        # Simulate a minimal internal request object
        class DummyRequest:
            client = type("client", (), {"host": "internal"})

        request = DummyRequest()

        payload = {
            "query": query,
            "page": page,
            "match_count": 10,  # ✅ Get up to 10 chunks per query
        }

        logger.debug(f"🔁 Calling internal semantic_search with payload: {payload}")
        response = await semantic_search(request=request, payload=payload)
        logger.debug(f"📥 Internal response: {response}")

        return response

    except Exception as e:
        logger.exception("🔥 Error during internal semantic search")
        raise
