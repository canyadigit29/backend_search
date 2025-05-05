from fastapi import APIRouter, HTTPException, Request
import os
import httpx

router = APIRouter()

@router.get("/api/behavior")
async def get_behavior(request: Request):
    try:
        # 👤 Try to get user_id from query string (optional)
        user_id = request.query_params.get("user_id", "default")
        print(f"🔍 Looking up behavior for user_id: {user_id}")

        async with httpx.AsyncClient() as client:
            supabase_url = "https://xyyjetaarlmzvqkzeegl.supabase.co/rest/v1/assistant_behavior"
            headers = {
                "apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}",
            }

            # 1️⃣ Try user-specific behavior
            params_user = {
                "user_id": f"eq.{user_id}",
                "active": "eq.true",
                "select": "system_message",
                "limit": 1
            }
            res = await client.get(supabase_url, headers=headers, params=params_user)
            res.raise_for_status()
            user_behavior = res.json()

            if user_behavior:
                return {"system_message": user_behavior[0]["system_message"]}

            # 2️⃣ Fallback to global default
            params_default = {
                "mode": "eq.default",
                "active": "eq.true",
                "select": "system_message",
                "limit": 1
            }
            res_default = await client.get(supabase_url, headers=headers, params=params_default)
            res_default.raise_for_status()
            fallback_behavior = res_default.json()

            if fallback_behavior:
                return {"system_message": fallback_behavior[0]["system_message"]}

        raise HTTPException(status_code=404, detail="No active behavior profile found.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Behavior lookup failed: {str(e)}")
