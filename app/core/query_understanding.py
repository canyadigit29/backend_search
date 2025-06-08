from app.core.openai_client import chat_completion
import json

def extract_entities_and_intent(user_prompt: str) -> dict:
    system_prompt = (
        "You are an expert code assistant. "
        "Given a user query, extract the following:\n"
        "- intent: (e.g., 'how to', 'fix error', 'find example', 'explain', 'summarize')\n"
        "- entities: (list of function names, class names, file types, error codes, etc. mentioned)\n"
        "Respond in JSON: {\"intent\": ..., \"entities\": [...]}. "
        "If not found, use null or empty list."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    result = chat_completion(messages)
    print(f"[DEBUG] LLM query understanding for '{user_prompt}': {result}")
    try:
        return json.loads(result)
    except Exception:
        return {"intent": None, "entities": []}
