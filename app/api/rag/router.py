import os
import asyncio
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

# Consolidate all necessary imports here
from app.api.file_ops.embed import embed_text
from app.core.supabase_client import create_client
from app.core.openai_client import chat_completion, stream_chat_completion
from app.core.token_utils import trim_texts_to_token_limit
from sentence_transformers import CrossEncoder

# Local modules for classification and search logic
from .classifier import classify_query, get_search_parameters
from ..file_ops.search_docs import perform_search, keyword_search, _parse_inline_or_terms, _fetch_chunks_by_ids, _select_included_and_pending

router = APIRouter()

# --- Pydantic Models for Request and Response ---
class RagSearchRequest(BaseModel):
    query: str = Field(..., description="The user's search query.")
    response_mode: str = Field("summary", description="Response mode: 'summary' or 'structured_results'.")
    # Allow other potential fields from the tool definition to be passed through
    class Config:
        extra = 'allow'

class RagSearchResponse(BaseModel):
    summary: Optional[str]
    summary_was_partial: bool
    sources: List[Dict]
    can_resume: bool
    pending_chunk_ids: List[str]
    included_chunk_ids: List[str]
    retrieved_chunks: Optional[List[Dict]] = None

# --- Main RAG Search Endpoint ---
@router.post("/rag-search", response_model=RagSearchResponse)
async def rag_search(request: RagSearchRequest):
    """
    This is the consolidated RAG endpoint. It takes a user query,
    orchestrates the full RAG pipeline based on assistant instructions,
    and returns a synthesized response.
    """
    user_prompt = request.query
    if not user_prompt:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    try:
        # 1. Classify Query and Get Parameters
        classification = await classify_query(user_prompt)
        search_params = get_search_parameters(classification.query_type)
        relevance_threshold = search_params["relevance_threshold"]
        search_weights = search_params["search_weights"]

        # 2. Generate Embedding for the main query
        try:
            embedding = embed_text(user_prompt)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate embedding: {e}")

        # 3. Perform Hybrid Search (Semantic + Keyword)
        # This logic is adapted from the original assistant_search_docs function
        
        # Initial semantic search
        tool_args = {
            "embedding": embedding,
            "user_prompt": user_prompt,
            "search_query": user_prompt,
            "relevance_threshold": relevance_threshold,
            "max_results": 100,
            **request.dict(exclude={"query", "response_mode"}) # Pass through other filters
        }
        search_result = perform_search(tool_args)
        matches = search_result.get("retrieved_chunks", [])

        # Keyword search augmentation
        keyword_terms = _parse_inline_or_terms(user_prompt) or [user_prompt]
        fts_results = keyword_search(
            keywords=keyword_terms,
            match_count=150,
            **request.dict(exclude={"query", "response_mode"})
        ) or []

        # Normalize keyword scores
        kw_scores = [r.get("keyword_score", 0.0) for r in fts_results]
        kmin, kmax = (min(kw_scores), max(kw_scores)) if kw_scores else (0.0, 0.0)
        for r in fts_results:
            ks = r.get("keyword_score", 0.0)
            r["keyword_score_norm"] = (ks - kmin) / (kmax - kmin) if kmax > kmin else 0.0

        # Blend results
        merged_by_id = {m.get("id"): m for m in matches if m.get("id")}
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
        
        # Compute combined score
        alpha_sem = search_weights.get("semantic", 0.5)
        beta_kw = search_weights.get("keyword", 0.5)
        for v in merged_by_id.values():
            sem = v.get("score", 0.0) or 0.0
            kw = v.get("keyword_score_norm", 0.0) or 0.0
            v["combined_score"] = alpha_sem * sem + beta_kw * kw
        
        matches = sorted(merged_by_id.values(), key=lambda x: x.get("combined_score", 0.0), reverse=True)

        # 4. Rerank Top Results
        # Logic copied from assistant_search_docs
        top_k_for_rerank = 50
        if matches and len(matches) > 1:
            cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            passages = [chunk.get("content", "") for chunk in matches[:top_k_for_rerank]]
            query_passage_pairs = [[user_prompt, passage] for passage in passages]
            rerank_scores = cross_encoder.predict(query_passage_pairs)
            for i, score in enumerate(rerank_scores):
                matches[i]["rerank_score"] = float(score)
            
            reranked_matches = sorted(matches[:top_k_for_rerank], key=lambda x: x.get("rerank_score", 0.0), reverse=True)
            matches = reranked_matches + matches[top_k_for_rerank:]

        # 5. Select Chunks for Summarization and Prepare Response
        included_chunks, pending_chunk_ids = _select_included_and_pending(matches, included_limit=25)
        included_chunk_ids = [c.get("id") for c in included_chunks if c.get("id")]

        summary = None
        summary_was_partial = False

        if request.response_mode == "summary" and included_chunks:
            # Build context for summarization
            annotated_texts = []
            for idx, chunk in enumerate(included_chunks, start=1):
                score = chunk.get("rerank_score") or chunk.get("combined_score") or chunk.get("score", 0.0)
                header = f"[#{idx} id={chunk.get('id')} file={chunk.get('file_name')} page={chunk.get('page_number')} score={score:.4f}]"
                body = (chunk.get("content", "") or "")[:3000] # Truncate
                annotated_texts.append(f"{header}\n{body}")
            
            top_text = trim_texts_to_token_limit(annotated_texts, 220_000, model="gpt-4-turbo-preview")

            if top_text:
                summary_prompt_messages = [
                    {"role": "system", "content": "You are an insightful research assistant. Read the provided document chunks and produce a concise, accurate synthesis that directly answers the user's query. Cite evidence using the chunk ids (id=...)."},
                    {"role": "user", "content": f"User query: {user_prompt}\n\nSearch results:\n{top_text}\n\nPlease provide a detailed summary based on these results."}
                ]
                summary, summary_was_partial = stream_chat_completion(summary_prompt_messages, model="gpt-4-turbo-preview", max_tokens=4096)

        # 6. Format Sources
        sources = []
        for c in included_chunks:
            sources.append({
                "id": c.get("id"),
                "file_name": c.get("file_name"),
                "page_number": c.get("page_number"),
                "score": c.get("rerank_score") or c.get("combined_score") or c.get("score"),
                "excerpt": (c.get("content", "") or "").strip().replace("\n", " ")[:300]
            })

        # 7. Construct Final Response
        response_data = {
            "summary": summary,
            "summary_was_partial": summary_was_partial,
            "sources": sources,
            "can_resume": bool(pending_chunk_ids),
            "pending_chunk_ids": pending_chunk_ids,
            "included_chunk_ids": included_chunk_ids,
        }

        if request.response_mode == "structured_results":
            response_data["retrieved_chunks"] = included_chunks

        return response_data

    except Exception as e:
        # Log the full error for debugging
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
