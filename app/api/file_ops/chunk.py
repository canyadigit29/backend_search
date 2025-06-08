import re
import os
from pathlib import Path
from uuid import uuid4
import tiktoken

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

def smart_chunk(text, max_tokens=2000, overlap_tokens=200):
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
        # Optionally, you can add section/page metadata here if needed
        chunks.append(chunk_text)
        chunk_meta.append({"section": None, "page": None})
        start += max_tokens - overlap_tokens  # overlap for context
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
            return

        file_path = file_entry["file_path"]
        file_name = file_entry.get("file_name") or file_entry.get("name") or file_path
        actual_user_id = user_id or file_entry.get("user_id", None)
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "files")
        print(f"📄 Filepath: {file_path}")

        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            print(f"❌ Could not download file from Supabase: {file_path}")
            return

        local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
        with open(local_temp_path, "wb") as f:
            f.write(response)

        try:
            text = extract_text(local_temp_path)
            print(f"📜 Extracted text length: {len(text.strip())} characters from {file_path}")
        except Exception as e:
            print(f"❌ Failed to extract text from {file_path}: {str(e)}")
            return

        # Use improved smart_chunk logic with section/page metadata
        chunk_tuples = smart_chunk(text, max_tokens=2000, overlap_tokens=200)
        db_chunks = []
        for i, (chunk_text, meta) in enumerate(chunk_tuples):
            chunk_id = str(uuid4())
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "file_name": file_name,
                "content": chunk_text,
                "chunk_index": i,
                "section_header": meta["section"],
                "page_number": meta["page"],
            }
            if actual_user_id:
                chunk["user_id"] = actual_user_id
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} semantic-aware chunks from {file_path}")

        if db_chunks:
            supabase.table("document_chunks").insert(db_chunks).execute()
            print(f"✅ Inserted {len(db_chunks)} chunks.")
            return [chunk["content"] for chunk in db_chunks]

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
