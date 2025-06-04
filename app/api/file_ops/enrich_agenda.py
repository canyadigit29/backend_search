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
    # Simple rule-based extraction by section headers
    sections = {}
    current = None
    for line in text.splitlines():
        for header in IMPORTANT_SECTIONS:
            if header.lower() in line.lower():
                current = header
                sections[current] = []
        if current:
            sections[current].append(line)
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
