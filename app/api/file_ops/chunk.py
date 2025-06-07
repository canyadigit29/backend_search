import re
import nltk
from pathlib import Path
from uuid import uuid4
import tiktoken
import hashlib
import traceback

from app.core.extract_text import extract_text  # Assumed
from app.core.supabase_client import supabase

nltk.download('punkt', quiet=True)
from nltk.tokenize import sent_tokenize

def chunk_file(file_id: str, user_id: str = None, enrich_metadata: bool = False):
    print(f"[DEBUG] chunk_file called with file_id={file_id}, user_id={user_id}, enrich_metadata={enrich_metadata}")
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
        actual_user_id = user_id or file_entry.get("user_id", None)
        project_id = file_entry.get("project_id")
        bucket = "maxgptstorage"
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
            traceback.print_exc()
            return

        # --- Token-based chunking using tiktoken ---
        model_name = "text-embedding-3-large"
        encoding = tiktoken.encoding_for_model(model_name)
        max_tokens = 8191  # OpenAI's max for this model
        chunk_size = 1500  # Conservative chunk size for context
        overlap = 200      # Overlap in tokens
        tokens = encoding.encode(text)
        total_tokens = len(tokens)
        print(f"🔢 Total tokens in document: {total_tokens}")
        chunks = []
        start = 0
        while start < total_tokens:
            end = min(start + chunk_size, total_tokens)
            chunk_tokens = tokens[start:end]
            chunk_text = encoding.decode(chunk_tokens)
            chunks.append(chunk_text)
            start += chunk_size - overlap  # Slide window with overlap
        print(f"🧩 Created {len(chunks)} token-based chunks.")
        # --- End token-based chunking ---

        # Optional: extract section headers/page numbers if enrich_metadata is True
        section_headers = []
        page_numbers = []
        if enrich_metadata:
            section_headers = [None] * len(chunks)
            page_numbers = [None] * len(chunks)
        # --- End token-based chunking ---

        print(f"[DEBUG] Total chunks before dedup: {len(chunks)}")
        chunk_records = []
        for i, chunk_text in enumerate(chunks):
            chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            try:
                existing = supabase.table("document_chunks").select("id").eq("file_id", file_entry["id"]).eq("content", chunk_text).execute()
            except Exception as e:
                print(f"[ERROR] Supabase deduplication query failed for chunk {i}: {e}")
                traceback.print_exc()
                continue
            if existing.data:
                print(f"⚠️ Duplicate chunk detected, skipping index {i}")
                continue
            chunk = {
                "content": chunk_text,
                "chunk_index": i,
                "chunk_hash": chunk_hash,
                "section_header": section_headers[i] if enrich_metadata else None,
                "page_number": page_numbers[i] if enrich_metadata else None,
            }
            if user_id:
                chunk["user_id"] = user_id
            if project_id:
                chunk["project_id"] = project_id
            chunk_records.append(chunk)
        print(f"[DEBUG] Final chunk_records count: {len(chunk_records)}")
        if chunk_records:
            db_chunks = []
            for i, chunk in enumerate(chunk_records):
                db_chunk = dict(chunk)
                db_chunk["id"] = str(uuid4())
                db_chunk["file_id"] = file_entry["id"]
                db_chunk["chunk_index"] = i
                db_chunks.append(db_chunk)
            try:
                supabase.table("document_chunks").insert(db_chunks).execute()
                print(f"✅ Inserted {len(db_chunks)} chunks.")
            except Exception as e:
                print(f"[ERROR] Failed to insert chunks into Supabase: {e}")
                traceback.print_exc()
        return chunk_records
    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
        traceback.print_exc()
        return
