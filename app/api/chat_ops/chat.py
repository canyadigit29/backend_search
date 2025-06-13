import json
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel

from app.api.file_ops.search_docs import perform_search
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.core.llm_answer_extraction import extract_answer_from_chunks_batched

router = APIRouter()

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in environment")

client = OpenAI(api_key=openai_api_key)

class ChatRequest(BaseModel):
    user_prompt: str
    user_id: str
    session_id: str
    previous_chunks: list = None  # Optional, for follow-up queries

@router.post("/chat")
async def chat_with_context(request: Request):
    # --- Special command: run_score_test ---
    try:
        data = await request.json()
        if isinstance(data, dict) and data.get("command") == "run_score_test":
            from app.api.file_ops.search_docs import perform_search  # Ensure perform_search is in scope
            # Step 1: Sample real meeting minutes content from the database
            # We'll sample 10 random chunks (meeting minutes snippets)
            rows = supabase.table("document_chunks").select("content").limit(20).execute().data
            sample_contents = [row["content"] for row in rows if row.get("content")]
            # Step 2: Use LLM to generate realistic search queries based on these samples
            llm_query_prompt = [
                {"role": "system", "content": "You are an expert at writing search queries for a council meeting minutes search engine. Given the following real meeting minutes snippets, generate 10 realistic, diverse search queries that a typical user (including residents, journalists, and staff) might ask to find information in these minutes. Mix general, everyday, and practical questions with a few professional or technical ones, but avoid only highly specific or expert-level queries. Return only the queries as a numbered list."},
                {"role": "user", "content": "\n\n".join(sample_contents[:10])}
            ]
            queries_response = chat_completion(llm_query_prompt, model="gpt-4o")
            import re
            sample_queries = re.findall(r"\d+\.\s*(.+)", queries_response)
            if not sample_queries:
                # fallback: split by lines
                sample_queries = [q.strip("- ") for q in queries_response.split("\n") if q.strip()]
            if len(sample_queries) > 10:
                sample_queries = sample_queries[:10]
            thresholds = [0.2, 0.3, 0.4, 0.5, 0.6]
            ratings = {t: [] for t in thresholds}
            for query in sample_queries:
                for t in thresholds:
                    tool_args = {
                        "embedding": None,  # Will be generated in perform_search
                        "user_id_filter": data.get("user_id", ""),
                        "user_prompt": query,
                        "search_query": query,
                        "match_threshold": t,
                        "match_count": 10
                    }
                    result = perform_search(tool_args)
                    chunks = result.get("retrieved_chunks", [])
                    result_text = "\n\n".join([c.get("content", "")[:300] for c in chunks[:3]])
                    llm_prompt = [
                        {"role": "system", "content": "You are an expert search quality evaluator. Given a user query and the top 3 search results, rate the overall relevance and usefulness of the results on a scale from 1 (poor) to 5 (excellent). Respond only with a single integer rating and a one-sentence justification."},
                        {"role": "user", "content": f"Query: {query}\n\nResults:\n{result_text}"}
                    ]
                    llm_response = chat_completion(llm_prompt, model="gpt-4o")
                    match = re.search(r"([1-5])", llm_response)
                    if match:
                        ratings[t].append(int(match.group(1)))
            avg_ratings = {t: (sum(ratings[t])/len(ratings[t]) if ratings[t] else 0) for t in thresholds}
            best_threshold = max(avg_ratings, key=avg_ratings.get)
            recommendation = f"Recommended similarity threshold: {best_threshold:.2f} (LLM-evaluated).\n\nDetails: {avg_ratings}\n\nSample queries: {sample_queries}"
            return {"recommendation": recommendation}
    except Exception as e:
        return {"error": f"Score test failed: {e}"}

    # --- Normal chat request: parse as ChatRequest ---
    try:
        data = await request.json()
        payload = ChatRequest(**data)
        prompt = payload.user_prompt.strip()

        try:
            uuid.UUID(str(payload.user_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format. Must be a UUID.")

        # If previous_chunks are provided, this is a follow-up query. Use all previous chunks for LLM answer extraction.
        if payload.previous_chunks:
            # Use batching for large numbers of chunks
            answer = extract_answer_from_chunks_batched(prompt, [c.get("content", "") for c in payload.previous_chunks])
            return {"answer": answer, "used_chunks": len(payload.previous_chunks)}

        # LLM-based query extraction
        system_prompt = (
            "You are a helpful assistant. Extract the main topic, keywords, or search query from the user's request. "
            "Return only the search phrase or keywords, not instructions."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        extracted_query = chat_completion(messages)

        # Extract metadata filters from the prompt
        from app.core.query_understanding import extract_search_filters
        search_filters = extract_search_filters(prompt)

        # Use the same hybrid/boosted search logic as /file_ops/search_docs
        from app.api.file_ops.search_docs import embed_text, perform_search
        search_query = extracted_query
        try:
            embedding = embed_text(search_query)
        except Exception as e:
            return {"error": f"Failed to generate embedding: {e}"}
        tool_args = {
            "embedding": embedding,
            "user_id_filter": payload.user_id,
            "file_name_filter": search_filters.get("file_name"),
            "description_filter": search_filters.get("description"),
            "start_date": None,
            "end_date": None,
            "user_prompt": prompt,
            "search_query": search_query
        }
        # Add new metadata filters if present
        for meta_field in [
            "document_type", "meeting_year", "meeting_month", "meeting_month_name", "meeting_day", "ordinance_title", "file_extension", "section_header", "page_number"
        ]:
            if search_filters.get(meta_field) is not None:
                tool_args[meta_field] = search_filters[meta_field]
        # Debug logging for prompt and query extraction
        print(f"[DEBUG] User prompt: {prompt}", flush=True)
        print(f"[DEBUG] Extracted query: {extracted_query}", flush=True)
        print(f"[DEBUG] Embedding length: {len(embedding) if hasattr(embedding, '__len__') else 'unknown'}", flush=True)
        print(f"[DEBUG] Tool args: {{k: v for k, v in tool_args.items() if k != 'embedding'}}", flush=True)
        # --- Hybrid search: semantic + keyword ---
        semantic_result = perform_search(tool_args)
        chunks = semantic_result.get("retrieved_chunks", [])

        # --- LLM-based summary of top search results (mirroring /file_ops/search_docs.py) ---
        summary = None
        try:
            MAX_SUMMARY_CHARS = 60000
            sorted_chunks = sorted(chunks, key=lambda x: x.get("score", 0), reverse=True)
            top_texts = []
            total_chars = 0
            for chunk in sorted_chunks:
                content = chunk.get("content", "")
                if not content:
                    continue
                if total_chars + len(content) > MAX_SUMMARY_CHARS:
                    break
                top_texts.append(content)
                total_chars += len(content)
            top_text = "\n\n".join(top_texts)
            if top_text.strip():
                summary_prompt = [
                    {"role": "system", "content": (
                        "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible, but don't be afraid to show some personality and offer your own analysis or perspective.\n"
                        "- Focus on information directly relevant to the user's question, but feel free to synthesize, interpret, and connect the dots.\n"
                        "- If there are patterns, trends, or notable points, highlight them and explain their significance.\n"
                        "- Use a conversational, engaging tone.\n"
                        "- Use bullet points, sections, or narrative as you see fit for clarity and impact.\n"
                        "- Reference file names, dates, or section headers where possible.\n"
                        "- Do not add information that is not present in the results, but you may offer thoughtful analysis, context, or commentary based on what is present.\n"
                        "- If the results are lengthy, provide a high-level summary first, then details.\n"
                        "- Your goal is to be genuinely helpful, insightful, and memorable—not just a calculator."
                    )},
                    {"role": "user", "content": f"User query: {prompt}\n\nSearch results:\n{top_text}"}
                ]
                summary = chat_completion(summary_prompt, model="gpt-4o")
            else:
                pass
        except Exception as e:
            summary = None

        return {"retrieved_chunks": chunks, "summary": summary, "extracted_query": extracted_query}

    except Exception as e:
        return {"error": str(e)}
