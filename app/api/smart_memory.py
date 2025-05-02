
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.api import router_brain

router = APIRouter()

class SmartMemoryRequest(BaseModel):
    query: str
    session_id: str
    topic_name: str = None

@router.post("/smart_memory")
async def smart_memory(req: SmartMemoryRequest):
    try:
        result = router_brain.route_query(
            user_query=req.query,
            session_id=req.session_id,
            topic_name=req.topic_name
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
