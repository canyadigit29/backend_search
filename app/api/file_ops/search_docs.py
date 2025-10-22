import json
import os
from collections import defaultdict
import sys
import statistics
import httpx

from app.core.supabase_client import create_client
from app.api.file_ops.embed import embed_text
from app.core.openai_client import chat_completion, stream_chat_completion
from app.core.query_understanding import extract_search_filters
from app.core.llama_query_transform import llama_query_transform
from app.core.token_utils import trim_texts_to_token_limit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import time
from app.core.stopwatch import Stopwatch


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
        # Fetch minimal fields needed to build sources and summary
        res = (
            supabase.table("document_chunks")
            .select("id,file_id,file_name,page_number,chunk_index,content")
            .in_("id", ids)
            .execute()
        )
        data = getattr(res, "data", None) or []
        return data
    except Exception:
        return []

def _select_included_and_pending(matches: list[dict], included_limit: int = 25, per_file_cap: int = 2):
    """
    From a score-sorted list of matches, select up to `included_limit` items with a
    per-file cap of `per_file_cap` to promote diversity. Operates over the top-50 only.
    Returns (included_chunks, pending_chunk_ids) where pending are the remaining items
    from the top-50 that were not included this pass, preserving order.
    """
    top50 = matches[:50]
    included = []
    included_ids = set()
    per_file_counts = defaultdict(int)

    # First pass: enforce per-file cap while filling included list
    for c in top50:
        if len(included) >= included_limit:
            break
        fid = c.get("file_id")
        cid = c.get("id")
        if not cid:
            continue
        if per_file_counts[fid] < per_file_cap:
            included.append(c)
            included_ids.add(cid)
            per_file_counts[fid] += 1

    # Second pass: if fewer than included_limit, fill from remaining without cap
    if len(included) < included_limit:
        for c in top50:
            if len(included) >= included_limit:
                break
            cid = c.get("id")
            if not cid or cid in included_ids:
                continue
            included.append(c)
            included_ids.add(cid)

    # Pending are the rest of top50 not included
    pending_ids = [c.get("id") for c in top50 if c.get("id") and c.get("id") not in included_ids]
    return included, pending_ids

def perform_search(tool_args):
    query_embedding = tool_args.get("embedding")
    user_id_filter = tool_args.get("user_id_filter")
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

    if not user_id_filter:
        return {"error": "user_id must be provided to perform search."}
    try:
        # Semantic search
        rpc_args = {
            "p_query_embedding": query_embedding,
            "p_search_weights": json.dumps(tool_args.get("search_weights", {"semantic": 0.5, "keyword": 0.5})),
            "p_relevance_threshold": threshold,
            "p_user_id": user_id_filter,
            "p_doc_type": tool_args.get("doc_type"),
            "p_or_terms": tool_args.get("or_terms"),
            "p_chunk_ids_in": tool_args.get("chunk_ids_in"),
            "p_match_limit": max_results,
            "p_start_date": tool_args.get("start_date"),
            "p_end_date": tool_args.get("end_date"),
            "p_metadata_filter": tool_args.get("metadata_filter"),
        }

        # Exclusively use the new, optimized 'match_documents_v3' RPC function.
        response = supabase.rpc("match_documents_v3", rpc_args).execute()
        if getattr(response, "error", None):
            # If the RPC call itself fails, raise an error to be caught below.
            raise RuntimeError(getattr(response.error, "message", str(response.error)))

        matches = response.data or []

        # The database 'similarity' is the combined score, which is what we want.
        matches.sort(key=lambda x: x.get("similarity", 0), reverse=True)

        # --- Apply max_results limit after sorting ---
        matches = matches[:max_results]

        return {"retrieved_chunks": matches}
    except Exception as e:
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


