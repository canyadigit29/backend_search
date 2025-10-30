import json
import os
from collections import defaultdict
import sys
import statistics
import httpx
import asyncio
import logging
import textwrap

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion, stream_chat_completion
from app.core.token_utils import trim_texts_to_token_limit
from sentence_transformers import CrossEncoder

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import time
from app.core.logger import log_info, log_error, request_id_var
from app.core.config import settings


# Load the Cross-Encoder model once when the module is loaded
# This is a lightweight model optimized for performance
cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
logger = logging.getLogger(__name__)



SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
SUPABASE_BUCKET_ID = "files"  # Hardcoded bucket name for consistency
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)


def _parse_inline_or_terms(text: str) -> list[str]:
    """
    Extract OR-separated terms from a single query string, e.g.,
    "foo OR bar OR baz" -> ["foo", "bar", "baz"].
    Case-insensitive on the OR separator. Trims quotes and whitespace.
    If no OR is present, returns an empty list (caller may choose to ignore).
    """
    if not isinstance(text, str) or not text:
        return []
    import re
    # Split on standalone OR tokens (case-insensitive); preserve phrases
    parts = re.split(r"\s+or\s+", text, flags=re.IGNORECASE)
    # If only one part, no inline OR detected
    if len(parts) <= 1:
        return []
    def _strip_quotes(s: str) -> str:
        s = (s or "").strip()
        # Remove wrapping quotes if present
        if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
            return s[1:-1].strip()
        return s
    terms = [_strip_quotes(p) for p in parts]
    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for t in terms:
        if not t:
            continue
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        uniq.append(t)
    return uniq

def _decide_weighting(user_prompt: str, or_terms: list[str]) -> tuple[float, float]:
    """
    Decide semantic vs keyword weighting based on lexical cues in the query.
    Returns (alpha_semantic, beta_keyword) where alpha + beta = 1.0.
    Heuristics boost keyword weight for quoted phrases, digits/addresses,
    short acronyms, and explicit OR lists.
    """
    text = (user_prompt or "").strip()
    import re
    lexicality = 0.0
    if not text:
        return (0.6, 0.4)
    if re.search(r"\"[^\"]+\"|'[^']+'", text):  # quoted phrases
        lexicality += 0.3
    if re.search(r"[A-Za-z]", text) and re.search(r"\d", text):  # letters and digits
        lexicality += 0.2
    if re.search(r"\b[A-Z]{2,5}\b", text):  # short all-caps acronyms
        lexicality += 0.2
    if or_terms and len(or_terms) > 1:
        lexicality += 0.2
    words = [w for w in re.split(r"\s+", text) if w]
    if 1 <= len(words) <= 2:
        lexicality += 0.1
    lexicality = max(0.0, min(1.0, lexicality))
    beta_keyword = 0.2 + 0.6 * lexicality   # 0.2..0.8
    alpha_semantic = 1.0 - beta_keyword     # 0.8..0.2
    return (alpha_semantic, beta_keyword)

def _fetch_chunks_by_ids(ids: list[str]):
    if not ids:
        return []
    try:
        # Fetch fields needed for sources and summary, including new metadata
        res = (
            supabase.table("file_items")
            .select("*")
            .in_("id", ids)
            .execute()
        )
        data = getattr(res, "data", None) or []
        return data
    except Exception:
        return []

def _select_included_and_pending(matches: list[dict], included_limit: int = 25):
    """
    From a score-sorted list of matches, select the top `included_limit` items.
    The rest of the top 50 are considered pending. This is a simple split,
    ignoring file diversity, to send the most relevant chunks first.
    """
    top_50 = matches[:50]
    included = top_50[:included_limit]
    included_ids = {c.get("id") for c in included}
    
    # Pending are the rest of top 50 not included in the first batch
    pending_ids = [c.get("id") for c in top_50 if c.get("id") and c.get("id") not in included_ids]
    
    return included, pending_ids

