
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text
from datetime import datetime

async def run_memory_query(query: str, top_k: int = 5):
    try:
        query_embedding = embed_text(query)

        raw_matches = supabase.rpc("match_memory_by_embedding", {
            "query_embedding": query_embedding,
            "match_threshold": 0.7,
            "match_count": 15
        }).execute().data or []

        def rank(entry):
            try:
                ts = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
                days_old = (datetime.utcnow() - ts).days
                recency_bonus = 0.02 if days_old <= 7 else 0.01 if days_old <= 30 else 0
                return entry.get("similarity", 0) + recency_bonus
            except:
                return entry.get("similarity", 0)

        ranked = sorted(raw_matches, key=rank, reverse=True)
        return ranked[:top_k]

    except Exception as e:
        print(f"❌ run_memory_query failed: {str(e)}")
        return []