def keyword_search(
    keywords,
    user_id_filter=None,
    doc_type=None,
    match_count=150,
    start_date=None,
    end_date=None,
    metadata_filter=None,
):
    """
    Keyword search over document_chunks table using Postgres FTS (ts_rank/BM25).
    This function specifically targets the 'match_documents_fts_v3' RPC endpoint.
    """
    def _quote_term(t: str) -> str:
        t = (t or "").strip()
        if not t:
            return ""
        # Escape embedded quotes
        t = t.replace('"', '\\"')
        # Quote multi-word or non-alnum terms to preserve phrases
        if any(ch.isspace() for ch in t) or not t.isalnum():
            return f'"{t}"'
        return t
    # Use OR between terms so any term can match
    quoted_terms = [_quote_term(k) for k in keywords if k]
    keyword_query = " OR ".join([qt for qt in quoted_terms if qt])

    # Arguments for match_documents_fts_v3, aligned with its SQL definition
    rpc_args = {
        "p_query_text": keyword_query,
        "p_user_id": user_id_filter,
        "p_doc_type": doc_type,
        "p_match_limit": match_count,
        "p_start_date": start_date,
        "p_end_date": end_date,
        "p_metadata_filter": metadata_filter,
    }

    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    service_role = os.environ["SUPABASE_SERVICE_ROLE"]
    headers = {
        "apikey": service_role,
        "Authorization": f"Bearer {service_role}",
        "Content-Type": "application/json"
    }
    
    endpoint = f"{supabase_url}/rest/v1/rpc/match_documents_fts_v3"

    try:
        print(f"[DEBUG] Keyword search: attempting endpoint {endpoint} with args {list(rpc_args.keys())}")
        response = httpx.post(endpoint, headers=headers, json=rpc_args, timeout=30)
        
        if response.status_code >= 400:
            try:
                error_details = response.json()
                print(f"[ERROR] Keyword search: endpoint {endpoint} returned status {response.status_code} with details: {error_details}")
            except Exception:
                print(f"[ERROR] Keyword search: endpoint {endpoint} returned status {response.status_code} with non-JSON body.")
            response.raise_for_status()

        results = response.json() or []
        print(f"[DEBUG] Keyword search: successfully called {endpoint}, found {len(results)} results.")
        for r in results:
            r["keyword_score"] = r.get("similarity", 0) # FTS function now returns 'similarity'
        return results
    except Exception as e:
        print(f"[ERROR] Keyword search: unhandled exception during search. Error: {str(e)}")
        return []


@router.post("/file_ops/search_docs")
async def api_search_docs(request: Request):
    # This endpoint is now a simplified wrapper around the assistant endpoint logic
    data = await request.json()
    return await assistant_search_docs(request)


