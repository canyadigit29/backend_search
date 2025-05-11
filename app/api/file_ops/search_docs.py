\import os
import json
import numpy as np
from app.core.supabase_client import create_client
from app.core.openai_client import embed_text

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def cosine_similarity(vec1, vec2):
    v1, v2 = np.array(vec1), np.array(vec2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

def perform_search(tool_args):
    query = tool_args.get("query")
    project_id = tool_args.get("project_id")

    if not query:
        return {"error": "Missing 'query' in tool arguments."}

    try:
        query_embedding = embed_text(query)
    except Exception as e:
        return {"error": f"Embedding failed: {str(e)}"}

    try:
        base_query = supabase.table("document_chunks").select("content, embedding, chunk_index, file_name")
        if project_id:
            base_query = base_query.eq("project_id", project_id)

        response = base_query.execute()

        if getattr(response, "error", None):
            return {"error": f"Supabase query failed: {response.error.message}"}

        rows = response.data

        if not rows:
            return {"message": "No document chunks found in the specified project."}

        scored = [
            {
                "content": row["content"],
                "score": cosine_similarity(query_embedding, row["embedding"]),
                "file_name": row.get("file_name", "(unknown file)"),
                "chunk_index": row.get("chunk_index", 0)
            }
            for row in rows
        ]

        top_matches = sorted(scored, key=lambda x: x["score"], reverse=True)[:15]

        return {"results": top_matches}

    except Exception as e:
        return {"error": f"Error during search: {str(e)}"}
