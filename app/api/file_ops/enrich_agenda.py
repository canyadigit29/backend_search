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
    topic_sources = {}
    from app.api.file_ops.embed import embed_text
    import json
    for section, lines in sections.items():
        section_text = "\n".join(lines)
        # 1. Extract topics from the section using LLM
        topic_extraction_prompt = [
            {"role": "system", "content": (
                "You are an expert at analyzing meeting agendas. "
                "Given the following section of a meeting agenda, extract a JSON array of the main topics, motions, or action items discussed in this section. "
                "Skip any lines that are bolded or numbered (e.g., '1.', '2.', 'A.', 'B.', or all uppercase). "
                "Only output the actual discussion topics, not headers, not bolded lines, and not numbered list items. "
                "Only output the JSON array, no explanation."
            )},
            {"role": "user", "content": section_text}
        ]
        try:
            topics_json = chat_completion(topic_extraction_prompt)
            topics = json.loads(topics_json)
            if not isinstance(topics, list):
                raise ValueError("LLM did not return a list")
            # Fallback filter: remove topics that look like bolded/numbered/list items or all uppercase
            import re
            filtered_topics = []
            for t in topics:
                t_stripped = str(t).strip()
                if re.match(r"^(\d+\.|[A-Z]\.|[IVX]+\.|[a-z]\.|[•\-*])", t_stripped):
                    continue
                if t_stripped.isupper() and len(t_stripped) > 3:
                    continue
                filtered_topics.append(t_stripped)
            if not filtered_topics:
                filtered_topics = topics  # fallback to original if all filtered out
            print(f"[enrich_agenda] Extracted topics for section '{section}': {filtered_topics}")
            topics = filtered_topics
        except Exception as e:
            print(f"[enrich_agenda] Failed to extract topics for section '{section}': {e}")
            topics = [section]  # fallback: treat section as a single topic

        for topic in topics:
            # 2. Generate a smart search query for this topic (with section context)
            query_prompt = [
                {"role": "system", "content": "You are an expert assistant. Given the following section and topic from a meeting agenda, generate a concise search query that would retrieve the most relevant prior discussions or documents for this topic. Only output the search query, no explanation."},
                {"role": "user", "content": f"Section: {section}\nTopic: {topic}\nContent:\n{section_text}"}
            ]
            try:
                search_query = chat_completion(query_prompt).strip()
            except Exception as e:
                print(f"[enrich_agenda] Failed to generate search query for topic '{topic}': {e}")
                search_query = topic  # fallback

            # 3. Generate embedding and perform search
            try:
                embedding = embed_text(search_query)
            except Exception as e:
                print(f"[enrich_agenda] Failed to generate embedding for topic '{topic}': {e}")
                embedding = None

            search_args = {"embedding": embedding, "user_id_filter": user_id} if embedding is not None else {"user_id_filter": user_id}
            history = perform_search(search_args, prefer_recent=True)
            retrieved_chunks = history.get('retrieved_chunks', [])
            top_chunks = sorted(retrieved_chunks, key=lambda x: x.get('score', 0), reverse=True)[:10]

            # Track sources for this topic
            if section not in topic_sources:
                topic_sources[section] = {}
            if topic not in topic_sources[section]:
                topic_sources[section][topic] = set()
            for chunk in top_chunks:
                file_name = None
                if chunk.get('file_metadata') and chunk['file_metadata'].get('name'):
                    file_name = chunk['file_metadata']['name']
                elif chunk.get('name'):
                    file_name = chunk['name']
                if file_name:
                    import os
                    base = os.path.splitext(file_name)[0]
                    words = base.replace('_', ' ').split()
                    formatted = ' '.join(w.capitalize() for w in words)
                    topic_sources[section][topic].add(formatted)

            # Build history_text with source file names in bold
            def format_filename(name):
                import os
                base = os.path.splitext(name)[0]
                words = base.replace('_', ' ').split()
                return ' '.join(w.capitalize() for w in words)

            history_text = ""
            for chunk in top_chunks:
                file_name = None
                if chunk.get('file_metadata') and chunk['file_metadata'].get('name'):
                    file_name = chunk['file_metadata']['name']
                elif chunk.get('name'):
                    file_name = chunk['name']
                if file_name:
                    formatted = format_filename(file_name)
                    history_text += f"**{formatted}**\n"
                history_text += chunk.get('content', '') + "\n\n"

            # 4. Summarize/enrich
            prompt = [
                {"role": "system", "content": "You are an expert at summarizing documents. Given the user's instructions and the history of previous discussions, summarize the following agenda topic."},
                {"role": "user", "content": f"Instructions: {instructions}\n\nSection: {section}\n\nTopic: {topic}\n\nHistory: {history_text}\n\n"}
            ]
            try:
                llm_response = chat_completion(prompt)
                print(f"[enrich_agenda] LLM enrichment response for topic '{topic}': {llm_response}")
                if section not in enriched_sections:
                    enriched_sections[section] = {}
                enriched_sections[section][topic] = llm_response
            except Exception as e:
                print(f"[enrich_agenda] Failed to enrich topic '{topic}' with LLM: {e}")
                if section not in enriched_sections:
                    enriched_sections[section] = {}
                enriched_sections[section][topic] = topic  # fallback

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

    for section, topics in enriched_sections.items():
        pdf.set_font('Arial', 'B', 12)
        pdf.cell(0, 10, to_latin1(section), ln=True)
        pdf.set_font('Arial', '', 12)
        if isinstance(topics, dict):
            for topic, content in topics.items():
                pdf.set_font('Arial', 'B', 11)
                pdf.cell(0, 8, to_latin1(f"  - {topic}"), ln=True)
                pdf.set_font('Arial', '', 11)
                # Find the file sources for this topic (from top_chunks used in history_text)
                # We'll need to collect these during enrichment above, so add a dict: topic_sources[section][topic] = set([file_names])
                sources = topic_sources.get(section, {}).get(topic, set()) if 'topic_sources' in locals() else set()
                if sources:
                    pdf.set_font('Arial', 'B', 10)
                    for src in sorted(sources):
                        pdf.cell(0, 7, to_latin1(f"    Source: {src}"), ln=True)
                    pdf.set_font('Arial', '', 11)
                # Only show the summary (content), not the raw chunk text
                if isinstance(content, list):
                    text = "\n".join(content)
                else:
                    text = str(content)
                for line in text.split('\n'):
                    pdf.multi_cell(0, 8, to_latin1(line))
                pdf.ln(2)
        else:
            # fallback for old structure
            if isinstance(topics, list):
                text = "\n".join(topics)
            else:
                text = str(topics)
            for line in text.split('\n'):
                pdf.multi_cell(0, 8, to_latin1(line))
            pdf.ln(4)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as out_pdf:
        pdf.output(out_pdf.name)
        out_pdf_path = out_pdf.name

    return FileResponse(out_pdf_path, media_type="application/pdf", filename="enriched_agenda.pdf")
