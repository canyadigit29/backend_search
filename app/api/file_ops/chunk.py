import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken
import calendar

from app.core.extract_text import extract_text, TextExtractionError
from app.core.supabase_client import supabase

def extract_metadata_from_filename(file_name):
    meta = {}
    name = file_name.rsplit('.', 1)[0].strip()
    ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
    meta['file_extension'] = ext

    # More flexible meeting document parsing
    doc_type_match = re.search(r'(Agenda|Minutes)', name, re.IGNORECASE)
    if doc_type_match:
        meta['document_type'] = doc_type_match.group(1).capitalize()

        # Find year
        year_match = re.search(r'(\d{4})', name)
        if year_match:
            meta['meeting_year'] = int(year_match.group(1))

        # Find month
        month_names = list(calendar.month_name)[1:]  # January to December
        month_abbrs = list(calendar.month_abbr)[1:]
        
        # Create a regex pattern for all month names and abbreviations
        month_pattern = '|'.join(month_names + month_abbrs)
        month_match = re.search(f'({month_pattern})', name, re.IGNORECASE)
        
        if month_match:
            month_str = month_match.group(1).capitalize()
            try:
                # Get month number from full name
                meta['meeting_month'] = list(calendar.month_name).index(month_str)
            except ValueError:
                try:
                    # Get month number from abbreviation
                    meta['meeting_month'] = list(calendar.month_abbr).index(month_str)
                except ValueError:
                    meta['meeting_month'] = None
            meta['meeting_month_name'] = month_str
        
        return meta

    # Ordinaces (existing logic)
    m = re.match(r'(\d+)?\s*(.*?) Ordinance$', name)
    if m:
        meta['document_type'] = 'Ordinance'
        if m.group(1):
            meta['ordinance_number'] = m.group(1).strip()
        meta['ordinance_title'] = m.group(2).strip()
        return meta

    # Misc (fallback)
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
            
            # Start with a clean slate for metadata that will go to the DB
            db_metadata = {}

            # Set document_type, which should always be present
            db_metadata['document_type'] = filename_meta.get('document_type')

            # Conditionally create meeting_date
            if 'meeting_year' in filename_meta and 'meeting_month' in filename_meta:
                try:
                    year = int(filename_meta['meeting_year'])
                    month = int(filename_meta['meeting_month'])
                    db_metadata['meeting_date'] = f"{year}-{month:02d}-01"
                except (ValueError, TypeError):
                    db_metadata['meeting_date'] = None
            else:
                db_metadata['meeting_date'] = None

            # Conditionally add ordinance fields
            if 'ordinance_number' in filename_meta:
                db_metadata['ordinance_number'] = filename_meta.get('ordinance_number')
            if 'ordinance_title' in filename_meta:
                db_metadata['ordinance_title'] = filename_meta.get('ordinance_title')

            # Merge with chunk-specific metadata like page_number
            db_metadata.update(meta)
            
            print(f"[DEBUG] Chunk {i} metadata for DB: {db_metadata}")

            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "content": chunk_text,
                "chunk_index": i,
                **db_metadata,
            }
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} fixed-size chunks from {file_path}")

        return {"chunks": db_chunks}

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        return {"error": "An unexpected error occurred during chunking."}
