from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import os
from datetime import datetime

from app.core.supabase_client import get_supabase_client
from openai import OpenAI
import asyncio
import math
from app.core.prompting import normalize_user_input, build_prompt_scaffold
from app.core.conversation import build_transcript


logger = logging.getLogger(__name__)
router = APIRouter()

# Pydantic models for request body validation
class RankingOptions(BaseModel):
    semantic_weight: Optional[float] = None
    keyword_weight: Optional[float] = None
    top_k: Optional[int] = None

class WebSearchOptions(BaseModel):
    enabled: Optional[bool] = None
    force: Optional[bool] = None
    allow_domains: Optional[List[str]] = None
    block_domains: Optional[List[str]] = None
    max_age_days: Optional[int] = None

class SoftFilters(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    doc_type: Optional[str] = None
    meeting_body: Optional[str] = None
    ordinance_number: Optional[str] = None

class ChatRequestBody(BaseModel):
    workspace_id: str
    chat_id: Optional[str] = None
    input: Optional[str] = None
    instructions: Optional[str] = None
    stream: bool = True
    ranking: Optional[RankingOptions] = None
    web_search: Optional[WebSearchOptions] = None
    soft_filters: Optional[SoftFilters] = None

def feature_enabled(env_var: str, default: bool = False) -> bool:
    """Check if a feature is enabled via environment variable."""
    return os.getenv(env_var, str(default)).lower() == 'true'

async def ensure_workspace_access(workspace_id: str, user_id: str):
    """Verify that the user has access to the workspace."""
    supabase = get_supabase_client()
    try:
        response = supabase.table("workspaces").select("id,user_id,instructions").eq("id", workspace_id).single().execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if response.data['user_id'] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return response.data
    except Exception as e:
        logger.error(f"Workspace access check failed for workspace {workspace_id}: {e}")
        if not isinstance(e, HTTPException):
            raise HTTPException(status_code=500, detail="Workspace lookup failed")
        raise e


@router.post("/chat/respond")
async def chat_respond(body: ChatRequestBody, request: Request):
    """
    Handles streaming chat responses by porting the logic from the Node.js /api/chat/respond route.
    """
    if not feature_enabled("USE_OPENAI_FILE_SEARCH", default=True):
        raise HTTPException(status_code=404, detail="Not Found")

    # For now, we'll assume a mock user. In a real scenario, this would come from an auth dependency.
    # user = await get_current_user(request)
    mock_user = {"user_id": "test-user-id"} # Replace with actual user auth later
    user_id = mock_user["user_id"]

    ws_row = await ensure_workspace_access(body.workspace_id, user_id)

    supabase = get_supabase_client()
    openai = OpenAI()

    # Fetch vector store ID
    try:
        vs_response = supabase.table("workspace_vector_stores").select("vector_store_id").eq("workspace_id", body.workspace_id).maybe_single().execute()
        if not vs_response.data or not vs_response.data.get("vector_store_id"):
            raise HTTPException(status_code=404, detail="Vector store not found for workspace. Create it first.")
        vector_store_id = vs_response.data["vector_store_id"]
    except Exception as e:
        logger.error(f"Failed to fetch vector store for workspace {body.workspace_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch vector store.")

    final_input = normalize_user_input(body.input) if body.input else ""
    final_instructions = body.instructions

    transcript_text = ""
    if body.chat_id:
        logger.info(f"Building transcript for chat_id: {body.chat_id}")
        try:
            transcript_text = build_transcript(body.chat_id, final_input)
        except Exception as e:
            logger.error(f"Transcript build failed for chat_id {body.chat_id}: {e}")
            transcript_text = final_input  # fall back to raw input if transcript fails
    else:
        transcript_text = final_input


    # Build instructions if not provided
    if not final_instructions:
        ws_instr = ws_row.get("instructions", "")
        instruction_parts = [f"Today is {datetime.now().strftime('%Y-%m-%d')}."]
        if ws_instr:
            instruction_parts.append(f"System Instructions:\n{ws_instr}")

        # Soft filter hints
        sf = body.soft_filters
        if sf:
            hints: List[str] = []
            if sf.year:
                hints.append(f"Focus on documents from {sf.year}.")
            if sf.month:
                hints.append(f"Bias toward month {sf.month} when relevant.")
            if sf.doc_type:
                hints.append(f"Prefer doc_type '{sf.doc_type}'.")
            if sf.meeting_body:
                hints.append(f"Meeting body bias: '{sf.meeting_body}'.")
            if sf.ordinance_number:
                hints.append(f"Consider ordinance number '{sf.ordinance_number}'.")
            if hints:
                instruction_parts.append("Retrieval Bias Hints:\n" + "\n".join(f"- {h}" for h in hints))

        instruction_parts.append(
            "Assistant Behavior:\n- Be concise.\n- Cite file names inline immediately after each claim sourced from a file using parentheses.\n- Use human-readable titles; omit extensions."
        )
        instruction_parts.append(
            "Citation Format:\nFor facts from workspace files: ( [Title](/files/{file_id}) ). Do not create a separate Sources section."
        )
        final_instructions = "\n\n".join(instruction_parts)

    # Build tools
    tools = [{"type": "file_search", "vector_store_ids": [vector_store_id]}]
    tool_resources: Dict[str, Any] = {}

    if feature_enabled("USE_OPENAI_WEB_SEARCH", default=True):
        tools.append({"type": "web_search"})

    # Apply ranking options if provided
    if body.ranking:
        ranking_options = {
            "hybrid_search": {},
            "ranker": "auto"
        }
        if body.ranking.semantic_weight is not None:
            ranking_options["hybrid_search"]["embedding_weight"] = body.ranking.semantic_weight
        if body.ranking.keyword_weight is not None:
            ranking_options["hybrid_search"]["text_weight"] = body.ranking.keyword_weight
        # drop empty hybrid_search
        if not ranking_options["hybrid_search"]:
            del ranking_options["hybrid_search"]
        tool_resources.setdefault("file_search", {})["ranking_options"] = ranking_options

    scaffold_version = os.getenv("PROMPT_SCAFFOLD_VERSION", "1.0")
    scaffolded_instructions = build_prompt_scaffold(
        final_instructions,
        web_search=any(t["type"] == "web_search" for t in tools),
        version=scaffold_version
    )

    payload = {
        "model": "gpt-5",
        "instructions": scaffolded_instructions,
        "input": transcript_text,
        "tools": tools,
        **({"tool_resources": tool_resources} if tool_resources else {}),
        "tool_choice": "auto"
    }

    # ----------------------------------------------------------------------------------
    # Shared retry/timeout wrapper for Responses API calls (stream + non-stream)
    # ----------------------------------------------------------------------------------
    MAX_RETRIES = int(os.getenv("RETRY_RETRIES", "3"))
    MIN_MS = int(os.getenv("RETRY_MIN_MS", "300"))
    MAX_MS = int(os.getenv("RETRY_MAX_MS", "2500"))
    JITTER = os.getenv("RETRY_JITTER", "true").lower() == "true"
    STREAM_TIMEOUT_SEC = float(os.getenv("RESPONSES_STREAM_TIMEOUT_SEC", "600"))

    def _compute_backoff(attempt: int) -> float:
        base = MIN_MS * (2 ** attempt)
        base = min(base, MAX_MS)
        if JITTER:
            import random
            base = base * (0.75 + random.random() * 0.5)
        return base / 1000.0

    def _is_retryable_error(err: Exception) -> bool:
        msg = f"{err}".lower()
        # Heuristic: treat rate limits, transient network, and 5xx as retryable
        if any(k in msg for k in ["rate", "timeout", "temporarily", "connection", "502", "503", "504"]):
            return True
        return False

    def _stream_with_retries() -> list[str]:
        chunks: list[str] = []
        attempt = 0
        while attempt <= MAX_RETRIES:
            stream = None
            try:
                # Acquire an event loop reference or stub timing
                try:
                    loop_time = asyncio.get_event_loop().time
                except RuntimeError:
                    # No running loop in this thread (pytest run_in_executor path); fallback to time.time
                    import time as _time
                    loop_time = _time.time  # type: ignore

                stream = openai.responses.stream(**payload)
                start = loop_time()
                for event in stream:
                    now = loop_time()
                    if (now - start) > STREAM_TIMEOUT_SEC:
                        chunks.append("\n[error] stream timeout exceeded")
                        if hasattr(stream, 'close'):  # close if possible
                            try: stream.close()
                            except Exception: pass
                        return chunks
                    et = getattr(event, 'type', '')
                    if et == 'response.output_text.delta' and getattr(event, 'delta', None):
                        chunks.append(event.delta)
                    elif et in ('error', 'response.error'):
                        err_obj = getattr(event, 'error', {}) or {}
                        msg = err_obj.get('message', 'response error') if isinstance(err_obj, dict) else 'response error'
                        chunks.append(f"\n[error] {msg}")
                        return chunks
                return chunks
            except Exception as e:
                if attempt == MAX_RETRIES or not _is_retryable_error(e):
                    chunks.append(f"\n[error] {str(e)}")
                    return chunks
                backoff = _compute_backoff(attempt)
                logger.warning(f"Stream attempt {attempt+1} failed (will retry in {backoff:.2f}s): {e}")
                import time as _time
                _time.sleep(backoff)
                attempt += 1
            finally:
                try:
                    if stream and hasattr(stream, 'close'):
                        stream.close()
                except Exception:
                    pass
        return chunks

    def _create_with_retries() -> Any:
        attempt = 0
        while attempt <= MAX_RETRIES:
            try:
                resp = openai.responses.create(**payload)
                return resp
            except Exception as e:
                if attempt == MAX_RETRIES or not _is_retryable_error(e):
                    raise
                backoff = _compute_backoff(attempt)
                logger.warning(f"Create attempt {attempt+1} failed (will retry in {backoff:.2f}s): {e}")
                import time as _time
                _time.sleep(backoff)
                attempt += 1
        raise RuntimeError("Exhausted retries for responses.create")

    async def stream_generator():
        # run blocking streaming logic in executor to avoid tying up event loop
        loop = asyncio.get_event_loop()
        chunks = await loop.run_in_executor(None, _stream_with_retries)
        for c in chunks:
            yield c

    if body.stream:
        return StreamingResponse(stream_generator(), media_type="text/plain")
    else:
        try:
            resp = _create_with_retries()
            out_text = "".join(
                [o.text for o in getattr(resp, "output", []) if getattr(o, "type", "") == "output_text"]
            )
            return {"id": getattr(resp, "id", None), "text": out_text}
        except Exception as e:
            logger.error(f"Non-streaming response failed: {e}")
            raise HTTPException(status_code=500, detail="Response generation failed")
