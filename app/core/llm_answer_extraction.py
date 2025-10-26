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
    result = chat_completion(messages, model="gpt-5")
    return result

def extract_answer_from_chunks_batched(user_query: str, chunks: list, file_names: list = None, batch_size: int = 20) -> str:
    """
    Process all chunks in batches, run LLM answer extraction on each batch, then combine and synthesize a final answer.
    """
    import math
    all_batch_answers = []
    total_batches = math.ceil(len(chunks) / batch_size)
    for i in range(total_batches):
        batch_chunks = chunks[i * batch_size : (i + 1) * batch_size]
        batch_file_names = file_names[i * batch_size : (i + 1) * batch_size] if file_names else None
        batch_answer = extract_answer_from_chunks(user_query, batch_chunks, file_names=batch_file_names)
        all_batch_answers.append(batch_answer)
    # Synthesize a final answer from all batch answers
    final_answer = extract_answer_from_chunks(user_query, all_batch_answers)
    return final_answer
