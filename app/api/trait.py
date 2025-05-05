from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.supabase_client import supabase  # ✅ Uses your Supabase wrapper
import uuid
import logging

router = APIRouter()
logger = logging.getLogger("maxgpt")
logger.setLevel(logging.DEBUG)

class TraitInsert(BaseModel):
    behavior_id: str
    trait_type: str
    value: str

@router.post("/api/trait")
async def insert_trait(payload: TraitInsert):
    try:
        # 🔒 Validate behavior_id is UUID
        try:
            uuid.UUID(payload.behavior_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid behavior_id format (must be UUID)")

        trait_type = payload.trait_type.strip().lower().replace(" ", "_").replace("-", "_")
        value = payload.value.strip()

        if not trait_type or not value:
            raise HTTPException(status_code=400, detail="Trait type and value cannot be blank.")

        logger.debug(f"📥 Trait insert payload: behavior_id={payload.behavior_id}, trait_type={trait_type}, value={value}")

        result = supabase.table("behavior_traits").upsert({
            "behavior_id": payload.behavior_id,
            "trait_type": trait_type,
            "value": value
        }).execute()

        if result.error:
            logger.error(f"❌ Supabase trait insert error: {result.error.message}")
            raise HTTPException(status_code=500, detail=result.error.message)

        logger.info("✅ Trait inserted or updated successfully.")
        return {"message": "Trait inserted or updated", "data": result.data}

    except Exception as e:
        logger.exception("🚨 Exception during trait insert")
        raise HTTPException(status_code=500, detail=str(e))
