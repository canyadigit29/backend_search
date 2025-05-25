import os

import re
import nltk
from pathlib import Path
from uuid import uuid4

from app.core.extract_text import extract_text  # Assumed
from app.core.supabase_client import supabase

nltk.download('punkt', quiet=True)
from nltk.tokenize import sent_tokenize

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
        actual_user_id = user_id or file_entry.get("user_id", None)
        project_id = file_entry.get("project_id")
        bucket = os.getenv("SUPABASE_STORAGE_BUCKET")
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

        max_chunk_size = 1600
        overlap = 200
        sentences = sent_tokenize(text)
        chunks = []
        current_chunk = []

        def chunk_text_block(sentences):
            return " ".join(sentences)

        token_length = 0
        for sentence in sentences:
            sentence_length = len(sentence)
            if token_length + sentence_length > max_chunk_size:
                if current_chunk:
                    chunks.append(chunk_text_block(current_chunk))
                    token_length = 0
                    current_chunk = []
            current_chunk.append(sentence)
            token_length += sentence_length

        if current_chunk:
            chunks.append(chunk_text_block(current_chunk))

        # Add overlap
        final_chunks = []
        for i, chunk in enumerate(chunks):
            start_idx = max(0, i - 1)
            combined = " ".join(chunks[start_idx:i + 1])
            final_chunks.append(combined)

        db_chunks = []
        for i, chunk_text in enumerate(final_chunks):
            chunk_id = str(uuid4())
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "content": chunk_text,
                "chunk_index": i,
            }
            if actual_user_id:
                chunk["user_id"] = actual_user_id
            if project_id:
                chunk["project_id"] = project_id
            db_chunks.append(chunk)

        print(f"🧹 Got {len(db_chunks)} semantic-aware chunks from {file_path}")

        if db_chunks:
            supabase.table("document_chunks").insert(db_chunks).execute()
            print(f"✅ Inserted {len(db_chunks)} chunks.")
            return [chunk["content"] for chunk in db_chunks]

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