def perform_search(tool_args):
    t0 = time.perf_counter()
    query_embedding = tool_args.get("embedding")
    user_prompt = tool_args.get("user_prompt")
    search_query = tool_args.get("search_query")
    
    # --- Dynamic Relevance Parameters ---
    threshold = tool_args.get("relevance_threshold", 0.4) # Similarity threshold
    max_results = tool_args.get("max_results", 100) # Max results to return

    # Patch: If embedding is missing, generate it from search_query or user_prompt
    if not query_embedding:
        text_to_embed = search_query or user_prompt
        if not text_to_embed:
            return {"error": "Embedding must be provided to perform similarity search."}
        from app.api.file_ops.embed import embed_text
        query_embedding = embed_text(text_to_embed)

    try:
        # The SQL function expects a similarity threshold directly.
        db_match_threshold = threshold

        # Build arguments for the RPC call, ensuring empty values are sent as None (NULL)
        rpc_args = {
            "query_embedding": query_embedding,
            "match_threshold": db_match_threshold,
            "match_count": max_results,
            "file_ids": tool_args.get("file_ids") or None
        }
        
        # Add metadata filters from tool_args, ensuring empty strings become None
        metadata_fields = [
            ("file_name", "filter_file_name"),
            ("description", "filter_description"),
            ("document_type", "filter_document_type"),
            ("meeting_year", "filter_meeting_year"),
            ("meeting_month", "filter_meeting_month"),
            ("meeting_month_name", "filter_meeting_month_name"),
            ("meeting_day", "filter_meeting_day"),
            ("ordinance_title", "filter_ordinance_title"),
        ]
        for tool_key, rpc_key in metadata_fields:
            value = tool_args.get(tool_key)
            rpc_args[rpc_key] = value if value else None

        # Exclusively use the new, optimized 'match_file_items_openai' RPC function.
        # This function is defined in Supabase and only returns metadata, not content.
        response = supabase.rpc("match_file_items_openai", rpc_args).execute()
        if getattr(response, "error", None):
            # If the RPC call itself fails, raise an error to be caught below.
            raise RuntimeError(getattr(response.error, "message", str(response.error)))

        matches = response.data or []

        # The database 'similarity' is the score we want.
        matches.sort(key=lambda x: x.get("similarity", 0), reverse=True)

        # --- Apply max_results limit after sorting ---
        matches = matches[:max_results]

        dt_ms = (time.perf_counter() - t0) * 1000.0
        log_info(logger, "semantic.search.ok", {"count": len(matches), "threshold": threshold, "duration_ms": round(dt_ms, 2)})
        return {"retrieved_chunks": matches}
    except Exception as e:
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log_error(logger, "semantic.search.error", {"error": str(e), "duration_ms": round(dt_ms, 2)}, exc_info=True)
        return {"error": f"Error during search: {str(e)}"}


