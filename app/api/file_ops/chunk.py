import re
from pathlib import Path
from uuid import uuid4

from app.core.extract_text import extract_text  # Assumed
from app.core.supabase_client import supabase

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
            return

        max_chunk_size = 1000
        overlap = 150
        chunks = []

        if len(text) <= max_chunk_size:
            chunk_id = str(uuid4())
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "content": text,
                "chunk_index": 0,
            }
            if actual_user_id:
                chunk["user_id"] = actual_user_id
            if project_id:
                chunk["project_id"] = project_id
            chunks.append(chunk)
        else:
            for i in range(0, len(text), max_chunk_size - overlap):
                chunk_text = text[i : i + max_chunk_size]
                chunk_id = str(uuid4())
                chunk = {
                    "id": chunk_id,
                    "file_id": file_entry["id"],
                    "content": chunk_text,
                    "chunk_index": len(chunks),
                }
                if actual_user_id:
                    chunk["user_id"] = actual_user_id
                if project_id:
                    chunk["project_id"] = project_id
                chunks.append(chunk)

        print(f"🧹 Got {len(chunks)} chunks from {file_path}")

        if chunks:
            supabase.table("document_chunks").insert(chunks).execute()
            print(f"✅ Inserted {len(chunks)} chunks.")
            return [chunk["content"] for chunk in chunks]  # ✅ PATCHED

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
