import os
import json
import requests
import tiktoken

import redis

from app.core.openai_client import chat_completion
from app.api.file_ops.search_docs import perform_search, MODEL_MAX_TOKENS, SUMMARY_RESPONSE_RESERVE, CHUNK_TOKEN_SAFETY_MARGIN, CHUNK_TOKEN_CAP

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL)

def process_search_job(tool_args, job_id, callback_url=None):
    """Worker function invoked by RQ. Performs the full search + summary and stores results in Redis."""
    key = f"search:results:{job_id}"
    try:
        redis_client.set(key, json.dumps({"status": "running"}))
        result = perform_search(tool_args)
        matches = result.get("retrieved_chunks", [])

        # Build summary using same budgeting logic as endpoint (no 59s constraint here)
        try:
            encoding = tiktoken.encoding_for_model("gpt-5")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")

        system_prompt_text = (
            "You are an insightful, engaging, and helpful assistant. Using only the following retrieved search results, answer the user's query as clearly and concisely as possible.\n"
            "- Focus on information directly relevant to the user's question. Synthesize and connect the dots only from the provided results.\n"
            "- Use bullet points, sections, or narrative as needed for clarity.\n"
            "- Reference file names, dates, or section headers where possible.\n"
            "- Do not add information not present in the results.\n"
        )
        user_wrapper_text = f"User query: {tool_args.get('user_prompt','')}\n\nSearch results:\n"

        model_max = MODEL_MAX_TOKENS
        reserve = SUMMARY_RESPONSE_RESERVE
        safety = CHUNK_TOKEN_SAFETY_MARGIN
        overhead_tokens = len(encoding.encode(system_prompt_text or "")) + len(encoding.encode(user_wrapper_text or ""))
        available_for_chunks = max(0, model_max - reserve - overhead_tokens - safety)
        available_for_chunks = min(available_for_chunks, CHUNK_TOKEN_CAP)
        top_texts = []
        total_tokens = 0
        for chunk in sorted(matches, key=lambda x: x.get("score", 0), reverse=True):
            content = chunk.get("content", "")
            if not content:
                continue
            chunk_tokens = len(encoding.encode(content))
            if total_tokens + chunk_tokens > available_for_chunks:
                break
            top_texts.append(content)
            total_tokens += chunk_tokens
        top_text = "\n\n".join(top_texts)

        summary = None
        if top_text.strip():
            summary_prompt = [
                {"role": "system", "content": system_prompt_text},
                {"role": "user", "content": f"{user_wrapper_text}{top_text}"}
            ]
            try:
                summary = chat_completion(summary_prompt, model="gpt-5")
                if isinstance(summary, str):
                    summary = summary.strip()
            except Exception:
                summary = None

        payload = {"status": "done", "retrieved_chunks": matches, "summary": summary}
        redis_client.set(key, json.dumps(payload))

        if callback_url:
            try:
                requests.post(callback_url, json=payload, timeout=10)
            except Exception:
                pass
    except Exception as e:
        redis_client.set(key, json.dumps({"status": "error", "error": str(e)}))