def extract_search_query(user_prompt: str, agent_mode: bool = False) -> str:
    """
    Use OpenAI to extract the most effective search phrase or keywords for semantic document retrieval from the user's request.
    If agent_mode is True, use enhanced instructions for extraction.
    """
    if agent_mode:
        system_prompt = (
            "You are a search assistant. Given a user's request, extract only the most relevant keywords or noun phrases that would best match the content of documents in a search system.\n"
            "- Do not include generic phrases like \"reference to,\" \"information about,\" \"how,\" or \"give me a rundown.\"\n"
            "- Only return concise, specific terms or noun phrases likely to appear in documents.\n"
            "- Use terminology that matches how topics are described in official documents.\n"
            "- Do not include instructions, explanations, or conversational words.\n"
            "- Separate each keyword or phrase with a comma.\n\n"
            "Examples:\n"
            "User: search for reference to arpa and give me a rundown of how the money was used\n"
            "Extracted: ARPA funding usage, ARPA money allocation\n\n"
            "User: find documents about covid relief funding\n"
            "Extracted: covid relief funding\n\n"
            "User: show me reports on infrastructure spending in 2022\n"
            "Extracted: infrastructure spending 2022\n\n"
            "User: what are the guidelines for grant applications\n"
            "Extracted: grant application guidelines\n"
        )
    else:
        system_prompt = (
            "You are a helpful assistant. Given a user request, extract a search phrase or keywords that would best match the content of relevant documents in a semantic search system. "
            "Be specific and use terminology likely to appear in the documents. "
            "Return only the search phrase or keywords, not instructions or explanations."
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    result = chat_completion(messages)
    return result.strip()


async def semantic_search(request, payload):
    return perform_search(payload)


router = APIRouter()


async def keyword_search_async(
    client: httpx.AsyncClient,
    keywords,
    file_ids=None,
    match_count=150,
    file_name_filter=None,
    description_filter=None,
    document_type=None,
    meeting_year=None,
    meeting_month=None,
    meeting_month_name=None,
    meeting_day=None,
    ordinance_title=None,
):
    """
    Asynchronous keyword search over file_items table using Postgres FTS.
    """
    def _quote_term(t: str) -> str:
        t = (t or "").strip()
        if not t:
            return ""
        t = t.replace('"', '\\"')
        if any(ch.isspace() for ch in t) or not t.isalnum():
            return f'"{t}"'
        return t
    
    quoted_terms = [_quote_term(k) for k in keywords if k]
    keyword_query = " OR ".join([qt for qt in quoted_terms if qt])

    rpc_args = {
        "keyword_query": keyword_query,
        "match_count": match_count,
        "file_ids": file_ids,
        "filter_file_name": file_name_filter,
        "filter_description": description_filter,
        "filter_document_type": document_type,
        "filter_meeting_year": meeting_year,
        "filter_meeting_month": meeting_month,
        "filter_meeting_month_name": meeting_month_name,
        "filter_meeting_day": meeting_day,
        "filter_ordinance_title": ordinance_title,
    }

    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_role = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {
        "apikey": service_role,
        "Authorization": f"Bearer {service_role}",
        "Content-Type": "application/json"
    }
    
    endpoint = f"{supabase_url}/rest/v1/rpc/match_file_items_fts"

    try:
        t0 = time.perf_counter()
        response = await client.post(endpoint, headers=headers, json=rpc_args, timeout=30)
        
        if response.status_code >= 400:
            try:
                error_details = response.json()
                log_error(logger, "keyword.search.http_error", {"status": response.status_code, "details": error_details})
            except Exception:
                log_error(logger, "keyword.search.http_error", {"status": response.status_code, "details": "non-JSON body"})
            response.raise_for_status()

        results = response.json() or []
        for r in results:
            r["keyword_score"] = r.get("ts_rank", 0)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log_info(logger, "keyword.search.ok", {"count": len(results), "duration_ms": round(dt_ms, 2)})
        return results
    except Exception as e:
        log_error(logger, "keyword.search.error", {"error": str(e)}, exc_info=True)
        return []


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    # This endpoint is now a simplified wrapper around the assistant endpoint logic
    data = await request.json()
    # Pass the parsed JSON payload through to the assistant endpoint handler
    return await assistant_search_docs(data)



# Endpoint to accept calls from an OpenAI Assistant (custom function / webhook)
@router.post("/assistant/search_docs")
async def assistant_search_docs(payload: dict):
    """
    Accepts a payload containing a search plan, executes the plan in parallel,
    and returns a summary or structured results.
    The planning is expected to be done by the frontend model or a dedicated planner.
    """
    user_prompt = payload.get("user_prompt")
    search_plan = payload.get("search_plan")

    # Log exactly what the frontend sent as the user query (and basic plan info)
    try:
        log_info(logger, "rag.query", {
            "user_prompt": user_prompt,
            "operator": (search_plan or {}).get("operator"),
            "terms": (search_plan or {}).get("terms", []),
        })
    except Exception:
        # Never fail the request due to logging
        pass

    if not user_prompt or not search_plan:
        return JSONResponse({"error": "Missing user_prompt or search_plan in payload"}, status_code=400)

    # The plan is now taken from the payload.
    search_terms = search_plan.get("terms", [])
    if not search_terms:
        # Fallback to user_prompt if terms are missing, though the plan should provide them.
        search_terms = [user_prompt]
    
    all_matches = {}
    log_info(logger, "rag.plan", {"operator": search_plan.get("operator"), "terms_count": len(search_terms)})

    # --- 2. Execute the Search Plan in Parallel ---
    
    # Gather all results from parallel searches
    all_term_results = []

    # Use a single session for all keyword searches
    async with httpx.AsyncClient() as client:
        # Create tasks for all searches
        search_tasks = []
        for term in search_terms:
            search_tasks.append(
                _perform_hybrid_search_for_term(term, payload, client)
            )
        
        # Run all tasks concurrently
        results_from_tasks = await asyncio.gather(*search_tasks)

        # Process results from each task
        for result in results_from_tasks:
            if result:
                all_term_results.append(result)

    # --- 3. Merge and Score results from all terms ---
    for term_results in all_term_results:
        merged_by_id = term_results.get("merged_results", {})
        for chunk_id, chunk_data in merged_by_id.items():
            if chunk_id not in all_matches:
                all_matches[chunk_id] = chunk_data
            else:
                # If chunk already found, update its score to be the max of its previous score and the new one
                all_matches[chunk_id]["combined_score"] = max(
                    all_matches[chunk_id].get("combined_score", 0.0),
                    chunk_data.get("combined_score", 0.0)
                )

    # --- 4. Rerank the Final Combined List ---
    log_info(logger, "rag.merge_across_terms", {"unique_candidates": len(all_matches or {})})
    matches = sorted(all_matches.values(), key=lambda x: x.get("combined_score", 0.0), reverse=True)
    
    top_k_for_rerank = 24 # From "Interactive Q&A" profile (Rerank Top m)
    if matches and len(matches) > 1:
        t_rr0 = time.perf_counter()
        passages = [chunk.get("content", "") for chunk in matches[:top_k_for_rerank]]
        # Use the original, full user prompt for reranking to capture the complete intent
        query_passage_pairs = [[user_prompt, passage] for passage in passages]
        
        # Run cross-encoder in a separate thread to avoid blocking the event loop
        rerank_scores = await asyncio.to_thread(cross_encoder.predict, query_passage_pairs)

        for i, score in enumerate(rerank_scores):
            matches[i]["rerank_score"] = float(score)
        
        reranked_matches = sorted(matches[:top_k_for_rerank], key=lambda x: x.get("rerank_score", 0.0), reverse=True)
        matches = reranked_matches + matches[top_k_for_rerank:]

        rr_scores = [m.get("rerank_score", 0.0) for m in reranked_matches[:5]]
        rr_dt_ms = (time.perf_counter() - t_rr0) * 1000.0
        log_info(logger, "rag.rerank", {
            "applied": True,
            "considered": min(len(passages), top_k_for_rerank),
            "top5_scores": [round(float(s or 0.0), 4) for s in rr_scores],
            "duration_ms": round(rr_dt_ms, 2),
        })
    else:
        log_info(logger, "rag.rerank", {"applied": False, "candidates": len(matches or [])})

    # --- 5. Prepare and Send the Response ---
    summary = None
    summary_was_partial = False
    
    included_chunks = matches[:12] # From "Interactive Q&A" profile (Retrieved k)
    included_chunk_ids = [c.get("id") for c in included_chunks if c.get("id")]

    # --- HYDRATION STEP ---
    # Unconditionally fetch the full content for the final list of chunks.
    # This is necessary because the search functions only return metadata.
    if included_chunk_ids:
        log_info(logger, "rag.hydration.start", {"count": len(included_chunk_ids)})
        t_hyd0 = time.perf_counter()
        hydrated_chunks_data = _fetch_chunks_by_ids(included_chunk_ids)
        
        # Create a dictionary for quick lookup of hydrated content
        hydrated_content_map = {str(chunk.get("id")): chunk for chunk in hydrated_chunks_data}
        
        # Replace the content in our working list of chunks
        for chunk in included_chunks:
            chunk_id = str(chunk.get("id"))
            if chunk_id in hydrated_content_map:
                # Preserve existing scores, but update content and metadata
                hydrated_chunk = hydrated_content_map[chunk_id]
                chunk["content"] = hydrated_chunk.get("content")
                # Also update file_name in case it was missing
                if not chunk.get("file_name"):
                    chunk["file_name"] = hydrated_chunk.get("file_name")

        hyd_dt_ms = (time.perf_counter() - t_hyd0) * 1000.0
        log_info(logger, "rag.hydration.complete", {"hydrated": len(hydrated_chunks_data or []), "duration_ms": round(hyd_dt_ms, 2)})

    try:
        # From "Interactive Q&A" profile (Per-Chunk Size: 900 tokens)
        per_chunk_token_limit = 900
        
        # Helper to trim a single text to a token limit.
        def _trim_to_tokens(text: str, limit: int, model: str) -> str:
            if not text:
                return ""
            # trim_texts_to_token_limit works with a list, so wrap and unwrap.
            trimmed_list = trim_texts_to_token_limit([text], limit, model=model, separator="")
            return trimmed_list[0] if trimmed_list else ""

        user_locale = payload.get("language") or "en-US"
        target_length = payload.get("target_length") or 220
        format_variant = payload.get("format_variant") or "standard"
        k_retrieved = len(included_chunks or [])

        chunk_label_map: dict[str, str] = {}
        file_label_map: dict[str, str] = {}
        for idx, source_entry in enumerate(sources, start=1):
            label = f"S{idx}"
            source_entry["label"] = label
            for cid in source_entry.get("chunk_ids") or []:
                if cid:
                    chunk_label_map[str(cid)] = label
            file_name = source_entry.get("file_name")
            if file_name:
                file_label_map[file_name] = label
            file_id_key = source_entry.get("file_id")
            if file_id_key:
                file_label_map[f"file_id::{file_id_key}"] = label

        normalized_terms: list[str] = []
        for term in search_terms:
            if isinstance(term, str) and term.strip():
                normalized_terms.append(term.lower())
        if isinstance(user_prompt, str) and user_prompt.strip():
            normalized_terms.append(user_prompt.lower())

        # Measure prompt assembly (often the hidden latency)
        t_prompt0 = time.perf_counter()
        retrieved_sections: list[str] = []
        direct_match_labels: set[str] = set()

        for idx, chunk in enumerate(included_chunks, start=1):
            disp = chunk.get("rerank_score") or chunk.get("combined_score") or chunk.get("similarity")
            try:
                disp_val = round(float(disp or 0), 4)
            except Exception:
                disp_val = 0

            chunk_id = chunk.get("id")
            file_label = chunk.get("file_name") or "Unknown source"
            label = chunk_label_map.get(str(chunk_id)) or file_label_map.get(file_label) or file_label_map.get(f"file_id::{chunk.get('file_id')}")
            if not label:
                label = f"S{k_retrieved + len(retrieved_sections) + 1}"
                if chunk_id:
                    chunk_label_map[str(chunk_id)] = label

            body = _trim_to_tokens(chunk.get("content", ""), per_chunk_token_limit, model="gpt-4-turbo")
            if not body:
                continue

            lower_body = body.lower()
            if normalized_terms:
                for term in normalized_terms:
                    if term and term in lower_body:
                        direct_match_labels.add(label)
                        break
            if label not in direct_match_labels and normalized_terms:
                raw_content_lower = (chunk.get("content") or "").lower()
                for term in normalized_terms:
                    if term and term in raw_content_lower:
                        direct_match_labels.add(label)
                        break

            score_text = f"{disp_val:.4f}" if isinstance(disp_val, (int, float)) else str(disp_val)
            section_lines = [
                f"[{label}] {file_label}",
            ]
            if chunk_id:
                section_lines.append(f"chunk_id: {chunk_id}")
            if chunk.get("file_id"):
                section_lines.append(f"file_id: {chunk.get('file_id')}")
            if chunk.get("meeting_date"):
                section_lines.append(f"meeting_date: {chunk.get('meeting_date')}")
            section_lines.append(f"score: {score_text}")
            section_lines.append("content:")
            section_lines.append("---")
            section_lines.append(body)
            section_lines.append("---")
            retrieved_sections.append("\n".join(section_lines))

        retrieved_context = "\n\n".join(retrieved_sections)
        prompt_build_ms = (time.perf_counter() - t_prompt0) * 1000.0
        if settings.DEBUG_VERBOSE_LOG_TEXTS:
            log_info(logger, "rag.summary.input.preview", {"chars": len(retrieved_context)})

        if retrieved_context.strip():
            log_info(logger, "rag.summary.start", {
                "included_count": len(included_chunks or []),
                "prompt_build_ms": round(prompt_build_ms, 2),
                "direct_match_labels": sorted(direct_match_labels),
            })

            sources_catalog_lines: list[str] = []
            for source_entry in sources:
                label = source_entry.get("label") or "?"
                title = source_entry.get("file_name") or "Unknown source"
                meeting_date = source_entry.get("meeting_date")
                file_id_value = source_entry.get("file_id")
                line = f"[{label}] {title}"
                if meeting_date:
                    line += f" (date={meeting_date})"
                if file_id_value:
                    line += f" - file_id={file_id_value}"
                sources_catalog_lines.append(line)
            sources_catalog_text = "\n".join(sources_catalog_lines) if sources_catalog_lines else "(no sources)"

            search_terms_line = (
                f"Search plan terms: {', '.join(term for term in search_terms if isinstance(term, str) and term.strip())}"
                if search_terms else "Search plan terms: (none)"
            )
            if direct_match_labels:
                direct_match_note = (
                    "The query terms appear in sources: "
                    + ", ".join(sorted(direct_match_labels))
                    + ". Highlight their evidence clearly."
                )
            else:
                direct_match_note = "No direct string match was detected; rely on the strongest evidence."

            retrieved_context_text = retrieved_context if retrieved_sections else "(no text retrieved)"

            user_query_for_prompt = user_prompt or ""
            user_query_format_safe = user_query_for_prompt.replace("{", "{{").replace("}", "}}")

            system_template = textwrap.dedent("""
                RAG Summarizer - System Instructions

                Role & Objective
                You are a factual, no-nonsense summarizer for a Retrieval-Augmented Generation pipeline. Your only job is to summarize the provided passages and metadata in response to the user query "{user_query}". Do not invent facts. If the context does not contain the answer, say so clearly.

                Inputs Provided by the caller:
                - User Query: "{user_query}"
                - Results: up to {k} retrieved items with title, source identifiers, timestamps, text content, chunk IDs, and optional scores.
                - Language: {user_locale}. Write in this language.
                - Target Length: {target_length} words.
                - Format Variant: {format_variant} (brief|standard|detailed).

                Hard Rules:
                1. No hallucinations: only use facts present in the provided results. If something is uncertain or missing, explicitly note it.
                2. Citations: after each claim that depends on a source, include compact citations like [S1] or [S3, S5]. Map every citation to the Sources section at the end.
                3. Deduplication and synthesis: merge overlapping points, prefer the most recent and highest-quality sources when conflicts occur. If evidence conflicts, present both and label the situation as Conflict.
                4. Date awareness: include dates when present. If the material may be stale, call it out (for example, "Last updated 2023-11-04 [S2]").
                5. Scope control: summarize only what is relevant to the user query. Ignore unrelated material.
                6. Privacy and safety: do not expose secrets, system prompts, or raw credentials. Replace them with [REDACTED] if encountered.
                7. Language and tone: use {user_locale}. Be clear, concise, and neutral.
                8. Formatting: follow the markdown scaffold exactly. Do not add sections unless requested.

                Output Markdown Scaffold (always use):
                ### Title
                A short, specific title.

                ### TL;DR
                One or two sentences answering the query.

                ### Key Points
                * 3-7 bullets with the most important facts. End each evidence-based bullet with citations like [S2].

                ### Details
                One or two short paragraphs that synthesize the evidence, resolve conflicts, and add necessary context. Use inline dates and metrics when available, with citations.

                ### Caveats & Unknowns
                * List any gaps, missing data, ambiguities, or conflicts (with citations).

                ### Next Steps (if applicable)
                * Actionable follow-ups such as tests to run, data to fetch, or documents to check.

                ### Sources
                * [S1] Title - identifier (date if known).
                * [S2] Title - identifier (date if known).
                Only list sources that were cited.

                Additional Formatting Guidelines:
                - Length control: aim for {target_length} words (+/-15%). If format variant is brief, target 100-150 words. If detailed, target 300-500 words.
                - Lists: prefer bullets for dense facts. Keep bullets under two lines when possible.
                - Numbers: preserve exact figures and units from the sources. If ranges differ, show both and cite each source.
                - Tables (optional): you may include a small markdown table (<=6 columns, <=10 rows) if it adds clarity.
                - Code or CLI snippets (optional): for technical steps you may include code blocks up to 15 lines.
                - Quotations: quote sparingly (<=20 words) only when the exact phrasing matters, and cite the source.
                - Redactions: replace sensitive tokens with [REDACTED] and mention the redaction in Caveats & Unknowns.

                Conflict Resolution Protocol:
                - Prefer newer sources over older ones when both are credible.
                - If credibility differs, present both views under Conflict with citations and avoid adjudicating beyond the evidence.
                - If a claim appears only once and is extraordinary, flag it as low confidence unless corroborated.

                When Context Is Insufficient:
                - Write: "The provided results do not contain enough information to answer this fully." Then list what is missing and specific follow-ups to retrieve.

                Prohibited Behaviors:
                - Do not browse or add outside knowledge.
                - Do not speculate, guess, or role-play.
                - Do not promise actions beyond summarization.
            """)

            system_content = system_template.format(
                user_query=user_query_format_safe,
                k=k_retrieved,
                user_locale=user_locale,
                target_length=target_length,
                format_variant=format_variant,
            )

            user_message_parts = [
                f"User query: {user_query_for_prompt}",
                search_terms_line,
                f"Language: {user_locale}.",
                f"Target length: {target_length} words. Format variant: {format_variant}.",
                "",
                "Retrieved results:",
                retrieved_context_text,
                "",
                "Sources catalog:",
                sources_catalog_text,
                "",
                direct_match_note,
            ]
            user_message_content = "\n".join(user_message_parts)

            summary_prompt = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message_content},
            ]
            # Constrain output tokens
            MAX_OUTPUT_TOKENS = 120_000
            t_sum0 = time.perf_counter()
            content, was_partial = stream_chat_completion(summary_prompt, model="gpt-5", max_seconds=99999, max_tokens=MAX_OUTPUT_TOKENS)
            sum_dt_ms = (time.perf_counter() - t_sum0) * 1000.0
            summary = content if content else None
            summary_was_partial = bool(was_partial)
            # Do not log the full summary by default. Only length.
            log_info(logger, "rag.summary.done", {"has_summary": bool(summary), "length": (len(summary) if isinstance(summary, str) else 0), "partial": summary_was_partial, "duration_ms": round(sum_dt_ms, 2)})

            # Optionally log a truncated summary snippet
            try:
                if summary and settings.LOG_SUMMARY_TEXT:
                    max_chars = int(getattr(settings, "SUMMARY_TEXT_MAX_CHARS", 1200) or 1200)
                    snippet = summary[:max_chars]
                    log_info(logger, "rag.summary.text", {
                        "chars": len(summary),
                        "truncated": len(summary) > max_chars,
                        "text": snippet,
                    })
            except Exception:
                # Never fail the request due to logging
                pass

            # Optionally persist full summary and metadata to Supabase (best-effort)
            try:
                if summary and settings.CAPTURE_SUMMARY_TO_DB:
                    rid = request_id_var.get()
                    record = {
                        "request_id": rid,
                        "user_prompt": user_prompt,
                        "included_chunk_ids": included_chunk_ids,
                        "summary": summary,
                        "summary_len": len(summary),
                        "summary_partial": bool(summary_was_partial),
                    }
                    try:
                        supabase.table("rag_summary_results").insert(record).execute()
                        log_info(logger, "rag.summary.capture.ok", {"len": len(summary)})
                    except Exception as db_e:
                        log_error(logger, "rag.summary.capture.error", {"error": str(db_e)})
            except Exception:
                # Best-effort only
                pass
    except Exception as e:
        # Log summary failures explicitly; this is the step right after reranking
        log_error(logger, "rag.summary.error", {"error": repr(e)}, exc_info=True)
        summary = None
        summary_was_partial = False
    
    excerpt_length = 300
    sources_map: dict[str, dict] = {}
    ordered_sources: list[dict] = []
    for chunk in included_chunks:
        file_name = chunk.get("file_name") or "Unknown source"
        score = chunk.get("rerank_score") or chunk.get("combined_score") or chunk.get("similarity")
        content = chunk.get("content") or ""
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        entry = sources_map.get(file_name)
        if not entry:
            entry = {
                "file_name": file_name,
                "score": score,
                "excerpt": excerpt,
                "chunk_ids": [chunk.get("id")],
                "id": chunk.get("id"),
                "file_id": chunk.get("file_id"),
                "meeting_date": chunk.get("meeting_date"),
            }
            sources_map[file_name] = entry
            ordered_sources.append(entry)
        else:
            entry.setdefault("chunk_ids", []).append(chunk.get("id"))
            if score is not None and (entry.get("score") is None or score > entry.get("score")):
                entry["score"] = score
                if excerpt:
                    entry["excerpt"] = excerpt
                    entry["id"] = chunk.get("id")
                    if chunk.get("file_id"):
                        entry["file_id"] = chunk.get("file_id")
                    if chunk.get("meeting_date"):
                        entry["meeting_date"] = chunk.get("meeting_date")

    sources = ordered_sources

    # Emit final-stage metrics
    log_info(logger, "rag.final", {
        "included_count": len(included_chunks or []),
        "included_ids_count": len(included_chunk_ids or []),
        "has_summary": bool(summary),
        "summary_len": (len(summary) if isinstance(summary, str) else None),
        "summary_was_partial": bool(summary_was_partial),
    })

    response_data = {
        "summary": summary,
        "summary_was_partial": summary_was_partial,
        "sources": sources,
        "can_resume": False, # Batch processing is removed
        "pending_chunk_ids": [],
        "included_chunk_ids": included_chunk_ids,
    }

    log_info(logger, "rag.sources", {"count": len(sources or [])})

    if isinstance(summary, str) and summary.strip():
        plain_text = summary.strip()
    else:
        fallback_lines: list[str] = [
            "No synthesized summary was produced. Review the retrieved evidence below:",
        ]
        if sources:
            for idx, source in enumerate(sources, start=1):
                name = str(source.get("file_name") or f"source-{idx}")
                excerpt = source.get("excerpt")
                excerpt_text = f": {excerpt}" if isinstance(excerpt, str) and excerpt else ""
                fallback_lines.append(f"- {name}{excerpt_text}")
        else:
            fallback_lines.append("- No sources were returned for this query.")

        plain_text = "\n".join(fallback_lines)

    try:
        preview = plain_text[:600] if isinstance(plain_text, str) else ""
        log_info(
            logger,
            "rag.emit",
            {
                "media_type": "text/plain",
                "chars": len(plain_text or ""),
                "has_summary": bool(summary and isinstance(summary, str) and summary.strip()),
                "sources_count": len(sources or []),
                "preview": preview,
            },
        )
    except Exception:
        # Never fail the request due to logging
        pass

    async def text_stream():
        yield plain_text

    return StreamingResponse(text_stream(), media_type="text/plain")


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)

