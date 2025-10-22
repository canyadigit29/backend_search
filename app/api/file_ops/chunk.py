import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken
import calendar

from app.core.extract_text import extract_text  # Assumed
from app.core.supabase_client import supabase

def extract_metadata_from_filename(file_name):
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

def fixed_size_chunk(text, max_tokens=1500, overlap_tokens=300):
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
        # Optionally, estimate page number as None or use 1 for all
        chunk_meta.append({"section_header": None, "page_number": None})
        chunks.append(chunk_text)
        start += max_tokens - overlap_tokens
    return list(zip(chunks, chunk_meta))

def chunk_file(file_id: str, user_id: str, file_text: str, metadata: dict):
    print(f"🔍 Starting chunking for file_id: {file_id}")
    try:
        # Use fixed-size overlapping chunking
        chunk_tuples = fixed_size_chunk(file_text, max_tokens=1500, overlap_tokens=300)
        db_chunks = []
        
        # The incoming metadata from the GPT is the source of truth.
        # We no longer need to infer it from the filename.
        base_metadata = metadata or {}

        for i, (chunk_text, chunk_specific_meta) in enumerate(chunk_tuples):
            chunk_id = str(uuid4())
            
            # Combine the overall file metadata with any metadata specific to this chunk (like page number)
            final_chunk_metadata = {**base_metadata, **chunk_specific_meta}

            chunk = {
                "id": chunk_id,
                "file_id": file_id,
                "user_id": user_id,
                "content": chunk_text,
                "chunk_index": i,
                "metadata": final_chunk_metadata,  # The combined metadata for this chunk
            }
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} fixed-size chunks for file {file_id}")
        return db_chunks

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        return []

