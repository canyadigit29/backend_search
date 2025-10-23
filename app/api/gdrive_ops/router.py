from fastapi import APIRouter, BackgroundTasks, HTTPException
from .sync import run_google_drive_sync

router = APIRouter()

@router.post("/sync", status_code=202)
async def trigger_google_drive_sync(background_tasks: BackgroundTasks):
    """
    Triggers the background task to sync files from Google Drive to Supabase.
    """
    print("Received request to trigger Google Drive sync.")
    background_tasks.add_task(run_google_drive_sync)
    return {"message": "Google Drive sync process has been started in the background."}
