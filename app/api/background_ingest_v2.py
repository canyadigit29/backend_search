from fastapi import APIRouter, Body
from typing import Dict

router = APIRouter()

@router.post("/background_ingest_v2")
async def background_ingest_v2(
    payload: Dict = Body(...)
):
    print("🧠 BACKGROUND_INGEST_V2 HIT")
    return {
        "message": "✅ V2 endpoint was hit successfully.",
        "payload_received": payload
    }
