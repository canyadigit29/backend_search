# llama_query_transform.py
"""
LlamaIndex-style query transformer for MaxGPT backend.
Supports LLM-based query rewriting and filter extraction in a single call.
"""
from app.core.openai_client import chat_completion
import json

LLAMA_QUERY_PROMPT = (
    "You are an expert search assistant. Given a user query, do the following as a JSON object:\n"
    "- 'intent': Briefly describe the user's intent.\n"
    "- 'query': Rewrite the query for best semantic search (concise, document-style, no instructions).\n"
    "- 'filters': Extract any metadata filters (document_type, year, section, etc.) as a JSON object.\n"
    "If a field is not present, use null or an empty object.\n"
)

def llama_query_transform(user_prompt: str) -> dict:
    messages = [
        {"role": "system", "content": LLAMA_QUERY_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    result = chat_completion(messages)
    try:
        return json.loads(result)
    except Exception:
        return {"intent": None, "query": user_prompt, "filters": {}}
