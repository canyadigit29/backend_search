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
import unicodedata
from uuid import UUID

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
    instructions: str = Form(""),
    user_id: str = Form(...)
):
    # Validate user_id
    import uuid
    if not user_id or not isinstance(user_id, str):
        raise HTTPException(status_code=400, detail="user_id is required for enrichment.")
    try:
        uuid.UUID(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="user_id must be a valid UUID.")

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
        section_text = "\n".join(lines)
        # 1. Use LLM to generate a smart search query for this section
        query_prompt = [
            {"role": "system", "content": "You are an expert assistant. Given the following section of a meeting agenda, generate a concise search query that would retrieve the most relevant prior discussions or documents for this section. Only output the search query, no explanation."},
            {"role": "user", "content": f"Section: {section}\n\nContent:\n{section_text}"}
        ]
        try:
            search_query = chat_completion(query_prompt).strip()
        except Exception as e:
            print(f"[enrich_agenda] Failed to generate search query for section '{section}': {e}")
            search_query = section  # fallback

        # 2. Generate embedding for the LLM-generated query
        try:
            embedding = embed_text(search_query)
        except Exception as e:
            print(f"[enrich_agenda] Failed to generate embedding for section '{section}': {e}")
            embedding = None

        # 3. Perform search with the smart query embedding
        search_args = {"embedding": embedding, "user_id_filter": user_id} if embedding is not None else {"user_id_filter": user_id}
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

    # --- PDF GENERATION AND RETURN (using fpdf) ---
    from fpdf import FPDF

    class PDF(FPDF):
        def header(self):
            self.set_font('Arial', 'B', 14)
            self.cell(0, 10, 'Enriched Agenda', ln=True, align='C')
            self.ln(5)

    pdf = PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font('Arial', '', 12)

    def to_latin1(text):
        return unicodedata.normalize("NFKD", str(text)).encode("latin-1", "replace").decode("latin-1")

    for section, content in enriched_sections.items():
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, to_latin1(section), ln=True)
        pdf.set_font('Arial', '', 12)
        if isinstance(content, list):
            text = "\n".join(content)
        else:
            text = str(content)
        for line in text.split('\n'):
            pdf.multi_cell(0, 8, to_latin1(line))
        pdf.ln(4)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as out_pdf:
        pdf.output(out_pdf.name)
        out_pdf_path = out_pdf.name

    return FileResponse(out_pdf_path, media_type="application/pdf", filename="enriched_agenda.pdf")
