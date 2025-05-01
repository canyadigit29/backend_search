
import fitz  # PyMuPDF
from supabase import create_client
from app.core.config import settings
from uuid import uuid4
import re

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

def chunk_file(file_id: str):
    print(f"🔍 Starting chunking for file_id: {file_id}")
    try:
        file_entry = None
        is_uuid = re.fullmatch(r"[0-9a-fA-F\-]{36}", file_id)

        if is_uuid:
            result = supabase.table("files").select("*").eq("id", file_id).execute()
            file_entry = result.data[0] if result.data else None
        else:
            filename_guess = f"uploads/{file_id}.pdf"
            result = supabase.table("files").select("*").eq("file_path", filename_guess).execute()
            file_entry = result.data[0] if result.data else None

        if not file_entry:
            print(f"❌ No file found for identifier: {file_id}")
            return

        file_path = file_entry["file_path"]
        bucket = "maxgptstorage"
        print(f"📄 Filepath: {file_path}")

        response = supabase.storage.from_(bucket).download(file_path)
        if not response:
            print(f"❌ Could not download file from Supabase: {file_path}")
            return

        with open("/tmp/tempfile.pdf", "wb") as f:
            f.write(response)

        text = ""
        doc = fitz.open("/tmp/tempfile.pdf")
        for page in doc:
            text += page.get_text()
        doc.close()

        max_chunk_size = 1000
        overlap = 200
        chunks = []
        for i in range(0, len(text), max_chunk_size - overlap):
            chunk_text = text[i:i + max_chunk_size]
            chunk_id = str(uuid4())
            chunks.append({
                "id": chunk_id,
                "file_id": file_entry["id"],
                "content": chunk_text,
                "chunk_index": len(chunks)
            })

        if chunks:
            supabase.table("chunks").insert(chunks).execute()
            print(f"✅ Inserted {len(chunks)} chunks.")

    except Exception as e:
        print(f"❌ Error during chunking: {str(e)}")
