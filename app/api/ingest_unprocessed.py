from fastapi import APIRouter, HTTPException
from supabase import create_client
from app.core.config import settings
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks
from datetime import datetime

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)

@router.post("/ingest_unprocessed")
async def ingest_unprocessed():
    storage_list = supabase.storage.from_("maxgptstorage").list("uploads/")
    if not storage_list:
        raise HTTPException(status_code=404, detail="No files found in storage.")

    for item in storage_list:
        file_path = f"uploads/{item['name']}"
        exists = supabase.table("ingested").select("*").eq("file_path", file_path).execute()
        if exists.data:
            continue  # Skip already-ingested files

        file_id = item["name"].split(".")[0]

        try:
            chunk_file(file_id)
            embed_chunks(file_id)
            supabase.table("ingested").insert({
                "file_id": file_id,
                "file_path": file_path,
                "ingested_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")

    return {"status": "success", "message": "Ingestion complete"}
