from fastapi import HTTPException
from app.api.memory_ops.recall_memory import recall_memory
from app.api.memory_ops.run_memory_query import run_memory_query  # ✅ use new helper
# from app.api.file_ops.search_docs import semantic_doc_search  # optional

import asyncio

async def route_query(user_query: str, session_id: str, topic_name: str = None):
    try:
        # Step 1: Attempt recall_memory by topic
        if topic_name:
            recall_data = await recall_memory(topic_name=topic_name, limit=20, offset=0)
            if recall_data.get("messages"):
                return {
                    "source": "recall_memory",
                    "messages": recall_data["messages"],
                    "total_count": recall_data.get("total_count", 0)
                }

        # Step 2: Semantic memory fallback using helper
        memory_data = await run_memory_query(user_query, top_k=5)
        if memory_data:
            return {
                "source": "search_memory",
                "messages": memory_data
            }

        # Step 3: Fallback to document search (optional)
        # doc_data = await semantic_doc_search(query=user_query, page=1)
        # if doc_data.get("matches"):
        #     return {
        #         "source": "document_search",
        #         "matches": doc_data["matches"]
        #     }

        return {
            "source": "none",
            "messages": []
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Router error: {str(e)}")
