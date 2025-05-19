import json
import os
import logging

from app.core.supabase_client import create_client

# Initialize logger
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"

def perform_search(tool_args):
    project_name = tool_args.get("project_name")
    project_names = tool_args.get("project_names")
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")
    limit = tool_args.get("limit", 3000)

    logger.debug(f"🔍 Searching for documents with the following parameters:")
    logger.debug(f"Project Name: {project_name}, Project Names: {project_names}")
    logger.debug(f"🔑 Received embedding: {query_embedding[:5]}...")
    logger.debug(f"🔎 Filtering by omission: {expected_phrase}")

    if not query_embedding:
        logger.error("❌ No embedding provided in tool_args.")
        return {"error": "Embedding must be provided to perform similarity search."}

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
                logger.error(f"❌ No project found with name: {project_name}")
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
                logger.error("❌ No matching projects found.")
                return {"error": f"No matching projects found."}
            project_ids = [row["id"] for row in result.data]

        logger.debug(f"✅ Project IDs found: {project_ids}")

        # Use Supabase RPC to perform pgvector search
        rpc_args = {
            "query_embedding": query_embedding,
            "match_threshold": 0.3,
            "match_count": limit,
            "user_id_filter": USER_ID,
            "project_ids_filter": project_ids or None
        }

        response = supabase.rpc("match_documents", rpc_args).execute()

        if getattr(response, "error", None):
            logger.error(f"❌ Supabase RPC failed: {response.error.message}")
            return {"error": f"Supabase RPC failed: {response.error.message}"}

        matches = response.data or []
        logger.debug(f"✅ Retrieved {len(matches)} matches from pgvector RPC.")

        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
            logger.debug(f"🔍 {len(matches)} results after omitting phrase: '{expected_phrase}'")

        return {"results": matches}

    except Exception as e:
        logger.error(f"❌ Error during search: {str(e)}")
        return {"error": f"Error during search: {str(e)}"}

# ✅ Async wrapper for internal use
async def semantic_search(request, payload):
    return perform_search(payload)
