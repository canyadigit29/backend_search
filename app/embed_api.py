from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.api.file_ops.embed import embed_text

router = APIRouter()

class EmbedRequest(BaseModel):
    input: str

@router.post("/embed")
async def generate_embedding(req: EmbedRequest):
    try:
        embedding = embed_text(req.input)
        return { "embedding": embedding }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
