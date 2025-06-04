# FastAPI endpoint for agenda enrichment
from fastapi import APIRouter, File, UploadFile, HTTPException, Form
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
    import json
    prompt = [
        {"role": "system", "content": "You are an expert at analyzing meeting agendas. Given the full text of an agenda, return ONLY a JSON array of the exact lines that should be treated as section headers. Do not include any explanation, markdown, or extra text. Only output a JSON array."},
        {"role": "user", "content": text[:6000]}
    ]
    try:
        llm_response = chat_completion(prompt)
        print(f"[extract_sections] LLM raw response: {llm_response}")
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
async def enrich_agenda(
    file: UploadFile = File(...),
    instructions: str = Form("")
):
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

    # Use instructions to guide section extraction and summarization
    def extract_sections_with_instructions(text, instructions):
        from app.core.openai_client import chat_completion
        import json
        prompt = [
            {"role": "system", "content": f"You are an expert at analyzing documents. The user has provided these instructions for enrichment: '{instructions}'. Given the full text of a document, return ONLY a JSON array of the exact lines that should be treated as section headers, based on the user's instructions. Do not include any explanation, markdown, or extra text. Only output a JSON array."},
            {"role": "user", "content": text[:6000]}
        ]
        try:
            llm_response = chat_completion(prompt)
            print(f"[extract_sections_with_instructions] LLM raw response: {llm_response}")
            header_lines = json.loads(llm_response)
            if not isinstance(header_lines, list):
                raise ValueError("LLM did not return a list")
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
            print(f"[extract_sections_with_instructions] LLM section header detection failed: {e}. Falling back to heuristic.")
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

    sections = extract_sections_with_instructions(text, instructions)
    enriched_sections = {}
    from app.api.file_ops.embed import embed_text  # <-- Import embed_text for embedding generation
    for section, lines in sections.items():
        topic = " ".join(lines)
        # Generate embedding for the section
        try:
            embedding = embed_text(section)
        except Exception as e:
            print(f"[enrich_agenda] Failed to generate embedding for section '{section}': {e}")
            embedding = None
        # Use perform_search to get history (reuse your backend search)
        # Pass instructions to the LLM for summarization
        # Only pass embedding if it is not None, otherwise do not include it
        search_args = {"embedding": embedding, "user_id_filter": "*"} if embedding is not None else {"user_id_filter": "*"}
        history = perform_search(search_args)
        print(f"[enrich_agenda] Search history for section '{section}': {history}")
        # Enrich section with AI-generated summary and history
        prompt = [
            {"role": "system", "content": "You are an expert at summarizing documents. Given the user's instructions and the history of previous discussions, summarize the following section."},
            {"role": "user", "content": f"Instructions: {instructions}\n\nSection: {section}\n\nHistory: {history}\n\n"}
        ]
        try:
            llm_response = chat_completion(prompt)
            print(f"[enrich_agenda] LLM enrichment response for section '{section}': {llm_response}")
            enriched_sections[section] = llm_response
        except Exception as e:
            print(f"[enrich_agenda] Failed to enrich section '{section}' with LLM: {e}")
            enriched_sections[section] = lines  # Fallback to original lines

    # --- PDF GENERATION AND RETURN ---
    import io
    from pdfdocument.document import PDFDocument

    buffer = io.BytesIO()
    pdf = PDFDocument(buffer)
    pdf.init_report()
    for section, lines in enriched_sections.items():
        pdf.h2(section)
        pdf.p("\n".join(lines))
    pdf.generate()
    buffer.seek(0)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as out_pdf:
        out_pdf.write(buffer.read())
        out_pdf_path = out_pdf.name

    return FileResponse(out_pdf_path, media_type="application/pdf", filename="enriched_agenda.pdf")
