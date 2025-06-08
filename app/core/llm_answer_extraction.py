from app.core.openai_client import chat_completion
import sys

def extract_answer_from_chunks(user_query: str, chunks: list, file_names: list = None) -> str:
    # Compose a prompt for the LLM to interpret, synthesize, and provide nuanced, context-aware answers
    context = "\n\n".join(chunks)
    system_prompt = (
        "You are an expert assistant. Given the following user query and context, interpret and synthesize an answer using all relevant information. "
        "You may use reasoning, inference, and opinion if helpful, but always ground your answer in the provided context. "
        "Highlight patterns, behaviors, or events, and explain your reasoning if you make inferences. "
        "If nothing relevant is found, say 'No relevant information found.'"
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
