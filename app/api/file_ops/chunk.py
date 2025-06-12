import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken
import calendar

from app.core.extract_text import extract_text  # Assumed
from app.core.supabase_client import supabase

def detect_section_headers(paragraphs):
    # Simple heuristic: all-caps lines, lines with numbers, or lines ending with ':'
    section_headers = {}
    current_section = None
    for idx, para in enumerate(paragraphs):
        line = para.strip()
        if (
            (line.isupper() and len(line) > 5) or
            re.match(r'^[A-Z][A-Za-z0-9 .\-:]{0,80}:$', line) or
            re.match(r'^(ARTICLE|SECTION|CHAPTER|PART|SUBPART|TITLE) [A-Z0-9 .-]+', line)
        ):
            current_section = line
        section_headers[idx] = current_section
    return section_headers

def extract_metadata_from_filename(file_name):
    # Example: 'Ordinance_2023-05-12_Title_of_Ordinance.pdf'
    meta = {}
    # Document type (e.g., Ordinance, Agenda, Minutes, etc.)
    doc_type_match = re.match(r'(Ordinance|Agenda|Minutes|Resolution|Misc)', file_name, re.IGNORECASE)
    if doc_type_match:
        meta['document_type'] = doc_type_match.group(1)
    # Date (YYYY-MM-DD or YYYY_MM_DD or YYYYMMDD)
    date_match = re.search(r'(\d{4})[-_]?([01]?\d)[-_]?([0-3]?\d)', file_name)
    if date_match:
        meta['meeting_year'] = int(date_match.group(1))
        meta['meeting_month'] = int(date_match.group(2))
        meta['meeting_month_name'] = calendar.month_name[int(date_match.group(2))]
        meta['meeting_day'] = int(date_match.group(3))
    # Ordinance/Resolution title (after date)
    title_match = re.search(r'\d{4}[-_][01]?\d[-_][0-3]?\d[_-](.*)\.', file_name)
    if title_match:
        meta['ordinance_title'] = title_match.group(1).replace('_', ' ').replace('-', ' ').strip()
    # File extension
    ext_match = re.search(r'\.([a-zA-Z0-9]+)$', file_name)
    if ext_match:
        meta['file_extension'] = ext_match.group(1).lower()
    return meta

def parse_page_markers(paragraphs):
    """
    Returns a dict mapping paragraph index to page number, based on ---PAGE N--- markers.
    """
    page_number = 1
    para_to_page = {}
    for idx, para in enumerate(paragraphs):
        if re.match(r"---PAGE (\d+)---", para.strip()):
            m = re.match(r"---PAGE (\d+)---", para.strip())
            page_number = int(m.group(1))
        para_to_page[idx] = page_number
    return para_to_page

def smart_chunk(text, max_tokens=1000, overlap_tokens=200):
    encoding = tiktoken.get_encoding("cl100k_base")
    paragraphs = text.split('\n')
    section_headers = detect_section_headers(paragraphs)
    para_to_page = parse_page_markers(paragraphs)
    # Group paragraphs by section
    section_groups = []
    current_section = None
    current_paragraphs = []
    current_para_indices = []
    for idx, para in enumerate(paragraphs):
        section = section_headers.get(idx)
        if section != current_section and current_paragraphs:
            section_groups.append((current_section, current_paragraphs, current_para_indices))
            current_paragraphs = []
            current_para_indices = []
        current_section = section
        current_paragraphs.append(para)
        current_para_indices.append(idx)
    if current_paragraphs:
        section_groups.append((current_section, current_paragraphs, current_para_indices))
    # Chunk within each section, but do not cross section boundaries
    chunks = []
    chunk_meta = []
    for section, paras, para_indices in section_groups:
        # Track page numbers for this section
        page_numbers = [para_to_page[i] for i in para_indices]
        # Tokenize section
        section_text = '\n'.join(paras)
        section_tokens = encoding.encode(section_text)
        total_tokens = len(section_tokens)
        start = 0
        while start < total_tokens:
            end = min(start + max_tokens, total_tokens)
            chunk_tokens = section_tokens[start:end]
            chunk_text = encoding.decode(chunk_tokens)
            # Determine which paragraphs are in this chunk
            chunk_paragraphs = []
            chunk_para_indices = []
            token_count = 0
            for para, idx in zip(paras, para_indices):
                para_tokens = encoding.encode(para)
                if token_count + len(para_tokens) > end:
                    break
                if token_count >= start:
                    chunk_paragraphs.append(para)
                    chunk_para_indices.append(idx)
                token_count += len(para_tokens)
            # Assign page number as the lowest page in this chunk
            chunk_page = min([para_to_page[i] for i in chunk_para_indices]) if chunk_para_indices else None
            chunk_meta.append({"section_header": section, "page_number": chunk_page})
            chunks.append(chunk_text)
            start += max_tokens - overlap_tokens
    return list(zip(chunks, chunk_meta))

def chunk_file(file_id: str, user_id: str = None):
    print(f"🔍 Starting chunking for file_id: {file_id}")
    try:
        file_entry = None
        is_uuid = re.fullmatch(r"[0-9a-fA-F\-]{36}", file_id)

        if is_uuid:
            result = supabase.table("files").select("*").eq("id", file_id).execute()
            file_entry = result.data[0] if result.data else None

        if not file_entry:
            print(f"❌ No file found for identifier: {file_id}")
            return []

        file_path = file_entry["file_path"]
        file_name = file_entry.get("file_name") or file_entry.get("name") or file_path
        actual_user_id = user_id or file_entry.get("user_id", None)
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "files")
        print(f"📄 Filepath: {file_path}")

        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            print(f"❌ Could not download file from Supabase: {file_path}")
            return []

        local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
        with open(local_temp_path, "wb") as f:
            f.write(response)

        try:
            text = extract_text(local_temp_path)
            print(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
        except Exception as e:
            print(f"❌ Failed to extract text from {file_path}: {str(e)}")
            return []

        # Extract metadata from filename
        filename_meta = extract_metadata_from_filename(Path(file_name).name)
        # Use improved smart_chunk logic with section/page metadata
        chunk_tuples = smart_chunk(text, max_tokens=1000, overlap_tokens=200)
        db_chunks = []
        for i, (chunk_text, meta) in enumerate(chunk_tuples):
            chunk_id = str(uuid4())
            # Merge all metadata
            chunk_metadata = {**filename_meta, **meta}
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "file_name": file_name,
                "content": chunk_text,
                "chunk_index": i,
                **chunk_metadata,
            }
            if actual_user_id:
                chunk["user_id"] = actual_user_id
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} semantic-aware chunks from {file_path}")

        return db_chunks  # Return full chunk dicts, do not insert here

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        return []
