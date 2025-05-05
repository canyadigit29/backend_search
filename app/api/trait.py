from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase  # ✅ Uses your Supabase wrapper

router = APIRouter()

class TraitInsert(BaseModel):
    behavior_id: str
    trait_type: str
    value: str

@router.post("/trait")
async def insert_trait(payload: TraitInsert):
    try:
        result = supabase.table("behavior_traits").upsert({
            "behavior_id": payload.behavior_id,
            "trait_type": payload.trait_type,
            "value": payload.value
        }).execute()

        if result.error:
            raise HTTPException(status_code=500, detail=result.error.message)

        return {"message": "Trait inserted or updated", "data": result.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
