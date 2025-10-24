import os
import sys

print("📁 Current Working Directory:", os.getcwd())
print("📂 Contents:", os.listdir(os.getcwd()))
print("📦 sys.path =", sys.path)

import os
import uuid
from datetime import datetime, timedelta

from app.core.openai_client import embed_text
from app.core.supabase_client import create_client

# ⏱ Set up Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]  # ✅ updated key name
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# 🧠 Pull un-ingested files
unindexed_files = (
    supabase.table("files")
    .select("*")
    .eq("ingested", False)
    .execute()
)

if not unindexed_files.data:
    print("📭 No new files found for ingestion.")
    exit(0)

files_to_process = unindexed_files.data

# 📦 Process each file
for file_data in files_to_process:
    file_id = file_data['id']
    file_path = file_data['file_path']
    print(f"Processing file: {file_path}")

    try:
        # Download file from storage
        file_content = supabase.storage.from_("files").download(file_path)
        
        # This is a placeholder for your chunking and embedding logic
        # You would replace this with your actual text extraction, chunking, and embedding calls
        print(f"  - Pretending to chunk and embed file ID: {file_id}")
        
        # Simulate creating chunks and embeddings
        chunks = ["This is chunk 1.", "This is chunk 2."]
        for i, chunk_text in enumerate(chunks):
            embedding = embed_text(chunk_text)
            supabase.table("document_chunks").insert({
                "file_id": file_id,
                "chunk_index": i,
                "content": chunk_text,
                "embedding": embedding,
            }).execute()
            print(f"    - Inserted chunk {i} for file {file_id}")

        # Mark the file as ingested
        supabase.table("files").update({"ingested": True}).eq("id", file_id).execute()
        print(f"✅ Successfully ingested file: {file_path}")

    except Exception as e:
        print(f"❌ Failed to process file {file_path}: {e}")

print("🧽 Ingestion cycle complete.")

