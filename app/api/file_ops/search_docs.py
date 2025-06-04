import json
import os
import logging
from collections import defaultdict
import sys

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def perform_search(tool_args):
    print("[DEBUG] perform_search called", file=sys.stderr)
    query_embedding = tool_args.get("embedding")
    expected_phrase = tool_args.get("expected_phrase")
    file_name_filter = tool_args.get("file_name_filter")
    collection_filter = tool_args.get("collection_filter")
    description_filter = tool_args.get("description_filter")
    start_date = tool_args.get("start_date")
    end_date = tool_args.get("end_date")
    user_id_filter = tool_args.get("user_id_filter")
    print(f"[DEBUG] tool_args: {json.dumps(tool_args, default=str)[:500]}", file=sys.stderr)
    print(f"[DEBUG] query_embedding: {str(query_embedding)[:100]}", file=sys.stderr)
    print(f"[DEBUG] user_id_filter: {user_id_filter}", file=sys.stderr)
    if not query_embedding:
        print("[DEBUG] No embedding provided", file=sys.stderr)
        logger.error("❌ No embedding provided in tool_args.")
        return {"error": "Embedding must be provided to perform similarity search."}
    if not user_id_filter:
        print("[DEBUG] No user_id provided", file=sys.stderr)
        logger.error("❌ No user_id provided in tool_args.")
        return {"error": "user_id must be provided to perform search."}
    try:
        try:
            total_chunks = supabase.table("document_chunks").select("id").execute().data
            print(f"[DEBUG] total_chunks: {len(total_chunks) if total_chunks else 0}", file=sys.stderr)
            logger.debug(f"📊 Total document_chunks: {len(total_chunks) if total_chunks else 0}")
        except Exception as e:
            print(f"[DEBUG] Could not count document_chunks: {e}", file=sys.stderr)
            logger.warning(f"⚠️ Could not count document_chunks: {e}")
        try:
            user_chunks = supabase.table("document_chunks").select("id").eq("user_id", user_id_filter).execute().data
            print(f"[DEBUG] user_chunks: {len(user_chunks) if user_chunks else 0}", file=sys.stderr)
            logger.debug(f"📊 Chunks for user {user_id_filter}: {len(user_chunks) if user_chunks else 0}")
        except Exception as e:
            print(f"[DEBUG] Could not count user document_chunks: {e}", file=sys.stderr)
            logger.warning(f"⚠️ Could not count user document_chunks: {e}")
        try:
            with_embedding = supabase.table("document_chunks").select("id").not_.is_("openai_embedding", None).execute().data
            print(f"[DEBUG] with_embedding: {len(with_embedding) if with_embedding else 0}", file=sys.stderr)
            logger.debug(f"📊 Chunks with openai_embedding: {len(with_embedding) if with_embedding else 0}")
        except Exception as e:
            print(f"[DEBUG] Could not count chunks with openai_embedding: {e}", file=sys.stderr)
            logger.warning(f"⚠️ Could not count chunks with openai_embedding: {e}")
        rpc_args = {
            "query_embedding": query_embedding,
            "user_id_filter": user_id_filter,
            "file_name_filter": file_name_filter,
            "collection_filter": collection_filter,
            "description_filter": description_filter,
            "start_date": start_date,
            "end_date": end_date,
            "match_threshold": 0.0,  # Patch: return all results regardless of similarity
            "match_count": 2000      # Patch: increase max results
        }
        print(f"[DEBUG] Calling match_documents with args: {json.dumps(rpc_args, default=str)[:500]}", file=sys.stderr)
        logger.debug(f"📤 Calling match_documents with args: {json.dumps(rpc_args, default=str)[:500]}")
        response = supabase.rpc("match_documents", rpc_args).execute()
        print(f"[DEBUG] Supabase RPC response: {str(response)[:500]}", file=sys.stderr)
        logger.debug(f"📥 Supabase RPC response: {str(response)[:500]}")
        if getattr(response, "error", None):
            print(f"[DEBUG] Supabase RPC failed: {getattr(response.error, 'message', str(response.error))}", file=sys.stderr)
            logger.error(f"❌ Supabase RPC failed: {response.error.message}")
            return {"error": f"Supabase RPC failed: {response.error.message}"}
        matches = response.data or []
        print(f"[DEBUG] Matches returned: {len(matches)}", file=sys.stderr)
        logger.debug(f"📊 Matches returned: {len(matches)}")
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        if matches:
            top = matches[0]
            preview = top["content"][:200].replace("\n", " ")
            print(f"[DEBUG] Top match score: {top.get('score')}, preview: {preview}", file=sys.stderr)
            logger.debug(f"🔝 Top match (score {top.get('score')}): {preview}")
        else:
            print("[DEBUG] No matches found", file=sys.stderr)
            logger.debug("⚠️ No matches found.")
        if expected_phrase:
            expected_lower = expected_phrase.lower()
            matches = [x for x in matches if expected_lower not in x["content"].lower()]
            print(f"[DEBUG] {len(matches)} results after omitting phrase: '{expected_phrase}'", file=sys.stderr)
            logger.debug(f"🔍 {len(matches)} results after omitting phrase: '{expected_phrase}'")
        return {"retrieved_chunks": matches}
    except Exception as e:
        print(f"[DEBUG] Error during search: {str(e)}", file=sys.stderr)
        logger.error(f"❌ Error during search: {str(e)}")
        return {"error": f"Error during search: {str(e)}"}