async def _perform_hybrid_search_for_term(term: str, payload: dict, client: httpx.AsyncClient):
    """
    Performs a self-contained hybrid search for a single term.
    This function is designed to be run concurrently for multiple terms.
    """
    try:
        # Each term gets its own hybrid search
        t0 = time.perf_counter()
        embedding = await asyncio.to_thread(embed_text, term)
        log_info(logger, "rag.embed", {"term_len": len(term or ""), "duration_ms": round((time.perf_counter()-t0)*1000.0, 2)})
    except Exception as e:
        log_error(logger, "rag.embed.error", {"term": term, "error": str(e)})
        return None

    tool_args = {
        "embedding": embedding,
        "user_prompt": term,
        "search_query": term,
        "relevance_threshold": 0.25, # From "Interactive Q&A" profile
        "max_results": 50,
        "file_ids": payload.get("file_ids"),
        "file_name": payload.get("file_name"),
        "description": payload.get("description"),
        "document_type": payload.get("document_type"),
        "meeting_year": payload.get("meeting_year"),
        "meeting_month": payload.get("meeting_month"),
        "meeting_month_name": payload.get("meeting_month_name"),
        "meeting_day": payload.get("meeting_day"),
        "ordinance_title": payload.get("ordinance_title"),
    }
    
    # Perform semantic and keyword searches concurrently
    semantic_task = asyncio.to_thread(perform_search, tool_args)
    
    sem_w, kw_w = _decide_weighting(term, [])
    keyword_task = keyword_search_async(
        client=client,
        keywords=[term.strip()],
        file_ids=tool_args.get("file_ids"),
        match_count=150,
        file_name_filter=tool_args.get("file_name"),
        description_filter=tool_args.get("description"),
        document_type=tool_args.get("document_type"),
        meeting_year=tool_args.get("meeting_year"),
        meeting_month=tool_args.get("meeting_month"),
        meeting_month_name=tool_args.get("meeting_month_name"),
        meeting_day=tool_args.get("meeting_day"),
        ordinance_title=tool_args.get("ordinance_title"),
    )

    search_result, fts_results = await asyncio.gather(semantic_task, keyword_task)
    
    term_matches = search_result.get("retrieved_chunks", [])
    fts_results = fts_results or []

    # --- Merge and Score results for the current term ---
    kw_scores = [r.get("keyword_score") or 0.0 for r in fts_results]
    kmin, kmax = (min(kw_scores), max(kw_scores)) if kw_scores else (0.0, 0.0)
    for r in fts_results:
        ks = r.get("keyword_score") or 0.0
        r["keyword_score_norm"] = (ks - kmin) / (kmax - kmin) if kmax > kmin else 0.0
    
    merged_by_id = {m.get("id"): m for m in term_matches if m.get("id")}
    for r in fts_results:
        rid = r.get("id")
        if not rid: continue
        if rid in merged_by_id:
            merged_by_id[rid]["keyword_score_norm"] = max(
                merged_by_id[rid].get("keyword_score_norm", 0.0),
                r.get("keyword_score_norm", 0.0)
            )
        else:
            merged_by_id[rid] = r
    
    for v in merged_by_id.values():
        sem = v.get("similarity", 0.0) or 0.0
        kw = v.get("keyword_score_norm", 0.0) or 0.0
        v["combined_score"] = sem_w * sem + kw_w * kw

    # Term-level metrics
    log_info(logger, "rag.term.results", {
        "term_len": len(term or ""),
        "semantic_count": len(term_matches or []),
        "keyword_count": len(fts_results or []),
        "merged_count": len(merged_by_id or {}),
        "weights": {"semantic": round(sem_w, 3), "keyword": round(kw_w, 3)},
    })
    
    # This debug block is no longer needed as the hydration logic is fixed.
    # try:
    #     sorted_merged = sorted(merged_by_id.values(), key=lambda x: x.get('combined_score', 0.0), reverse=True)
    #     print(f"--- TOP 5 MERGED FOR TERM '{term}' (BEFORE RERANK) ---")
    #     for i, item in enumerate(sorted_merged[:5]):
    #         content_preview = (item.get('content') or 'NO CONTENT').strip().replace('\\n', ' ')[:100]
    #         print(f"  #{i+1}: id={item.get('id')}, score={item.get('combined_score'):.4f}, content='{content_preview}...'")
    #     print("----------------------------------------------------")
    # except Exception as e:
    #     print(f"[WARN] Failed to print top merged results for term '{term}': {e}")

    return {"term": term, "merged_results": merged_by_id}
