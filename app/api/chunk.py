import fitz  # PyMuPDF
import re
from uuid import uuid4
from pathlib import Path
from supabase import create_client
from app.core.config import settings

from docx import Document
from striprtf.striprtf import rtf_to_text

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)


def extract_text(file_path: str, local_path: str) -> str:
    if file_path.endswith(".pdf"):
        doc = fitz.open(local_path)
        return "".join([page.get_text() for page in doc])

    elif file_path.endswith(".docx"):
        doc = Document(local_path)
        return "\n".join([para.text for para in doc.paragraphs])

    elif file_path.endswith(".txt") or file_path.endswith(".md"):
        with open(local_path, "r", encoding="utf-8") as f:
            return f.read()

    elif file_path.endswith(".rtf"):
        with open(local_path, "r", encoding="utf-8") as f:
            return rtf_to_text(f.read())

    else:
        raise ValueError(f"Unsupported file format: {file_path}")


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
        bucket = "maxgptstorage"
        print(f"📄 Filepath: {file_path}")

        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            print(f"❌ Could not download file from Supabase: {file_path}")
            return

        local_temp_path = "/tmp/tempfile" + Path(file_path).suffix
        with open(local_temp_path, "wb") as f:
            f.write(response)

        # Extract text
        text = extract_text(file_path, local_temp_path)

        # Chunking parameters
        max_chunk_size = 1000
        overlap = 200
        chunks = []

        if len(text) <= max_chunk_size:
            # 👇 Entire file becomes one chunk
            chunk_id = str(uuid4())
            chunk = {
                "id": chunk_id,
                "file_id": file_entry["id"],
                "content": text,
                "chunk_index": 0
            }
            if actual_user_id:
                chunk["user_id"] = actual_user_id
            chunks.append(chunk)
        else:
            # 👇 Normal multi-chunk logic
            for i in range(0, len(text), max_chunk_size - overlap):
                chunk_text = text[i:i + max_chunk_size]
                chunk_id = str(uuid4())
                chunk = {
                    "id": chunk_id,
                    "file_id": file_entry["id"],
                    "content": chunk_text,
                    "chunk_index": len(chunks)
                }
                if actual_user_id:
                    chunk["user_id"] = actual_user_id
                chunks.append(chunk)

        if chunks:
            supabase.table("chunks").insert(chunks).execute()
            print(f"✅ Inserted {len(chunks)} chunks.")

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
