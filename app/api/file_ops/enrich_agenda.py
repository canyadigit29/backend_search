# FastAPI endpoint for agenda enrichment
from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse
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

def is_lettered_or_numbered_header(line):
    import re
    stripped = line.strip()
    # Match patterns like 'A. ...', 'B. ...', '1. ...', '2. ...', etc.
    return bool(re.match(r'^[A-Z]\.[ \t].+', stripped)) or bool(re.match(r'^\d+\.[ \t].+', stripped))

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
        # Filter out lettered/numbered headers
        header_lines = [h for h in header_lines if not is_lettered_or_numbered_header(h)]
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
                ((stripped.isupper() and len(stripped) > 3) or
                stripped.endswith(":") or
                (i > 0 and lines[i-1].strip() == "" and (i+1 < len(lines) and lines[i+1].strip() == "")))
                and not is_lettered_or_numbered_header(stripped)
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
    def extract_groups_and_subtopics(text):
        import re
        lines = text.splitlines()
        groups = []
        current_group = None
        for line in lines:
            if re.match(r"^\d+\. ", line.strip()):
                if current_group:
                    groups.append(current_group)
                current_group = {"group_title": line.strip(), "lines": [], "subtopics": []}
            elif current_group is not None:
                current_group["lines"].append(line)
        if current_group:
            groups.append(current_group)
        # Now, for each group, extract subtopics (lettered or bulleted lines)
        for group in groups:
            subtopics = []
            current_sub = None
            for line in group["lines"]:
                if re.match(r"^[A-Z]\. ", line.strip()) or re.match(r"^[-*] ", line.strip()):
                    if current_sub:
                        subtopics.append(current_sub)
                    current_sub = {"title": line.strip(), "lines": []}
                elif current_sub is not None:
                    current_sub["lines"].append(line)
            if current_sub:
                subtopics.append(current_sub)
            group["subtopics"] = subtopics
        return groups

    text_groups = extract_groups_and_subtopics(text)
    from app.api.file_ops.embed import embed_text
    import json
    results = []
    for group in text_groups:
        group_text = "\n".join(group["lines"])
        # Summarize group
        group_summary_prompt = [
            {"role": "system", "content": "You are an expert at summarizing meeting agenda sections. Summarize the following agenda group for the user."},
            {"role": "user", "content": group_text}
        ]
        try:
            group_summary = chat_completion(group_summary_prompt)
        except Exception:
            group_summary = ""
        group_result = {
            "group_title": group["group_title"],
            "summary": group_summary,
            "subtopics": []
        }
        for sub in group["subtopics"]:
            sub_text = "\n".join(sub["lines"])
            # Summarize subtopic
            sub_summary_prompt = [
                {"role": "system", "content": "You are an expert at summarizing meeting agenda topics. Summarize the following agenda subtopic for the user."},
                {"role": "user", "content": sub_text}
            ]
            try:
                sub_summary = chat_completion(sub_summary_prompt)
            except Exception:
                sub_summary = ""
            # Retrieve source chunks for subtopic
            try:
                embedding = embed_text(sub["title"])
                search_args = {"embedding": embedding, "user_id_filter": user_id}
                history = perform_search(search_args)
                retrieved_chunks = history.get('retrieved_chunks', [])
                top_chunks = sorted(retrieved_chunks, key=lambda x: x.get('score', 0), reverse=True)[:10]
            except Exception:
                top_chunks = []
            group_result["subtopics"].append({
                "title": sub["title"],
                "summary": sub_summary,
                "sources": top_chunks
            })
        results.append(group_result)
    return JSONResponse(content=results)
