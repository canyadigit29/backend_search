import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken
import calendar

from app.core.supabase_client import supabase
from app.api.file_ops.embed import remove_embeddings_for_file

def extract_metadata_from_filename(file_name):
    meta = {}
    name = file_name.rsplit('.', 1)[0].strip()
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    meta['file_extension'] = ext

    doc_type_match = re.search(r'(Agenda|Minutes)', name, re.IGNORECASE)
    if doc_type_match:
        meta['document_type'] = doc_type_match.group(1).capitalize()
        year_match = re.search(r'(\d{4})', name)
        if year_match:
            meta['meeting_year'] = int(year_match.group(1))
        month_names = list(calendar.month_name)[1:]
        month_abbrs = list(calendar.month_abbr)[1:]
        month_pattern = '|'.join(month_names + month_abbrs)
        month_match = re.search(f'({month_pattern})', name, re.IGNORECASE)
        if month_match:
            month_str = month_match.group(1).capitalize()
            try:
                meta['meeting_month'] = list(calendar.month_name).index(month_str)
            except ValueError:
                try:
                    meta['meeting_month'] = list(calendar.month_abbr).index(month_str)
                except ValueError:
                    meta['meeting_month'] = None
            meta['meeting_month_name'] = month_str
        return meta

    m = re.match(r'(\d+)?\s*(.*?) Ordinance$', name)
    if m:
        meta['document_type'] = 'Ordinance'
        if m.group(1):
            meta['ordinance_number'] = m.group(1).strip()
        meta['ordinance_title'] = m.group(2).strip()
        return meta

    meta['document_type'] = 'Misc'
    meta['misc_title'] = name
    return meta

def parse_page_markers(paragraphs):
    page_number = 1
    para_to_page = {}
    for idx, para in enumerate(paragraphs):
        if re.match(r"---PAGE (\d+)---", para.strip()):
            m = re.match(r"---PAGE (\d+)---", para.strip())
            page_number = int(m.group(1))
        para_to_page[idx] = page_number
    return para_to_page

def fixed_size_chunk(text, max_tokens=1200, overlap_tokens=120):
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

def chunk_file(file_id: str, file_name: str, text: str):
    """
    Chunks the provided text and associates it with the given file_id and file_name.
    This is a pure function that does not perform I/O besides deleting old chunks.
    """
    print(f"🔍 Starting chunking for file_id: {file_id} and file_name: {file_name}")
    try:
        print(f"🗑️ Deleting existing chunks for file_id: {file_id}")
        remove_embeddings_for_file(file_id)
        print(f"✅ Successfully deleted existing chunks for file_id: {file_id}")

        if not text or not text.strip():
            print("🤷 No text available to chunk.")
            return {"chunks": []}

        filename_meta = extract_metadata_from_filename(Path(file_name).name)
        print(f"[DEBUG] Filename metadata for {file_name}: {filename_meta}")
        chunk_tuples = fixed_size_chunk(text, max_tokens=1200, overlap_tokens=120)
        db_chunks = []
        for i, (chunk_text, meta) in enumerate(chunk_tuples):
            chunk_id = str(uuid4())
            db_metadata = {}
            db_metadata['document_type'] = filename_meta.get('document_type')
            if 'meeting_year' in filename_meta and 'meeting_month' in filename_meta:
                try:
                    year = int(filename_meta['meeting_year'])
                    month = int(filename_meta['meeting_month'])
                    db_metadata['meeting_date'] = f"{year}-{month:02d}-01"
                except (ValueError, TypeError):
                    db_metadata['meeting_date'] = None
            else:
                db_metadata['meeting_date'] = None
            if 'ordinance_number' in filename_meta:
                db_metadata['ordinance_number'] = filename_meta.get('ordinance_number')
            if 'ordinance_title' in filename_meta:
                db_metadata['ordinance_title'] = filename_meta.get('ordinance_title')
            db_metadata.update(meta)
            
            chunk = {
                "id": chunk_id,
                "file_id": file_id,
                "content": chunk_text,
                "chunk_index": i,
                **db_metadata,
            }
            chunk.pop('file_name', None)
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} fixed-size chunks from {file_name}")
        return {"chunks": db_chunks}
    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        return {"error": "An unexpected error occurred during chunking."}