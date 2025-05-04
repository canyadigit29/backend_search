from fastapi import APIRouter, HTTPException
import os
import httpx

router = APIRouter()

@router.get("/behavior")
async def get_active_behavior():
    url = "https://xyyjetaarlmzvqkzeegl.supabase.co/rest/v1/assistant_behavior"
    headers = {
        "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}",
    }
    params = {
        "mode": "eq.default",
        "active": "eq.true",
        "select": "system_message"
    }

    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers, params=params)
        res.raise_for_status()
        data = res.json()

        if not data:
            raise HTTPException(status_code=404, detail="No active behavior found.")

        return {"system_message": data[0]["system_message"]}
