
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

def search_memory(user_id: str, query: str, match_threshold: float = 0.75, match_count: int = 5):
    try:
        query_embedding = embed_text(query)
        query_embedding_array = query_embedding if isinstance(query_embedding, list) else query_embedding.tolist()

        response = supabase.rpc(
            "match_memory",
            {
                "query_embedding": query_embedding_array,
                "match_threshold": match_threshold,
                "match_count": match_count,
                "user_id": user_id
            }
        ).execute()

        if not response.data:
            return []

        return response.data
    except Exception as e:
        print(f"❌ Error searching memory: {str(e)}")
        return []
