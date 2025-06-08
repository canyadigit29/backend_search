from app.core.openai_client import chat_completion
import sys

def extract_answer_from_chunks(user_query: str, chunks: list, file_names: list = None) -> str:
    # Compose a prompt for the LLM to extract relevant events/answers from the chunks
    context = "\n\n".join(chunks)
    system_prompt = (
        "You are an expert assistant. Given the following user query and context, extract all instances where the query is answered, especially events, behaviors, or patterns. "
        "For each instance, provide the date (if available), a quote or summary, and the document name if available. "
        "If nothing is found, say 'No relevant information found.'"
    )
    user_prompt = f"User query: {user_query}\n\nContext:\n{context}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    print(f"[DEBUG] LLM answer extraction prompt: {user_prompt[:500]}...", file=sys.stderr)
    result = chat_completion(messages, model="gpt-4o")
    print(f"[DEBUG] LLM answer extraction result: {result[:500]}...", file=sys.stderr)
    return result
