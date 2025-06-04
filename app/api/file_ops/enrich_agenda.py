# FastAPI endpoint for agenda enrichment
from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
import os
import tempfile
from app.core.extract_text import extract_text
from app.api.file_ops.search_docs import perform_search
from app.core.openai_client import chat_completion
import mammoth
import pdfplumber

router = APIRouter()

IMPORTANT_SECTIONS = [
    "Presentation of bills, petitions, remonstrances, communications, memorials",
    "Report of Borough Order.",
    "Report of General Government Committee.",
    "Report of Protection Committee.",
    "Report of Public Services Committee.",
    "Reports of Special Committees in the order of their appointments.",
    "Unfinished business of previous meetings.",
    "New business."
]

def extract_sections(text):
    # Use LLM to determine section headers from the agenda text
    from app.core.openai_client import chat_completion
    import re
    # Prompt the LLM to extract section headers
    prompt = [
        {"role": "system", "content": "You are an expert at analyzing meeting agendas. Given the full text of an agenda, return a JSON array of the exact lines that should be treated as section headers. Only return the JSON array, nothing else."},
        {"role": "user", "content": text[:6000]}  # Truncate to avoid token limits
    ]
    try:
        import json
        llm_response = chat_completion(prompt)
        header_lines = json.loads(llm_response)
        if not isinstance(header_lines, list):
            raise ValueError("LLM did not return a list")
        # Split text into sections using the detected headers
        sections = {}
        current = None
        for line in text.splitlines():
            if line.strip() in header_lines:
                current = line.strip()
                sections[current] = []
            if current:
                sections[current].append(line)
        if not sections:
            raise ValueError("No sections found by LLM")
        return sections
    except Exception as e:
        print(f"[extract_sections] LLM section header detection failed: {e}. Falling back to heuristic.")
        # Fallback to previous heuristic
        sections = {}
        current = None
        lines = text.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_header = (
                (stripped.isupper() and len(stripped) > 3) or
                stripped.endswith(":") or
                (i > 0 and lines[i-1].strip() == "" and (i+1 < len(lines) and lines[i+1].strip() == ""))
            )
            if is_header:
                current = stripped
                sections[current] = []
            if current:
                sections[current].append(line)
        if not sections:
            sections["Full Document"] = lines
        return sections

@router.post("/file_ops/enrich_agenda")
async def enrich_agenda(file: UploadFile = File(...)):
    # Save uploaded file to temp
    suffix = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # Extract text
    if suffix == ".pdf":
        text = extract_text(tmp_path)
    elif suffix in [".docx", ".doc"]:
        with open(tmp_path, "rb") as docx_file:
            result = mammoth.extract_raw_text(docx_file)
            text = result.value
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    # Extract agenda sections
    sections = extract_sections(text)
    enriched_sections = {}
    for section, lines in sections.items():
        topic = " ".join(lines)
        # Use perform_search to get history (reuse your backend search)
        search_result = perform_search({"query": section, "user_id_filter": "*"})
        # Summarize with LLM
        summary = chat_completion(f"Summarize the history of: {section} based on the following: {search_result}")
        enriched_sections[section] = (lines, summary)

    # Insert summaries into text
    enriched_text = ""
    for section, (lines, summary) in enriched_sections.items():
        enriched_text += "\n".join(lines)
        enriched_text += f"\n\n[History Summary]\n{summary}\n\n"

    # Write enriched text to new file
    enriched_path = tmp_path + "_enriched.txt"
    with open(enriched_path, "w", encoding="utf-8") as f:
        f.write(enriched_text)

    return FileResponse(enriched_path, filename="enriched_" + file.filename)
