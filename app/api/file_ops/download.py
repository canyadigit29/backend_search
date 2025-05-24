from fastapi import APIRouter, HTTPException, Query
from app.core.supabase_client import supabase

router = APIRouter()

@router.get("/download")
async def get_signed_url(file_path: str = Query(...)):
    try:
        result = supabase.storage.from_("maxgptstorage").create_signed_url(
            file_path, 60  # URL valid for 60 seconds
        )
        if not result.data or "signedUrl" not in result.data:
            raise HTTPException(status_code=404, detail="File not found or unable to sign URL")
        return {"signedUrl": result.data["signedUrl"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate signed URL: {str(e)}")
