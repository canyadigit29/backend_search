from fastapi import APIRouter, HTTPException, Body
from app.core.openai_client import chat_completion
from app.core.supabase_client import supabase
from app.api.file_ops.search_docs import perform_search
from app.api.file_ops.embed import embed_text
import re
import json
from datetime import datetime

router = APIRouter()

def parse_date_from_filename(filename: str):
    # Example: 01-January-2022-Agenda-ocr or 01_January_2022_Agenda_ocr
    # Try to extract date in DD-Month-YYYY or DD_Month_YYYY
    match = re.search(r'(\d{2})[-_](\w+)[-_](\d{4})', filename)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        date = datetime.strptime(f"{day}-{month}-{year}", "%d-%B-%Y")
        return date
    except Exception:
        return None

@router.post("/item_history")
async def item_history(
    topic: str = Body(..., embed=True),
    user_id: str = Body(..., embed=True)
):
    """
    For a given topic, return all relevant chunks (with metadata) from previous meetings (agendas/minutes),
    so the frontend can display sources/files in the same way as normal semantic search.
    """
    try:
        embedding = embed_text(topic)
        # Semantic search
        rpc_args = {
            "query_embedding": embedding,
            "file_name_filter": None,
            "description_filter": None,
            "user_id_filter": user_id,
            "match_threshold": 0.3,
            "match_count": 1000
        }
        response = supabase.rpc("match_documents", rpc_args).execute()
        if getattr(response, "error", None):
            raise HTTPException(status_code=500, detail=f"Supabase RPC failed: {response.error.message}")
        semantic_matches = response.data or []
        # Only keep minutes files
        semantic_matches = [m for m in semantic_matches if re.search(r'minutes', m.get("file_name", ""), re.I)]
        # Keyword search (customizable)
        stopwords = {"the", "and", "of", "in", "to", "a", "for", "on", "at", "by", "with", "is", "as", "an", "be", "are", "was", "were", "it", "that", "from"}
        keywords = [w for w in re.split(r"\W+", topic or "") if w and w.lower() not in stopwords]
        from app.api.file_ops.search_docs import keyword_search
        keyword_results = keyword_search(keywords, user_id_filter=user_id)
        keyword_results = [k for k in keyword_results if re.search(r'minutes', k.get("file_name", ""), re.I)]
        # Hybrid merge/boost
        all_matches = {m["id"]: m for m in semantic_matches}
        phrase = topic.strip('"') if topic.startswith('"') and topic.endswith('"') else topic
        phrase_lower = phrase.lower()
        for k in keyword_results:
            content_lower = k.get("content", "").lower()
            orig_score = k.get("score", 0)
            num_words = len((phrase_lower or '').split())
            high_boost = (num_words <= 4) or (len(keyword_results) <= 3)
            if phrase_lower in content_lower:
                if high_boost:
                    k["score"] = orig_score + 4.0
                else:
                    k["score"] = orig_score + 0.08
                k["boosted_reason"] = "exact_phrase"
                k["original_score"] = orig_score
                all_matches[k["id"]] = k
            elif k["id"] in all_matches:
                prev_score = all_matches[k["id"]].get("score", 0)
                if prev_score < 1.0:
                    all_matches[k["id"]]["original_score"] = prev_score
                    all_matches[k["id"]]["score"] = prev_score + 1.0
                    all_matches[k["id"]]["boosted_reason"] = "keyword_overlap"
            else:
                k["score"] = orig_score + 0.5
                all_matches[k["id"]] = k
        matches = list(all_matches.values())
        matches.sort(key=lambda x: x.get("score", 0), reverse=True)
        # --- Two-pass approach: First, ask LLM to label each chunk as Relevant/Not Relevant ---
        if not matches:
            return {"history": [], "retrieved_chunks": []}
        chunk_texts = [f"File: {m.get('file_name', '')}\nChunk: {m.get('content', '')}" for m in matches]
        labeling_prompt = [
            {"role": "system", "content": (
                "You are an expert at reading meeting minutes. For each chunk below, determine if it contains information directly relevant to the topic. "
                "If it is relevant, respond with 'Relevant'. If not, respond with 'Not Relevant'. "
                "Respond with a JSON array of 'Relevant' or 'Not Relevant' for each chunk in order."
            )},
            {"role": "user", "content": f"Topic: {topic}\nChunks:\n" + "\n---\n".join(chunk_texts)[:12000]}
        ]
        labeling_response = chat_completion(labeling_prompt)
        try:
            relevance_labels = json.loads(labeling_response)
        except Exception:
            relevance_labels = ["Relevant"] * len(matches)  # fallback: treat all as relevant
        # Filter matches to only relevant ones
        relevant_matches = [m for m, label in zip(matches, relevance_labels) if label.lower() == "relevant"]
        if not relevant_matches:
            return {"history": [{"summary": "No relevant information found in any source."}], "retrieved_chunks": []}
        # --- Second pass: Summarize only relevant chunks ---
        all_text = "\n\n---\n\n".join(f"File: {m.get('file_name', '')}\nChunk: {m.get('content', '')}" for m in relevant_matches)
        prompt = [
            {"role": "system", "content": (
                "You are an expert at reading meeting minutes. The following are chunks of text from different meeting minutes files. "
                "Each chunk may be much larger than the information relevant to the topic. "
                "For each chunk, extract and summarize ONLY the information that is directly relevant to the topic. "
                "If a chunk contains no relevant information, do not include it in the summary. "
                "For each file/chunk, output a summary in the format: \nFile: <file_name>\nSummary: <summary of relevant info>. "
                "At the end, provide a brief overall summary of what was discussed about the topic across all files. "
                "Quote or extract the exact relevant sentences if possible."
            )},
            {"role": "user", "content": f"Topic: {topic}\nMeeting chunks:\n{all_text[:12000]}"}
        ]
        summary = chat_completion(prompt)
        # Add file_metadata to each chunk for frontend grouping
        for chunk in relevant_matches:
            if 'file_metadata' not in chunk:
                chunk['file_metadata'] = {
                    'id': chunk.get('file_id'),
                    'name': chunk.get('file_name'),
                    'type': 'pdf',
                }
        return {"history": [{"summary": summary}], "retrieved_chunks": relevant_matches}
    except Exception as e:
        print(f"[ERROR] item_history failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get item history: {str(e)}")
