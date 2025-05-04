from fastapi import APIRouter, HTTPException
from supabase import create_client
from app.core.config import settings
from app.api.chunk import chunk_file
from app.api.embed import embed_chunks
from datetime import datetime
import time

router = APIRouter()
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE)


@router.post("/ingest_unprocessed")
async def ingest_unprocessed():
    storage_list = supabase.storage.from_("maxgptstorage").list("uploads/")
    if not storage_list:
        raise HTTPException(status_code=404, detail="No files found in storage.")

    for item in storage_list:
        file_path = f"uploads/{item['name']}"

        # Check if already ingested
        file_check = supabase.table("files").select("*").eq("file_path", file_path).execute()
        file_record = file_check.data[0] if file_check.data else None
        if file_record and file_record.get("ingested") is True:
            continue

        try:
            # Ensure file is registered and capture the UUID
            if not file_record:
                insert
