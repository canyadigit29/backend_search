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
from app.core.answer_builder import build_structured_answer

router = APIRouter()

openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise RuntimeError("OPENAI_API_KEY not set in environment")

client = OpenAI(api_key=openai_api_key)

class ChatRequest(BaseModel):
    user_prompt: str
    session_id: str
    previous_chunks: list = None  # Optional, for follow-up queries

@router.post("/chat")
async def chat_with_context(request: Request):
    # --- Special command: run_score_test ---
    try:
        data = await request.json()
        if isinstance(data, dict) and data.get("command") == "run_score_test":
            from app.api.file_ops.search_docs import perform_search
            rows = supabase.table("document_chunks").select("content").limit(20).execute().data
            sample_contents = [row["content"] for row in rows if row.get("content")]
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
            # --- Iterative LLM-in-the-loop tuning ---
            thresholds = [0.2, 0.3, 0.4, 0.5, 0.6]
            alpha_beta_grid = [(a, 1-a) for a in [0.5, 0.6, 0.7, 0.8]]
            max_attempts = 10
            attempt = 0
            best_score = -1
            best_params = None
            best_results = None
            history = []
            params = {"threshold": 0.3, "alpha": 0.7, "beta": 0.3}
            while attempt < max_attempts:
                attempt += 1
                all_llm_scores = []
                all_llm_feedback = []
                for query in sample_queries:
                    tool_args = {
                        "embedding": None,
                        "user_id_filter": None,
                        "user_prompt": query,
                        "search_query": query,
                        "match_threshold": params["threshold"],
                        "match_count": 10,
                        "alpha": params["alpha"],
                        "beta": params["beta"]
                    }
                    result = perform_search(tool_args)
                    chunks = result.get("retrieved_chunks", [])
                    # Pass all results to LLM for re-ranking
                    result_text = "\n\n".join([f"[{i+1}] {c.get('content','')[:300]}" for i, c in enumerate(chunks)])
                    llm_prompt = [
                        {"role": "system", "content": (
                            "You are an expert in retrieval-augmented generation (RAG) systems and search quality engineering. "
                            "Your job is to evaluate search results, rerank them for relevance, and suggest optimal search parameters (threshold, alpha, beta) within allowed ranges. "
                            "If results are insufficient, suggest a new query and parameters. "
                            "Only suggest values within these ranges: threshold [0.0, 1.0], alpha/beta [0.0, 1.0] and sum to 1.0. "
                            f"You may suggest changes for up to {max_attempts} attempts. "
                            "Here is the current perform_search function for your reference (Python):\n" +
                            """\n" +
                            open('app/api/file_ops/search_docs.py', encoding='utf-8').read().split('def perform_search')[1][:6000] +
                            "\n""" +
                            "Respond ONLY with a valid JSON object, no markdown, no code block, no explanation outside the JSON. "
                            "If you suggest any code changes, include them in a 'suggested_code_change' field as a string. "
                            "Example: {\"reranked\": [{{\"index\": int, \"score\": float}}], \"rerun_search\": true/false, \"new_query\": str, \"suggested_threshold\": float, \"suggested_alpha\": float, \"suggested_beta\": float, \"feedback\": str, \"suggested_code_change\": str}"
                        )},
                        {"role": "user", "content": f"Query: {query}\n\nResults:\n{result_text}\n\nCurrent params: threshold={params['threshold']}, alpha={params['alpha']}, beta={params['beta']}"}
                    ]
                    # Print LLM prompt and response to Railway log for debugging
                    print("[DEBUG] LLM prompt for run_score_test:\n" + llm_prompt[0]["content"][:1000] + "...", flush=True)
                    print("[DEBUG] LLM user message:\n" + llm_prompt[1]["content"][:1000] + "...", flush=True)
                    llm_response = chat_completion(llm_prompt, model="gpt-4o")
                    # Fix: Strip code block markers if present
                    llm_response_clean = llm_response.strip()
                    if llm_response_clean.startswith('```json'):
                        llm_response_clean = llm_response_clean[7:]
                    if llm_response_clean.startswith('```'):
                        llm_response_clean = llm_response_clean[3:]
                    if llm_response_clean.endswith('```'):
                        llm_response_clean = llm_response_clean[:-3]
                    print(f"[DEBUG] LLM response for run_score_test: {llm_response_clean[:1000]}...", flush=True)
                    try:
                        llm_json = json.loads(llm_response_clean)
                        reranked = llm_json.get("reranked", [])
                        feedback = llm_json.get("feedback", "")
                        suggested_threshold = llm_json.get("suggested_threshold", params["threshold"])
                        suggested_alpha = llm_json.get("suggested_alpha", params["alpha"])
                        suggested_beta = llm_json.get("suggested_beta", params["beta"])
                        # Apply new scores and order
                        for r in reranked:
                            idx = r["index"]
                            if 0 <= idx < len(chunks):
                                chunks[idx]["llm_score"] = r["score"]
                        chunks.sort(key=lambda x: x.get("llm_score", 0), reverse=True)
                        all_llm_scores.append(sum([r["score"] for r in reranked])/len(reranked) if reranked else 0)
                        all_llm_feedback.append(feedback)
                        # Update params for next attempt
                        params["threshold"] = suggested_threshold
                        params["alpha"] = suggested_alpha
                        params["beta"] = suggested_beta
                    except Exception as e:
                        all_llm_feedback.append(f"LLM response parse error: {e}, raw: {llm_response}")
                avg_score = sum(all_llm_scores)/len(all_llm_scores) if all_llm_scores else 0
                history.append({"attempt": attempt, "params": params.copy(), "avg_score": avg_score, "feedback": all_llm_feedback})
                if avg_score > best_score:
                    best_score = avg_score
                    best_params = params.copy()
                    best_results = {"queries": sample_queries, "chunks": chunks, "feedback": all_llm_feedback}
                # Stopping condition: perfect score or no improvement
                if avg_score >= 1.0 or attempt == max_attempts:
                    break
            stopping_reason = ""
            if avg_score >= 1.0:
                stopping_reason = "LLM was satisfied with the results and ended the test."
            elif attempt == max_attempts:
                stopping_reason = "Maximum number of test rounds reached."
            # Include stopping_reason in the full response
            # Compose a human-readable summary for the UI
            summary_lines = [
                "Score test completed!",
                f"{stopping_reason}",
                f"Best parameters found: threshold={best_params['threshold']:.2f}, alpha={best_params['alpha']:.2f}, beta={best_params['beta']:.2f}",
                f"Best average LLM score: {best_score:.2f}",
            ]
            if best_results and best_results.get("feedback"):
                summary_lines.append("\nLLM Feedback on final attempt:")
                for feedback in best_results["feedback"]:
                    summary_lines.append(f"- {feedback}")
            # Final consolidated summary of best parameters and suggestions
            final_suggestion = None
            final_code_change = None
            final_new_query = None
            # Try to extract from the last attempt with feedback as dict (if LLM returned structured feedback)
            for entry in reversed(history):
                for fb in entry.get("feedback", []):
                    if isinstance(fb, dict):
                        if fb.get("suggested_code_change") and not final_code_change:
                            final_code_change = fb["suggested_code_change"]
                        if fb.get("new_query") and not final_new_query:
                            final_new_query = fb["new_query"]
                        if not final_suggestion:
                            final_suggestion = fb
            # Compose a clear summary
            summary_lines.append("\nFinal LLM Parameter Suggestion:")
            summary_lines.append(f"  threshold: {best_params['threshold']:.2f}")
            summary_lines.append(f"  alpha: {best_params['alpha']:.2f}")
            summary_lines.append(f"  beta: {best_params['beta']:.2f}")
            if final_new_query:
                summary_lines.append(f"  new_query: {final_new_query}")
            if final_code_change:
                summary_lines.append(f"  suggested_code_change:\n{final_code_change}")
            summary = "\n".join(summary_lines)
            # Print the summary to the Railway log for debugging
            print("\n" + summary + "\n", flush=True)
            # Print the final consolidated summary to the Railway log
            print("\n[DEBUG] Final consolidated summary for run_score_test:\n" + summary + "\n", flush=True)
            return {"content": summary}
    except Exception as e:
        return {"error": f"Score test failed: {e}"}

    # --- Normal chat request: parse as ChatRequest ---
    try:
        data = await request.json()
        payload = ChatRequest(**data)
        prompt = payload.user_prompt.strip()

        # user_id removed from requests: searches are global

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
            "user_id_filter": None,
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
            # --- Chronological ordering for summaries/histories ---
            def chunk_date_key(chunk):
                # Use year, month, day if available, else fallback to 0
                y = chunk.get('meeting_year') or 0
                m = chunk.get('meeting_month') or 0
                d = chunk.get('meeting_day') or 0
                return (y, m, d)
            # Sort top N relevant chunks by date for summary/history
            sorted_chunks = sorted(chunks, key=lambda x: x.get('score', 0), reverse=True)
            top_n = 50  # Reduce max results to top 50 for summary/history
            top_relevant = sorted_chunks[:top_n]
            top_chronological = sorted(top_relevant, key=chunk_date_key)
            top_texts = []
            total_chars = 0
            for chunk in top_chronological:
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
                        "You are an insightful, engaging, and helpful assistant with a sharp wit and a touch of cynicism. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible, but don't be afraid to show some personality, sarcasm, or smart-ass commentary when appropriate.\n"
                        "- Focus on information directly relevant to the user's question, but feel free to synthesize, interpret, and connect the dots.\n"
                        "- If there are patterns, trends, or notable points, highlight them and explain their significance—bonus points for dry humor or clever observations.\n"
                        "- Use a conversational, engaging tone, and don't shy away from a little snark if the situation calls for it.\n"
                        "- Use bullet points, sections, or narrative as you see fit for clarity and impact.\n"
                        "- Reference file names, dates, or section headers where possible.\n"
                        "- Do not add information that is not present in the results, but you may offer thoughtful analysis, context, or commentary based on what is present.\n"
                        "- If the results are lengthy, provide a high-level summary first, then details.\n"
                        "- Your goal is to be genuinely helpful, insightful, and memorable—not just a calculator.\n"
                        "- If you find any information even partially related to the query, summarize it directly. Avoid saying 'no direct reference' if there are any relevant details present.\n"
                        "- If the answer is obvious, feel free to point it out with a bit of attitude. If it's missing, don't be afraid to roll your eyes in text."
                    )},
                    {"role": "user", "content": f"User query: {prompt}\n\nSearch results:\n{top_text}"}
                ]
                summary = chat_completion(summary_prompt, model="gpt-4o")
            else:
                pass
        except Exception as e:
            summary = None

        # --- Modular LLM answer synthesis (structured answer with citations) ---
        structured_answer = build_structured_answer(
            user_query=prompt,
            chunks=chunks,
            style="conversational",
            include_citations=True,
            extra_instructions=None,
        )
        # --- LLM-driven parameter tuning: rerank and suggest new params ---
        # Step 1: Run initial search as before (already done above)
        # Step 2: Ask LLM to rerank and suggest new params
        result_text = "\n\n".join([f"[{i+1}] {c.get('content','')[:300]}" for i, c in enumerate(chunks)])
        llm_prompt = [
            {"role": "system", "content": (
                "You are an expert search quality evaluator and parameter tuner. Given a user query and the top 10-100 search results, re-rank the results for best relevance, assign a new score (0-1) to each, and if the results are not relevant or sufficient, provide a new search query and/or new search parameters (threshold, alpha, beta) to improve the results. Respond ONLY with a valid JSON object, no markdown, no code block, no explanation outside the JSON. Example: {\"reranked\": [{{\"index\": int, \"score\": float}}], \"rerun_search\": true/false, \"new_query\": str, \"suggested_threshold\": float, \"suggested_alpha\": float, \"suggested_beta\": float, \"feedback\": str}"
            )},
            {"role": "user", "content": f"Query: {prompt}\n\nResults:\n{result_text}\n\nCurrent params: threshold=0.6, alpha=0.9, beta=0.1"}
        ]
        llm_response = chat_completion(llm_prompt, model="gpt-4o")
        llm_response_clean = llm_response.strip()
        if llm_response_clean.startswith('```json'):
            llm_response_clean = llm_response_clean[7:]
        if llm_response_clean.startswith('```'):
            llm_response_clean = llm_response_clean[3:]
        if llm_response_clean.endswith('```'):
            llm_response_clean = llm_response_clean[:-3]
        try:
            llm_json = json.loads(llm_response_clean)
            reranked = llm_json.get("reranked", [])
            feedback = llm_json.get("feedback", "")
            rerun_search = llm_json.get("rerun_search", False)
            new_query = llm_json.get("new_query", None)
            suggested_threshold = llm_json.get("suggested_threshold", 0.6)
            suggested_alpha = llm_json.get("suggested_alpha", 0.9)
            suggested_beta = llm_json.get("suggested_beta", 0.1)

            # Validate and clamp parameters
            def clamp(val, minv, maxv):
                try:
                    return max(minv, min(maxv, float(val)))
                except Exception:
                    return minv
            suggested_threshold = clamp(suggested_threshold, 0.0, 1.0)
            suggested_alpha = clamp(suggested_alpha, 0.0, 1.0)
            suggested_beta = clamp(suggested_beta, 0.0, 1.0)
            # Optionally normalize alpha/beta to sum to 1
            total = suggested_alpha + suggested_beta
            if total > 0:
                suggested_alpha /= total
                suggested_beta /= total
            else:
                suggested_alpha, suggested_beta = 0.7, 0.3

            # Apply new scores and order
            for r in reranked:
                idx = r.get("index")
                score = r.get("score")
                if isinstance(idx, int) and 0 <= idx < len(chunks) and isinstance(score, (int, float)):
                    chunks[idx]["llm_score"] = clamp(score, 0.0, 1.0)
            chunks.sort(key=lambda x: x.get("llm_score", 0), reverse=True)

            # If LLM says to rerun search, do it automatically (only once)
            if rerun_search and new_query and isinstance(new_query, str) and new_query.strip():
                print(f"[DEBUG] LLM requested rerun with new query: {new_query}", flush=True)
                tool_args["search_query"] = new_query.strip()
                tool_args["embedding"] = embed_text(new_query.strip())
                tool_args["match_threshold"] = suggested_threshold
                tool_args["alpha"] = suggested_alpha
                tool_args["beta"] = suggested_beta
                semantic_result = perform_search(tool_args)
                chunks = semantic_result.get("retrieved_chunks", [])
                if not chunks:
                    print("[DEBUG] Rerun search returned no results.", flush=True)
        except Exception as e:
            feedback = f"LLM response parse error: {e}, raw: {llm_response}"
        # --- Alias Extraction and Query Expansion ---
        # Step 1: Run initial search as before (already done above)
        # Step 2: Ask LLM to extract aliases/identifiers from the top results
        alias_extraction_prompt = [
            {"role": "system", "content": (
                "You are an expert at entity and alias extraction. Given a user query and the top search results, identify any alternate names, roles, titles, legal names, addresses, or identifiers that refer to the same person, place, or thing as the original query. "
                "Return a JSON list of all discovered aliases or identifiers, excluding the original query term. If none are found, return an empty list. Example: [\"Wellspring Church Building\", \"the solicitor\", \"123 Main St\"]"
            )},
            {"role": "user", "content": f"Query: {prompt}\n\nSearch results:\n{top_text}"}
        ]
        alias_response = chat_completion(alias_extraction_prompt, model="gpt-4o")
        try:
            alias_list = json.loads(alias_response)
        except Exception:
            alias_list = []
        # Step 3: If aliases found, expand the search
        expanded_chunks = chunks
        if alias_list:
            expanded_query = f"{search_query} OR " + " OR ".join([f'\"{alias}\"' for alias in alias_list])
            try:
                expanded_embedding = embed_text(expanded_query)
            except Exception as e:
                expanded_embedding = embedding
            expanded_tool_args = tool_args.copy()
            expanded_tool_args["embedding"] = expanded_embedding
            expanded_tool_args["search_query"] = expanded_query
            expanded_result = perform_search(expanded_tool_args)
            expanded_chunks = expanded_result.get("retrieved_chunks", [])
        # Use expanded_chunks for summary/history
        chunks = expanded_chunks
        # --- Multi-round LLM parameter tuning for live queries ---
        max_tuning_rounds = 3  # You can adjust this for more/less rounds
        params = {"threshold": 0.5, "alpha": 0.7, "beta": 0.3}
        best_chunks = chunks
        best_score = -1
        for tuning_round in range(max_tuning_rounds):
            # Prepare LLM prompt for reranking and parameter suggestion
            result_text = "\n\n".join([f"[{i+1}] {c.get('content','')[:300]}" for i, c in enumerate(best_chunks)])
            llm_prompt = [
                {"role": "system", "content": "You are an expert search quality evaluator and parameter tuner. Given a user query and the top 50 search results, re-rank the results for best relevance, assign a new score (0-1) to each, and suggest new values for the similarity threshold and hybrid weights (alpha for semantic, beta for keyword) if you think results can be improved. Respond ONLY with a valid JSON object, no markdown, no code block, no explanation outside the JSON. Example: {\"reranked\": [{{\"index\": int, \"score\": float}}], \"suggested_threshold\": float, \"suggested_alpha\": float, \"suggested_beta\": float, \"feedback\": str}"},
                {"role": "user", "content": f"Query: {prompt}\n\nResults:\n{result_text}\n\nCurrent params: threshold={params['threshold']}, alpha={params['alpha']}, beta={params['beta']}"}
            ]
            llm_response = chat_completion(llm_prompt, model="gpt-4o")
            llm_response_clean = llm_response.strip()
            if llm_response_clean.startswith('```json'):
                llm_response_clean = llm_response_clean[7:]
            if llm_response_clean.startswith('```'):
                llm_response_clean = llm_response_clean[3:]
            if llm_response_clean.endswith('```'):
                llm_response_clean = llm_response_clean[:-3]
            try:
                llm_json = json.loads(llm_response_clean)
                reranked = llm_json.get("reranked", [])
                feedback = llm_json.get("feedback", "")
                suggested_threshold = llm_json.get("suggested_threshold", params["threshold"])
                suggested_alpha = llm_json.get("suggested_alpha", params["alpha"])
                suggested_beta = llm_json.get("suggested_beta", params["beta"])
                # Apply new scores and order
                for r in reranked:
                    idx = r["index"]
                    if 0 <= idx < len(best_chunks):
                        best_chunks[idx]["llm_score"] = r["score"]
                best_chunks.sort(key=lambda x: x.get("llm_score", 0), reverse=True)
                # Update params for next round
                params["threshold"] = suggested_threshold
                params["alpha"] = suggested_alpha
                params["beta"] = suggested_beta
                # Optionally, re-run search with new params (semantic search)
                tool_args["match_threshold"] = params["threshold"]
                tool_args["alpha"] = params["alpha"]
                tool_args["beta"] = params["beta"]
                semantic_result = perform_search(tool_args)
                best_chunks = semantic_result.get("retrieved_chunks", [])
            except Exception as e:
                feedback = f"LLM response parse error: {e}, raw: {llm_response}"
                break
        chunks = best_chunks
        return {
            "retrieved_chunks": chunks,
            "summary": summary,
            "extracted_query": extracted_query,
            "llm_feedback": feedback,
            "structured_answer": structured_answer
        }

    except Exception as e:
        return {"error": str(e)}
