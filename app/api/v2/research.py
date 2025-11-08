from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import os
import json
import asyncio
from datetime import datetime
from openai import OpenAI

from app.core.supabase_client import get_supabase_client
from app.core.prompting import build_prompt_scaffold, normalize_user_input

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------- Models ----------------------
class RankingOptions(BaseModel):
    semantic_weight: Optional[float] = None
    keyword_weight: Optional[float] = None
    top_k: Optional[int] = None

class SoftFilters(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    doc_type: Optional[str] = None
    meeting_body: Optional[str] = None
    ordinance_number: Optional[str] = None

class ResearchRequest(BaseModel):
    workspace_id: str
    question: str
    stream: bool = True
    ranking: Optional[RankingOptions] = None
    soft_filters: Optional[SoftFilters] = None
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None


def feature_enabled(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() == "true"


async def resolve_vector_store_id(supabase, workspace_id: str) -> str:
    try:
        res = supabase.table("workspace_vector_stores").select("vector_store_id").eq("workspace_id", workspace_id).maybe_single().execute()
        if not res.data or not res.data.get("vector_store_id"):
            raise HTTPException(status_code=404, detail="Vector store not found for workspace.")
        return res.data["vector_store_id"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Vector store resolution failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to resolve vector store")


def build_research_instructions(base_ws_instr: str, question: str, sf: Optional[SoftFilters], start_date: Optional[str], end_date: Optional[str]) -> str:
    parts = [f"Today is {datetime.utcnow().strftime('%Y-%m-%d')}."]
    if base_ws_instr:
        parts.append(f"System Instructions:\n{base_ws_instr}")
    parts.append(f"Research Question:\n{question}")

    # Date range hints
    if start_date or end_date:
        dr = []
        if start_date:
            dr.append(f"Start date hint: {start_date}")
        if end_date:
            dr.append(f"End date hint: {end_date}")
        parts.append("Date Range Bias:\n" + "\n".join(f"- {d}" for d in dr))

    if sf:
        hints: List[str] = []
        if sf.year:
            hints.append(f"Prefer documents from year {sf.year}.")
        if sf.month:
            hints.append(f"Bias toward month {sf.month} if relevant.")
        if sf.doc_type:
            hints.append(f"Focus doc_type '{sf.doc_type}'.")
        if sf.meeting_body:
            hints.append(f"Meeting body bias '{sf.meeting_body}'.")
        if sf.ordinance_number:
            hints.append(f"Consider ordinance number '{sf.ordinance_number}'.")
        if hints:
            parts.append("Soft Filter Hints:\n" + "\n".join(f"- {h}" for h in hints))

    parts.append("Behavior:\n- Produce an outline first, then gather concise quotes (≤25) covering breadth, then draft a summary.\n- Cite file titles inline immediately after each quoted fact.\n- Omit a separate Sources section.")
    parts.append("Output Phases:\n1) Outline\n2) Quotes list (JSON lines acceptable)\n3) Draft narrative")
    return "\n\n".join(parts)


@router.post("/research")
async def research(body: ResearchRequest):
    if not feature_enabled("FEATURE_RESEARCH_AGENT", True):
        raise HTTPException(status_code=404, detail="Research feature disabled")

    supabase = get_supabase_client()
    openai = OpenAI()

    # Workspace instructions fetch
    ws_row = supabase.table("workspaces").select("id,instructions,user_id").eq("id", body.workspace_id).single().execute()
    if not ws_row.data:
        raise HTTPException(status_code=404, detail="Workspace not found")

    vector_store_id = await resolve_vector_store_id(supabase, body.workspace_id)

    instructions = build_research_instructions(
        ws_row.data.get("instructions", ""),
        body.question,
        body.soft_filters,
        body.start_date,
        body.end_date,
    )

    scaffolded = build_prompt_scaffold(instructions, web_search=False, version=os.getenv("PROMPT_SCAFFOLD_VERSION", "1.0"))

    tools = [{"type": "file_search", "vector_store_ids": [vector_store_id]}]
    tool_resources: Dict[str, Any] = {}

    if body.ranking:
        ranking_options = {"hybrid_search": {}, "ranker": "auto"}
        if body.ranking.semantic_weight is not None:
            ranking_options["hybrid_search"]["embedding_weight"] = body.ranking.semantic_weight
        if body.ranking.keyword_weight is not None:
            ranking_options["hybrid_search"]["text_weight"] = body.ranking.keyword_weight
        if not ranking_options["hybrid_search"]:
            del ranking_options["hybrid_search"]
        tool_resources.setdefault("file_search", {})["ranking_options"] = ranking_options

    payload = {
        "model": "gpt-5",
        "instructions": scaffolded,
        "input": normalize_user_input(body.question),
        "tools": tools,
        **({"tool_resources": tool_resources} if tool_resources else {}),
        "tool_choice": "auto",
    }

    # Retry/timeout parameters (shared with chat)
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
        if any(k in msg for k in ["rate", "timeout", "temporarily", "connection", "502", "503", "504"]):
            return True
        return False

    def _create_with_retries():
        attempt = 0
        while attempt <= MAX_RETRIES:
            try:
                return openai.responses.create(**payload)
            except Exception as e:
                if attempt == MAX_RETRIES or not _is_retryable_error(e):
                    raise
                backoff = _compute_backoff(attempt)
                logger.warning(f"Research create attempt {attempt+1} failed (retry in {backoff:.2f}s): {e}")
                import time as _time
                _time.sleep(backoff)
                attempt += 1
        raise RuntimeError("Exhausted retries for research create")

    def _stream_with_retries() -> list[tuple[str, dict]]:
        attempt = 0
        while attempt <= MAX_RETRIES:
            stream = None
            events: list[tuple[str, dict]] = []
            try:
                # Similar loop/time fallback as chat endpoint
                try:
                    loop_time = asyncio.get_event_loop().time
                except RuntimeError:
                    import time as _time
                    loop_time = _time.time  # type: ignore
                stream = openai.responses.stream(**payload)
                start = loop_time()
                draft_chunks: list[str] = []
                phase_emitted = set()
                for ev in stream:
                    now = loop_time()
                    if (now - start) > STREAM_TIMEOUT_SEC:
                        events.append(("error", {"message": "stream timeout"}))
                        return events
                    et = getattr(ev, 'type', '')
                    if et == 'response.output_text.delta' and getattr(ev, 'delta', None):
                        if len(draft_chunks) < 5 and 'outline' not in phase_emitted:
                            phase_emitted.add('outline')
                            events.append(('phase', {'phase': 'outline'}))
                        elif len(draft_chunks) >= 5 and 'draft' not in phase_emitted:
                            phase_emitted.add('draft')
                            events.append(('phase', {'phase': 'draft'}))
                        draft_chunks.append(ev.delta)
                        events.append(('draft_chunk', {'text': ev.delta}))
                    elif et in ('error', 'response.error'):
                        err_obj = getattr(ev, 'error', {}) or {}
                        msg = err_obj.get('message', 'response error') if isinstance(err_obj, dict) else 'response error'
                        events.append(('error', {'message': msg}))
                        return events
                events.append(('sources', {'sources': []}))
                events.append(('complete', {'text': ''.join(draft_chunks)}))
                return events
            except Exception as e:
                if attempt == MAX_RETRIES or not _is_retryable_error(e):
                    return [("error", {"message": str(e)})]
                backoff = _compute_backoff(attempt)
                logger.warning(f"Research stream attempt {attempt+1} failed (retry in {backoff:.2f}s): {e}")
                import time as _time
                _time.sleep(backoff)
                attempt += 1
            finally:
                try:
                    if stream and hasattr(stream, 'close'):
                        stream.close()
                except Exception:
                    pass
        return [("error", {"message": "exhausted retries"})]

    if not body.stream:
        try:
            resp = _create_with_retries()
            text = "".join([o.text for o in getattr(resp, "output", []) if getattr(o, "type", "") == "output_text"])
            # Persist minimal report (outline/draft merged) placeholder
            saved = supabase.table("research_reports").insert({
                "workspace_id": body.workspace_id,
                "question": body.question,
                "draft": text,
                "outline": "",
                "quotes": "[]",
                "logs": "{}"
            }).execute()
            return {"id": saved.data[0].get("id") if saved.data else None, "draft": text, "outline": "", "quotes": []}
        except Exception as e:
            logger.error(f"Research non-stream error: {e}")
            raise HTTPException(status_code=500, detail="Research generation failed")

    async def sse_gen():
        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(None, _stream_with_retries)
        for name, data in events:
            yield f"event: {name}\ndata: {json.dumps(data)}\n\n"

    return StreamingResponse(sse_gen(), media_type="text/event-stream")


@router.get("/research/reports")
async def list_research_reports(workspace_id: str):
    supabase = get_supabase_client()
    try:
        res = supabase.table("research_reports").select("id,question,created_at").eq("workspace_id", workspace_id).order("created_at", desc=True).execute()
        return {"reports": res.data or []}
    except Exception as e:
        logger.error(f"List reports failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list reports")


@router.get("/research/reports/{report_id}")
async def get_research_report(report_id: str):
    supabase = get_supabase_client()
    try:
        res = supabase.table("research_reports").select("*").eq("id", report_id).maybe_single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Report not found")
        return {"report": res.data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get report failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch report")