async def semantic_search(request, payload):
    return perform_search(payload)


router = APIRouter()


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    print("[DEBUG] api_search_docs called", file=sys.stderr)
    data = await request.json()
    print(f"[DEBUG] Incoming request data: {json.dumps(data, default=str)[:500]}", file=sys.stderr)
    query = data.get("query") or data.get("user_prompt")
    user_id = data.get("user_id")
    print(f"[DEBUG] query: {query}", file=sys.stderr)
    print(f"[DEBUG] user_id: {user_id}", file=sys.stderr)
    if not query:
        print("[DEBUG] Missing query", file=sys.stderr)
        return JSONResponse({"error": "Missing query"}, status_code=400)
    if not user_id:
        print("[DEBUG] Missing user_id", file=sys.stderr)
        return JSONResponse({"error": "Missing user_id"}, status_code=400)
    try:
        embedding = embed_text(query)
        print(f"[DEBUG] embedding generated: {str(embedding)[:100]}", file=sys.stderr)
    except Exception as e:
        print(f"[DEBUG] Failed to generate embedding: {e}", file=sys.stderr)
        logger.error(f"❌ Failed to generate embedding: {e}")
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)
    tool_args = {
        "embedding": embedding,
        "user_id_filter": user_id,
        "file_name_filter": data.get("file_name_filter"),
        "collection_filter": data.get("collection_filter"),
        "description_filter": data.get("description_filter"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
    }
    print(f"[DEBUG] tool_args for perform_search: {json.dumps(tool_args, default=str)[:500]}", file=sys.stderr)
    result = perform_search(tool_args)
    print(f"[DEBUG] perform_search result: {str(result)[:500]}", file=sys.stderr)
    if "error" in result:
        print(f"[DEBUG] Error in perform_search: {result['error']}", file=sys.stderr)
        return JSONResponse({"error": result["error"]}, status_code=500)
    matches = result.get("retrieved_chunks", [])
    print(f"[DEBUG] matches for response: {len(matches)}", file=sys.stderr)

    # Compose output to include all required fields for frontend compatibility
    retrieved_chunks = []
    for m in matches:
        retrieved_chunks.append({
            # file_items table fields
            "id": m.get("id"),
            "file_id": m.get("file_id"),
            "user_id": m.get("user_id"),
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
            "sharing": m.get("sharing"),
            "content": m.get("content"),
            "tokens": m.get("tokens"),
            "openai_embedding": m.get("openai_embedding"),
            # search score
            "score": m.get("score"),
            # files table fields (as file_metadata)
            "file_metadata": {
                "file_id": m.get("file_id"),
                "folder_id": m.get("folder_id"),
                "created_at": m.get("file_created_at") or m.get("created_at"),
                "updated_at": m.get("file_updated_at") or m.get("updated_at"),
                "sharing": m.get("file_sharing"),
                "description": m.get("description"),
                "file_path": m.get("file_path"),
                "name": m.get("name") or m.get("file_name"),
                "size": m.get("size"),
                "tokens": m.get("file_tokens"),
                "type": m.get("type"),
                "project_id": m.get("project_id"),
                "message_index": m.get("message_index"),
                "timestamp": m.get("timestamp"),
                "topic_id": m.get("topic_id"),
                "chunk_index": m.get("chunk_index"),
                "embedding_json": m.get("embedding_json"),
                "session_id": m.get("session_id"),
                "status": m.get("status"),
                "content": m.get("file_content"),
                "topic_name": m.get("topic_name"),
                "speaker_role": m.get("speaker_role"),
                "ingested": m.get("ingested"),
                "ingested_at": m.get("ingested_at"),
                "uploaded_at": m.get("uploaded_at"),
                "relevant_date": m.get("relevant_date"),
            }
        })

    return JSONResponse({"retrieved_chunks": retrieved_chunks})


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
