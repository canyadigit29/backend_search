import os
import uuid
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from redis import from_url as redis_from_url
from rq import Queue

from app.tasks.search_worker import process_search_job

# Connect to Redis
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_conn = redis_from_url(REDIS_URL)
q = Queue(connection=redis_conn)

router = APIRouter()

@router.post("/assistant/search/start")
async def start_search(request: Request):
    """
    Starts an asynchronous search job.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

    user_prompt = data.get("query") or data.get("user_prompt")
    if not user_prompt:
        return JSONResponse({"error": "Missing query in payload"}, status_code=400)

    user_id = (data.get("user", {}).get("id") or
               os.environ.get("ASSISTANT_DEFAULT_USER_ID") or
               "4a867500-7423-4eaa-bc79-94e368555e05")

    job_id = str(uuid.uuid4())
    
    # These are the arguments your worker function expects
    tool_args = {
        "user_prompt": user_prompt,
        "user_id_filter": user_id,
        "search_query": user_prompt, # Assuming simple search for now
        # Add other filters and parameters from `data` as needed
        "file_name_filter": data.get("file_name_filter"),
        "description_filter": data.get("description_filter"),
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "relevance_threshold": data.get("relevance_threshold"),
        "max_results": data.get("max_results")
    }

    q.enqueue(process_search_job, tool_args, job_id=job_id)

    return JSONResponse({"status": "running", "job_id": job_id})

@router.get("/assistant/search/status/{job_id}")
async def get_search_status(job_id: str):
    """
    Checks the status of a search job.
    """
    job = q.fetch_job(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    
    return JSONResponse({"status": job.get_status()})

@router.get("/assistant/search/results/{job_id}")
async def get_search_results(job_id: str):
    """
    Retrieves the results of a completed search job.
    """
    job = q.fetch_job(job_id)
    if job is None:
        return JSONResponse({"status": "not_found"}, status_code=404)
    
    if job.get_status() != 'finished':
        return JSONResponse({"status": job.get_status()})

    # Assuming the worker saves the result in redis
    result_key = f"search:results:{job_id}"
    result = redis_conn.get(result_key)
    
    if result:
        return JSONResponse(content=result)
    else:
        # Fallback to job.result if not in redis
        return JSONResponse({"status": "finished", "result": job.result})
