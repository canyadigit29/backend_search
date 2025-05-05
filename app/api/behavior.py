from fastapi import APIRouter, HTTPException, Request
import os
import httpx

router = APIRouter()

SUPABASE_URL = "https://xyyjetaarlmzvqkzeegl.supabase.co"
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE"]
FALLBACK_USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"

@router.get("/api/behavior")
async def get_behavior(request: Request):
    try:
        user_id = request.query_params.get("user_id", FALLBACK_USER_ID)
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}"
        }

        async with httpx.AsyncClient() as client:
            # 1️⃣ Try to fetch user-specific behavior
            behavior_url = f"{SUPABASE_URL}/rest/v1/assistant_behavior"
            behavior_params = {
                "user_id": f"eq.{user_id}",
                "active": "eq.true",
                "select": "id, tone, system_message",
                "limit": 1
            }
            res = await client.get(behavior_url, headers=headers, params=behavior_params)
            res.raise_for_status()
            rows = res.json()

            # 2️⃣ Fallback to known seeded UUID if nothing found
            if not rows and user_id != FALLBACK_USER_ID:
                fallback_params = {
                    "user_id": f"eq.{FALLBACK_USER_ID}",
                    "active": "eq.true",
                    "select": "id, tone, system_message",
                    "limit": 1
                }
                res = await client.get(behavior_url, headers=headers, params=fallback_params)
                res.raise_for_status()
                rows = res.json()

            if not rows:
                raise HTTPException(status_code=404, detail="No behavior profile found.")

            behavior = rows[0]
            behavior_id = behavior["id"]

            # 3️⃣ Fetch associated traits
            traits_url = f"{SUPABASE_URL}/rest/v1/behavior_traits"
            traits_params = {
                "behavior_id": f"eq.{behavior_id}",
                "select": "trait_type,value"
            }
            traits_res = await client.get(traits_url, headers=headers, params=traits_params)
            traits_res.raise_for_status()
            traits_data = traits_res.json()

            return {
                "behavior_id": behavior_id,
                "tone": behavior.get("tone"),
                "system_message": behavior.get("system_message"),
                "traits": traits_data
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Behavior lookup failed: {str(e)}")
