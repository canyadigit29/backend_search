from fastapi import APIRouter, HTTPException
from supabase import create_client
from app.core.config import settings
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks
import datetime

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

@router.post("/ingest_unprocessed")
async def ingest_unprocessed():
    try:
        # List all files in the upload folder
        response = supabase.storage.from_("maxgptstorage").list("uploads")
        storage_list = response.get("data", [])

        for item in storage_list:
            file_path = f"uploads/{item['name']}"
            exists = supabase.table("ingested").select("*").eq("file_path", file_path).execute()

            if exists.data:
                continue  # already ingested

            file_id = item['name'].split(".")[0]

            try:
                chunk_file(file_id)
                embed_chunks(file_id)
                supabase.table("ingested").insert({
                    "file_path": file_path,
                    "file_id": file_id,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to ingest {file_id}: {str(e)}")

        return {"message": "Ingestion completed."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unhandled error: {str(e)}")