# Endpoint to accept calls from an OpenAI Assistant (custom function / webhook)
@router.post("/assistant/search_docs")
async def assistant_search_docs(request: Request):
    """
    Accepts a payload from an OpenAI assistant, normalizes fields, and forwards
    to the perform_search flow.
    """
    # Timer no longer controls batching; kept available if needed elsewhere
    sw = None

    try:
        data = await request.json()
    except Exception as e:
        return JSONResponse({"error": f"Invalid JSON payload: {e}"}, status_code=400)

    user_prompt = data.get("query") or data.get("user_prompt")
    if not user_prompt:
        return JSONResponse({"error": "Missing query in payload"}, status_code=400)

    user_id = (data.get("user", {}).get("id") or 
               os.environ.get("ASSISTANT_DEFAULT_USER_ID") or 
               "4a867500-7423-4eaa-bc79-94e368555e05")

    # Optional resume mode: summarize only specific chunk IDs provided by the caller
    resume_chunk_ids = data.get("resume_chunk_ids")

    relevance_threshold = data.get("relevance_threshold")
    if relevance_threshold is None:
        relevance_threshold = 0.4 # Default if not provided by assistant

    query_obj = llama_query_transform(user_prompt)
    search_query = query_obj.get("query") or user_prompt
    search_filters = query_obj.get("filters") or {}

    try:
        embedding = embed_text(search_query)
    except Exception as e:
        return JSONResponse({"error": f"Failed to generate embedding: {e}"}, status_code=500)

    matches = []
    if resume_chunk_ids:
        # Resume path: fetch only the requested chunk IDs, preserving the caller-provided order
        fetched = _fetch_chunks_by_ids(resume_chunk_ids)
        by_id = {c.get("id"): c for c in fetched}
        matches = [by_id[i] for i in resume_chunk_ids if by_id.get(i)]
    else:
        provided_or_terms = data.get("or_terms") if isinstance(data.get("or_terms"), list) else []
        inline_or_terms = _parse_inline_or_terms(user_prompt)
        or_terms = provided_or_terms or inline_or_terms
        
        tool_args = {
            "embedding": embedding,
            "user_id_filter": user_id,
            "doc_type": data.get("doc_type"),
            "start_date": data.get("start_date"),
            "end_date": data.get("end_date"),
            "metadata_filter": data.get("metadata_filter"),
            "user_prompt": user_prompt,
            "search_query": search_query,
            "relevance_threshold": relevance_threshold,
            "max_results": data.get("max_results"),
            "search_weights": data.get("search_weights"),
            "or_terms": or_terms,
        }
        
        # Optional OR-terms merging is now handled inside the match_documents_v3 function
        search_result = perform_search(tool_args)
        matches = search_result.get("retrieved_chunks", [])

        # Fallback: If semantic/OR-merged results are sparse, run a keyword FTS search and merge
        try:
            should_fallback = len(matches) < 5
        except Exception:
            should_fallback = True
        if should_fallback:
            # Build keyword list: prefer explicit or_terms or inline OR-parsed terms; avoid treating the entire query as one phrase
            keyword_terms = []
            if or_terms:
                keyword_terms.extend([str(t) for t in or_terms if t])
            else:
                # As a last resort, include the whole prompt and also split into tokens > 2 chars
                if isinstance(user_prompt, str) and user_prompt.strip():
                    inline_terms = _parse_inline_or_terms(user_prompt)
                    if inline_terms:
                        keyword_terms.extend(inline_terms)
                    else:
                        keyword_terms.append(user_prompt.strip())
            # If still empty, don't attempt FTS
            if keyword_terms:
                fts_results = keyword_search(
                    keywords=keyword_terms,
                    user_id_filter=user_id,
                    doc_type=tool_args.get("doc_type"),
                    start_date=tool_args.get("start_date"),
                    end_date=tool_args.get("end_date"),
                    metadata_filter=tool_args.get("metadata_filter"),
                    match_count=150,
                ) or []
                # Normalize keyword scores to 0..1 (per-batch) for blending
                kw_scores = [r.get("keyword_score") or 0.0 for r in fts_results]
                if kw_scores:
                    kmin, kmax = min(kw_scores), max(kw_scores)
                else:
                    kmin, kmax = (0.0, 0.0)
                for r in fts_results:
                    ks = r.get("keyword_score") or 0.0
                    if kmax > kmin:
                        r["keyword_score_norm"] = (ks - kmin) / (kmax - kmin)
                    else:
                        r["keyword_score_norm"] = 0.0
                
                # Let the assistant decide the blend, otherwise default to 50/50
                weights = data.get("search_weights", {"semantic": 0.5, "keyword": 0.5})
                alpha_sem = weights.get("semantic", 0.5)
                beta_kw = weights.get("keyword", 0.5)

                merged_by_id = {}
                for m in matches:
                    mid = m.get("id")
                    if not mid:
                        continue
                    merged_by_id[mid] = m
                for r in fts_results:
                    rid = r.get("id")
                    if not rid:
                        continue
                    existing = merged_by_id.get(rid)
                    if existing is None:
                        merged_by_id[rid] = r
                    else:
                        # Preserve best normalized keyword score
                        existing["keyword_score_norm"] = max(
                            existing.get("keyword_score_norm", 0.0) or 0.0,
                            r.get("keyword_score_norm", 0.0) or 0.0,
                        )
                # Compute combined score and sort
                for v in merged_by_id.values():
                    sem = v.get("similarity", 0.0) or 0.0 # Now using 'similarity' from DB
                    kw = v.get("keyword_score_norm", 0.0) or 0.0
                    v["combined_score"] = alpha_sem * sem + beta_kw * kw
                matches = sorted(merged_by_id.values(), key=lambda x: (x.get("combined_score", 0.0) or 0.0), reverse=True)
    
    # --- OPTIMIZED: Neighbor retrieval and summary using the simplified results ---
    chunk_map = {(c.get("file_id"), c.get("chunk_index")): c for c in matches}
    for chunk in matches:
        file_id, chunk_index = chunk.get("file_id"), chunk.get("chunk_index")
        if file_id and chunk_index is not None:
            prev_chunk = chunk_map.get((file_id, chunk_index - 1))
            if prev_chunk: chunk["prev_chunk"] = {k: prev_chunk[k] for k in ("content", "page_number") if k in prev_chunk}
            next_chunk = chunk_map.get((file_id, chunk_index + 1))
            if next_chunk: chunk["next_chunk"] = {k: next_chunk[k] for k in ("content", "page_number") if k in next_chunk}

    summary = None
    summary_was_partial = False
    pending_chunk_ids = []
    included_chunk_ids = []
    included_chunks = []
    try:
        # Fixed batching with diversification: select included with per-file cap, pending is the rest of top 50
        included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=25, per_file_cap=2)
        included_chunk_ids = [c.get("id") for c in included_chunks if c.get("id")]

        # Guardrail: trim each chunk to avoid exceeding model context (hard-coded)
        per_chunk_char_limit = 2500
        def _trim(s: str, n: int) -> str:
            if not s:
                return ""
            return s[:n]
        # Build structured search results block with metadata headers per chunk
        annotated_texts = []
        for idx, chunk in enumerate(included_chunks, start=1):
            # Prefer combined_score if available, else semantic score, else normalized keyword
            disp = chunk.get("combined_score")
            if disp is None:
                disp = chunk.get("score")
            if disp is None:
                disp = chunk.get("keyword_score_norm")
            try:
                disp_val = round(float(disp or 0), 4)
            except Exception:
                disp_val = 0
            header = f"[#{{idx}} id={chunk.get('id')} file={chunk.get('file_name')} page={chunk.get('page_number')} score={disp_val}]"
            body = _trim(chunk.get("content", ""), per_chunk_char_limit)
            if body:
                annotated_texts.append(f"{header}\n{body}")
        # Token-aware cap across all included texts (hard-coded): assume ~200k token context, leave headroom
        MAX_INPUT_TOKENS = 260_000
        top_text = trim_texts_to_token_limit(annotated_texts, MAX_INPUT_TOKENS, model="gpt-5", separator="\n\n")

        if top_text.strip():
            summary_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are an insightful research assistant. Read the provided document chunks and produce a concise, accurate synthesis that directly answers the user's query. "
                        "Cite evidence using the chunk ids (id=...) when making claims. Prefer precision over verbosity. If multiple interpretations exist, explain them briefly."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"User query: {user_prompt}\n\n"
                        "Search results (each chunk starts with a metadata header):\n"
                        f"{top_text}\n\n"
                        "Please respond with the following structure:\n"
                        "1) Key findings (with inline citations like [id=...])\n"
                        "2) Evidence by chunk (grouped by id, 1-3 bullets each)\n"
                        "3) Important names/aliases/variants\n"
                        "4) Suggested follow-up questions"
                    ),
                },
            ]
            # No time cap: complete the summary for this batch; constrain output tokens (hard-coded)
            MAX_OUTPUT_TOKENS = 100_000
            content, was_partial = stream_chat_completion(summary_prompt, model=None, max_seconds=99999, max_tokens=MAX_OUTPUT_TOKENS)
            summary = content if content else None
            summary_was_partial = bool(was_partial)
            # Even if partial occurs due to upstream issues, we still return pending based on batch remainder
    except Exception:
        summary = None
        summary_was_partial = False
        # Keep pending/included as computed if any
    
    # --- Build sources without clickable links (no signed URLs) ---
    excerpt_length = 300
    sources = []
    # Iterate over the same 'included_chunks' list to build the sources we summarized.
    for c in included_chunks:
        file_name = c.get("file_name")
        content = c.get("content") or ""
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        sources.append({
            "id": c.get("id"),
            "file_name": file_name,
            "page_number": c.get("page_number"),
            "score": c.get("score"),
            "excerpt": excerpt
        })

    # Resume is offered when there are remainder chunks in the top-50 (batch 2)
    can_resume = bool(pending_chunk_ids)
    return JSONResponse({
        "summary": summary,
        "summary_was_partial": summary_was_partial,
        "sources": sources,
        "can_resume": can_resume,
        "pending_chunk_ids": pending_chunk_ids,
        "included_chunk_ids": included_chunk_ids,
        # Timing metrics removed for batch-based flow
    })


# Legacy endpoint maintained for backward compatibility
@router.post("/search")
async def api_search_documents(request: Request):
    tool_args = await request.json()
    return perform_search(tool_args)
