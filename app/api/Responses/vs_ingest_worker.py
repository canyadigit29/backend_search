import io
import os
import tempfile
import time
import logging
from typing import Optional, List, Dict

from openai import OpenAI
from fastapi import HTTPException

from app.core.config import settings
from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)


def _resolve_vector_store_id() -> str:
    if settings.GDRIVE_VECTOR_STORE_ID:
        return settings.GDRIVE_VECTOR_STORE_ID
    if settings.GDRIVE_WORKSPACE_ID:
        try:
            res = (
                supabase.table("workspace_vector_stores")
                .select("vector_store_id")
                .eq("workspace_id", settings.GDRIVE_WORKSPACE_ID)
                .maybe_single()
                .execute()
            )
            row = getattr(res, "data", None)
            if row and row.get("vector_store_id"):
                return row["vector_store_id"]
        except Exception as e:
            logger.error(f"Failed to lookup vector_store_id from Supabase: {e}")
    raise HTTPException(status_code=404, detail="Vector store id not configured or not found for workspace")


def _attach_file_to_vector_store(client: OpenAI, vector_store_id: str, file_id: str):
    last_err = None
    try:
        client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)
        return
    except Exception as e:
        last_err = e
    try:
        getattr(client, "beta").vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)  # type: ignore
        return
    except Exception as e2:
        last_err = e2
    raise RuntimeError(f"Attach failed: {last_err}")


def _retry_call(fn, *args, retries=4, base_delay=1.0, **kwargs):
    delay = base_delay
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == retries - 1:
                raise
            # Backoff on rate limits and transient errors
            time.sleep(delay)
            delay *= 2


def _get_eligible_files(limit: int) -> List[Dict]:
    """Pick files that should be uploaded to the Vector Store.
    Policy: ingested=False (repurposed), and either (ocr_needed=False) or (ocr_scanned=True).
    """
    try:
        q = (
            supabase.table("files")
            .select("id,name,file_path,type,ocr_needed,ocr_scanned,ocr_text_path,ingested")
            .eq("ingested", False)
            .limit(limit)
        )
        # Supabase Python client doesn't support OR directly; fetch and filter in memory
        res = q.execute()
        rows = getattr(res, "data", []) or []
        eligible = []
        for r in rows:
            if (not r.get("ocr_needed")) or (r.get("ocr_scanned")):
                eligible.append(r)
        return eligible
    except Exception as e:
        logger.error(f"Failed to query eligible files: {e}")
        return []


async def upload_missing_files_to_vector_store():
    """Uploads pending Supabase files into the OpenAI Vector Store with backoff.
    On success, sets files.ingested=True (repurposed to mean VS-uploaded).
    Rate is controlled by VS_UPLOAD_DELAY_MS and VS_UPLOAD_BATCH_LIMIT envs.
    """
    vector_store_id = _resolve_vector_store_id()
    client = OpenAI()
    delay_ms = max(0, int(settings.VS_UPLOAD_DELAY_MS))
    per_call_sleep = delay_ms / 1000.0
    batch_limit = max(1, int(settings.VS_UPLOAD_BATCH_LIMIT))

    files = _get_eligible_files(batch_limit)
    uploaded = 0
    skipped = 0
    errors = 0

    for f in files:
        file_id = f["id"]
        name = f.get("name") or os.path.basename(f.get("file_path", "")) or f"file-{file_id}"
        file_path = f["file_path"]
        content_type = f.get("type", "application/octet-stream")

        # Prefer OCR text if available
        temp_path = None
        try:
            if f.get("ocr_scanned") and f.get("ocr_text_path"):
                try:
                    content = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(f["ocr_text_path"])
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb") as tmp:
                        tmp.write(content if isinstance(content, (bytes, bytearray)) else content.encode("utf-8"))
                        temp_path = tmp.name
                except Exception as e:
                    logger.warning(f"Failed downloading OCR text for {name}, falling back to original: {e}")

            if temp_path is None:
                content = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
                suffix = os.path.splitext(name)[1] or ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(content)
                    temp_path = tmp.name

            # Upload to OpenAI Files with retry/backoff
            def _create_file(p):
                with open(p, "rb") as fh:
                    return client.files.create(file=fh, purpose="assistants")

            created = _retry_call(_create_file, temp_path, retries=4, base_delay=1.0)

            # Attach to Vector Store with retry/backoff
            _retry_call(_attach_file_to_vector_store, client, vector_store_id, created.id, retries=4, base_delay=1.0)

            # Mark as ingested=True to indicate VS upload success
            try:
                supabase.table("files").update({"ingested": True}).eq("id", file_id).execute()
            except Exception as e:
                logger.warning(f"Uploaded {name} to VS but failed to mark ingested=True: {e}")

            uploaded += 1
            if per_call_sleep:
                time.sleep(per_call_sleep)
        except Exception as e:
            logger.error(f"Failed VS upload for {name} (id={file_id}): {e}")
            errors += 1
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    return {"vector_store_id": vector_store_id, "uploaded": uploaded, "skipped": skipped, "errors": errors}
