import os
import json
import numpy as np
from app.core.supabase_client import create_client
from app.core.openai_client import embed_text

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"

def cosine_similarity(vec1, vec2):
    v1, v2 = np.array(vec1), np.array(vec2)
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

def perform_search(tool_args):
    query = tool_args.get("query")
    project_name = tool_args.get("project_name")
    project_names = tool_args.get("project_names")

    if not query:
        return {"error": "Missing 'query' in tool arguments."}

    try:
        query_embedding = embed_text(query)
    except Exception as e:
        return {"error": f"Embedding failed: {str(e)}"}

    try:
        project_ids = []

        if project_name:
            result = (
                supabase.table("projects")
                .select("id")
                .eq("user_id", USER_ID)
                .eq("name", project_name)
                .maybe_single()
                .execute()
            )
            if not result or not getattr(result, "data", None):
                return {"error": f"No project found with name: {project_name}"}
            project_ids = [result.data["id"]]

        elif project_names:
            result = (
                supabase.table("projects")
                .select("id, name")
                .eq("user_id", USER_ID)
                .in_("name", project_names)
                .execute()
            )
            if not result or not getattr(result, "data", None):
                return {"error": f"No matching projects found."}
            project_ids = [row["id"] for row in result.data]

        base_query = supabase.table("document_chunks").select(
            "content, embedding, chunk_index, file_name"
        )

        if project_ids:
            base_query = base_query.in_("project_id", project_ids)

        response = base_query.execute()

        if getattr(response, "error", None):
            return {"error": f"Supabase query failed: {response.error.message}"}

        rows = response.data

        if not rows:
            return {"message": "No document chunks found for the specified project(s)."}

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

# ✅ Async wrapper for internal use
async def semantic_search(request, payload):
    return perform_search(payload)



