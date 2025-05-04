from fastapi import HTTPException
import requests

SEARCH_BACKEND = "https://backendsearch-production.up.railway.app"

def route_query(user_query: str, session_id: str, topic_name: str = None):
    try:
        # Step 1: Try recall_memory by topic (if given)
        if topic_name:
            recall_url = f"{SEARCH_BACKEND}/api/recall_memory"
            recall_params = {"topic_name": topic_name, "limit": 20, "offset": 0}
            recall_result = requests.get(recall_url, params=recall_params, timeout=5)
            recall_data = recall_result.json()

            if recall_data.get("messages"):
                return {
                    "source": "recall_memory",
                    "messages": recall_data["messages"],
                    "total_count": recall_data["total_count"]
                }

        # Step 2: Fallback to semantic memory search
        search_memory_url = f"{SEARCH_BACKEND}/api/search_memory"
        memory_result = requests.post(search_memory_url, json={"query": user_query, "top_k": 5}, timeout=5)
        memory_data = memory_result.json()

        if memory_data:
            return {
                "source": "search_memory",
                "messages": memory_data
            }

        # Step 3: Fallback to document search
        doc_search_url = f"{SEARCH_BACKEND}/api/search"
        doc_result = requests.post(doc_search_url, json={"query": user_query, "page": 1}, timeout=5)
        doc_data = doc_result.json()

        if doc_data.get("matches"):
            return {
                "source": "document_search",
                "matches": doc_data["matches"]
            }

        # Nothing found
        return {
            "source": "none",
            "messages": []
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Router error: {str(e)}")
