import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken
import calendar

from app.core.extract_text import extract_text, TextExtractionError
from app.core.supabase_client import supabase

def extract_metadata_from_filename(file_name):
    # ... (keep existing function)
    import re
    import calendar
    meta = {}
    name = file_name.rsplit('.', 1)[0].strip()
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    meta['file_extension'] = ext

    # Agendas
    m = re.match(r'([A-Za-z]+) (\d{4}) Agenda(?: ([A-Za-z ]+))?$', name)
    if m:
        meta['document_type'] = 'Agenda'
        meta['meeting_month_name'] = m.group(1).capitalize()
        try:
            meta['meeting_month'] = list(calendar.month_name).index(m.group(1).capitalize())
        except ValueError:
            meta['meeting_month'] = None
        meta['meeting_year'] = int(m.group(2))
        if m.group(3):
            meta['extra'] = m.group(3).strip()
        return meta

    # Minutes
    m = re.match(r'([A-Za-z]+) (\d{4}) Minutes(?: ([A-Za-z ]+))?$', name)
    if m:
        meta['document_type'] = 'Minutes'
        meta['meeting_month_name'] = m.group(1).capitalize()
        try:
            meta['meeting_month'] = list(calendar.month_name).index(m.group(1).capitalize())
        except ValueError:
            meta['meeting_month'] = None
        meta['meeting_year'] = int(m.group(2))
        if m.group(3):
            meta['extra'] = m.group(3).strip()
        return meta

    # Ordinaces
    m = re.match(r'(\d+)?\s*(.*?) Ordinance$', name)
    if m:
        meta['document_type'] = 'Ordinance'
        if m.group(1):
            meta['ordinance_number'] = m.group(1).strip()
        meta['ordinance_title'] = m.group(2).strip()
        return meta

    # Misc
    meta['document_type'] = 'Misc'
    meta['misc_title'] = name
    return meta

def parse_page_markers(paragraphs):
    # ... (keep existing function)
    page_number = 1
    para_to_page = {}
    for idx, para in enumerate(paragraphs):
        if re.match(r"---PAGE (\d+)---", para.strip()):
            m = re.match(r"---PAGE (\d+)---", para.strip())
            page_number = int(m.group(1))
        para_to_page[idx] = page_number
    return para_to_page

def fixed_size_chunk(text, max_tokens=1200, overlap_tokens=120):
    # ... (keep existing function)
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    total_tokens = len(tokens)
    chunks = []
    chunk_meta = []
    start = 0
    while start < total_tokens:
        end = min(start + max_tokens, total_tokens)
        chunk_tokens = tokens[start:end]
        chunk_text = encoding.decode(chunk_tokens)
        chunk_meta.append({"section_header": None, "page_number": None})
        chunks.append(chunk_text)
        start += max_tokens - overlap_tokens
    return list(zip(chunks, chunk_meta))

def chunk_file(file_id: str):
    print(f"🔍 Starting chunking for file_id: {file_id}")
    try:
        # ... (file lookup logic remains the same)
        file_entry = None
        is_uuid = re.fullmatch(r"[0-9a-fA-F\-]{36}", file_id)

        if is_uuid:
            result = supabase.table("files").select("*").eq("id", file_id).execute()
            file_entry = result.data[0] if result.data else None

        if not file_entry:
            print(f"❌ No file found for identifier: {file_id}")
            return {"error": "File not found."}

        file_path = file_entry["file_path"]
        file_name = file_entry.get("file_name") or file_entry.get("name") or file_path
        # ... (rest of the setup is the same)
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "files")
        print(f"📄 Filepath: {file_path}")

        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            print(f"❌ Could not download file from Supabase: {file_path}")
            return {"error": "Failed to download file from storage."}

        local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
        with open(local_temp_path, "wb") as f:
            f.write(response)

        try:
            text = extract_text(local_temp_path)
            print(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
        except TextExtractionError as e:
            print(f"❌ Failed to extract text from {file_path}: {str(e)}")
            return {"error": str(e)}
        except Exception as e:
            print(f"❌ An unexpected error occurred during text extraction: {str(e)}")
            return {"error": "An unexpected error occurred during file processing."}

        # ... (the rest of the chunking logic is the same)
        filename_meta = extract_metadata_from_filename(Path(file_name).name)
        print(f"[DEBUG] Filename metadata for {file_name}: {filename_meta}")
        chunk_tuples = fixed_size_chunk(text, max_tokens=1200, overlap_tokens=120)
        db_chunks = []
        for i, (chunk_text, meta) in enumerate(chunk_tuples):
            chunk_id = str(uuid4())
            chunk_metadata = {**filename_meta, **meta}
            print(f"[DEBUG] Chunk {i} metadata: {chunk_metadata}")
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "file_name": file_name,
                "content": chunk_text,
                "chunk_index": i,
                **chunk_metadata,
            }
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} fixed-size chunks from {file_path}")

        return {"chunks": db_chunks}

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        return {"error": "An unexpected error occurred during chunking."}
