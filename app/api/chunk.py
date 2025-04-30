
from fastapi import HTTPException
from app.core.supabase_client import supabase
import uuid

def chunk_file(file_id: str, chunk_size: int = 500):
    try:
        print(f"[chunk_file] Starting for file_id: {file_id}")

        # Step 1: Fetch file content
        file_response = supabase.table("files").select("id, content").eq("id", file_id).single().execute()
        if file_response.data is None:
            raise HTTPException(status_code=404, detail=f"No file found with id: {file_id}")

        content = file_response.data["content"]
        print(f"[chunk_file] Retrieved file content length: {len(content)} characters")

        if not content.strip():
            raise HTTPException(status_code=400, detail="File content is empty or whitespace.")

        # Step 2: Split into chunks
        chunks = [
            content[i:i + chunk_size]
            for i in range(0, len(content), chunk_size)
        ]

        print(f"[chunk_file] Total chunks created: {len(chunks)}")

        # Step 3: Insert chunks
        chunk_entries = [
            {
                "id": str(uuid.uuid4()),
                "file_id": file_id,
                "content": chunk.strip()
            }
            for chunk in chunks if chunk.strip()
        ]

        insert_response = supabase.table("chunks").insert(chunk_entries).execute()
        print(f"[chunk_file] Inserted {len(chunk_entries)} chunks into Supabase.")

    except Exception as e:
        print(f"[chunk_file] ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Chunking failed: {str(e)}")
