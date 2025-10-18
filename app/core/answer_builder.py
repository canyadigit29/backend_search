"""
answer_builder.py: Modular LLM answer synthesis for RAG pipelines.
- Supports structured answers, citations, and flexible formatting.
- Inspired by LlamaIndex's response synthesis nodes.
"""
from app.core.openai_client import chat_completion
from typing import List, Dict, Any


def build_structured_answer(
    user_query: str,
    chunks: List[Dict[str, Any]],
    style: str = "default",
    include_citations: bool = True,
    extra_instructions: str = None,
) -> Dict[str, Any]:
    """
    Synthesize a structured answer from retrieved chunks, optionally including citations and custom instructions.
    Returns a dict with 'answer', 'citations', and 'raw_chunks'.
    """
    context = "\n\n".join([c.get("content", "") for c in chunks])
    citation_map = {}
    for idx, c in enumerate(chunks):
        label = f"[{idx+1}]"
        citation_map[label] = {
            "file_name": c.get("file_name"),
            "section_header": c.get("section_header"),
            "meeting_date": c.get("meeting_date"),
            "score": c.get("score"),
        }
    system_prompt = (
        "You are a helpful assistant. Using only the provided context, answer the user's query as clearly and concisely as possible. "
        "Cite sources inline using [number] notation where relevant. "
        "If the answer is synthesized from multiple sources, cite all relevant ones. "
        "If no relevant information is found, say so. "
    )
    if style == "conversational":
        system_prompt += " Use a conversational, engaging tone."
    if extra_instructions:
        system_prompt += " " + extra_instructions
    user_prompt = f"User query: {user_query}\n\nContext:\n" + "\n\n".join([
        f"[{i+1}] {c.get('content','')}" for i, c in enumerate(chunks)
    ])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    answer = chat_completion(messages, model="gpt-5")
    # Optionally extract citations from answer (e.g., [1], [2])
    import re
    cited = set(re.findall(r"\[(\d+)\]", answer))
    citations = [citation_map.get(f"[{n}]") for n in cited if citation_map.get(f"[{n}]")]
    return {
        "answer": answer,
        "citations": citations if include_citations else None,
        "raw_chunks": chunks,
    }
