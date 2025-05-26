import json
import os
import logging
from collections import defaultdict
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from app.core.supabase_client import create_client

# Logger and client setup
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)
client = OpenAI()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

router = APIRouter()

# Request model
class ExtendedSearchRequest(BaseModel):
    query: str
    user_id: str
    project_name: Optional[str] = None
    expected_phrase: Optional[str] = None
    document_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    limit: int = 3000

@router.post("/search_docs")
async def search_docs_api(request: ExtendedSearchRequest):
    try:
        embedding_response = client.embeddings.create(
            input=request.query,
            model="text-embedding-3-small"
        )
        embedding = embedding_response.data[0].embedding
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")

    # Validate and parse date range
    date_range = {}
    if request.start_date and request.end_date:
        try:
            start = datetime.fromisoformat(request.start_date)
            end = datetime.fromisoformat(request.end_date)
            date_range["start"] = start.isoformat()
            date_range["end"] = end.isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    tool_args = {
        "embedding": embedding,
        "user_id": request.user_id,
        "project_name": request.project_name,
        "expected_phrase": request.expected_phrase,
        "document_type": request.document_type,
        "date_range": date_range,
        "limit": request.limit,
    }

    results = perform_search(tool_args)
    return results


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
                .eq("user_id", tool_args["user_id"])
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
                .eq("user_id", tool_args["user_id"])
                .in_("name", project_names)
                .execute()
            )
            if not result or not getattr(result, "data", None):
                logger.error("❌ No matching projects found.")
                return {"error": f"No matching projects found."}
            project_ids = [row["id"] for row in result.data]

        logger.debug(f"✅ Project IDs found: {project_ids}")

        rpc_args = {
            "query_embedding": query_embedding,
            "match_threshold": 0.9,
            "match_count": limit,
            "user_id_filter": tool_args["user_id"],
            "project_ids_filter": project_ids if project_ids else None,
            "file_type_filter": tool_args.get("document_type"),
            "start_date_filter": tool_args.get("date_range", {}).get("start"),
            "end_date_filter": tool_args.get("date_range", {}).get("end"),
        }

        response = supabase.rpc("match_documents", rpc_args).execute()
        logger.debug(f"🧠 match_documents returned: {len(response.data or [])} chunks")

        if getattr(response, "error", None):
            logger.error(f"❌ Supabase RPC failed: {response.error.message}")
            return {"error": f"Supabase RPC failed: {response.error.message}"}

        matches = response.data or []

        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        logger.debug("🔽 Matches sorted by descending score")

        if matches:
            top = matches[0]
            preview = top["content"][:200].replace("\n", " ")
            logger.debug(f"🔝 Top match (score {top.get('score')}): {preview}")

        grouped = defaultdict(list)
        for match in matches:
            file_id = match.get("file_id")
            if file_id:
                grouped[file_id].append(match)

        top_file_id = matches[0].get("file_id") if matches else None
        if top_file_id and top_file_id in grouped:
            matches = grouped[top_file_id]
            logger.debug(f"📂 Returning {len(matches)} chunks from top file_id: {top_file_id}")

        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
            logger.debug(f"🔍 {len(matches)} results after omitting phrase: '{expected_phrase}'")

        return {"results": matches}

    except Exception as e:
        logger.error(f"❌ Error during search: {str(e)}")
        return {"error": f"Error during search: {str(e)}"}

# ✅ Optional async wrapper
async def semantic_search(request, payload):
    return perform_search(payload)
