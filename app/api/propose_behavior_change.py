from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import os
import httpx
import uuid
from datetime import datetime

router = APIRouter()

class BehaviorProposal(BaseModel):
    behavior_id: str
    trait_type: str
    current_value: str = None
    proposed_value: str
    change_reason: str
    run_self_test: bool = False

@router.post("/propose_change")
async def propose_behavior_change(payload: BehaviorProposal):
    url = "https://xyyjetaarlmzvqkzeegl.supabase.co/rest/v1/proposed_behavior_changes"

    data = {
        "id": str(uuid.uuid4()),
        "behavior_id": payload.behavior_id,
        "trait_type": payload.trait_type,
        "current_value": payload.current_value,
        "proposed_value": payload.proposed_value,
        "change_reason": payload.change_reason,
        "run_self_test": payload.run_self_test,
        "status": "pending",
        "created_at": datetime.utcnow().isoformat()
    }

    headers = {
        "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer " + os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(url, headers=headers, json=data)
        res.raise_for_status()
        return {"message": "Proposal logged", "proposal_id": data["id"]}
