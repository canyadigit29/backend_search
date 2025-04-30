import fitz  # PyMuPDF
from supabase import create_client
from app.core.config import settings
from uuid import uuid4

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

def chunk_file(file_id: str):
    print(f"🔍 Starting chunking for file_id: {file_id}")
    file_entry = supabase.table("files").select("*").eq("id", file_id).single().execute().data
    if not file_entry:
        print(f"❌ File ID {file_id} not found in files table.")
        return

    filepath = file_entry["filepath"]
    bucket = settings.SUPABASE_BUCKET
    print(f"📄 Filepath: {filepath}")

    response = supabase.storage.from_(bucket).download(filepath)
    if not response:
        print(f"❌ Could not download file from Supabase: {filepath}")
        return

    text = ""
    with open("/tmp/tempfile.pdf", "wb") as f:
        f.write(response)

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
            "file_id": file_id,
            "content": chunk_text,
            "chunk_index": len(chunks)
        })

    if chunks:
        supabase.table("chunks").insert(chunks).execute()
        print(f"✅ Inserted {len(chunks)} chunks.")
    else:
        print("⚠️ No chunks generated.")
