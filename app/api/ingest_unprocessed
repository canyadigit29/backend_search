from fastapi import APIRouter, HTTPException from supabase import create_client from app.core.config import settings from app.api.chunk import chunk_file from app.api.embed import embed_chunks import datetime

router = APIRouter()

supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

@router.post("/ingest_unprocessed") async def ingest_unprocessed(): try: # List all files in uploads folder storage_list = supabase.storage.from_('maxgptstorage').list('uploads', {'limit': 1000, 'offset': 0, 'sortBy': {'column': 'name', 'order': 'asc'}}) if not storage_list: return {"message": "No files found in uploads folder."}

for item in storage_list:
        file_path = f"uploads/{item['name']}"
        # Check if file has already been ingested
        exists = supabase.table("ingested").select("file_path").eq("file_path", file_path).execute()
        if exists.data:
            continue  # already ingested

        # Derive a UUID from the file name (or generate new one)
        file_id = item['name'].split(".")[0]

        # Trigger chunking and embedding
        try:
            chunk_file(file_id)
            embed_chunks(file_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Chunk/embed failed for {file_id}: {str(e)}")

        # Mark file as ingested
        supabase.table("ingested").insert({
            "file_path": file_path,
            "chunked_at": datetime.datetime.utcnow().isoformat()
        }).execute()

    return {"message": "Ingestion pass complete."}

except Exception as e:
    raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
