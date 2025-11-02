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
# Note: multi-store mapping helpers live in app.api.Responses.vs_store_mapping
# When enabling Drive subfolder → store routing, resolve a per-file target store
# via vs_store_mapping.resolve_vector_store_for(workspace_id, drive_folder_id=..., label=...)

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


def _attach_file_to_vector_store(client: OpenAI, vector_store_id: str, file_id: str) -> Optional[str]:
    """Attach an OpenAI File to a Vector Store and return the VS file id if available."""
    last_err = None
    # Try modern namespace first
    try:
        res = client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)
        return getattr(res, "id", None)
    except Exception as e:
        last_err = e
        logger.warning("[vs_ingest_worker] vector_stores.files.create failed; trying beta fallback: %s", e)
    # Beta namespace fallback
    try:
        res = getattr(client, "beta").vector_stores.files.create(  # type: ignore
            vector_store_id=vector_store_id, file_id=file_id
        )
        return getattr(res, "id", None)
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


def _get_eligible_files(limit: int, workspace_id: Optional[str]) -> List[Dict]:
    """Pick files that should be uploaded to the Vector Store for a workspace.
    Policy (per-workspace): file_workspaces.ingested=False AND deleted=False AND
    either (files.ocr_needed=False) OR (files.ocr_scanned=True).
    """
    if not workspace_id:
        logger.error("Workspace id not provided for VS ingestion worker")
        return []
    try:
        # Join file_workspaces with files to get paths and OCR fields
        sel = (
            "file_id, workspace_id, ingested, deleted, openai_file_id, vs_file_id, "
            "files(id,name,file_path,type,ocr_needed,ocr_scanned,ocr_text_path)"
        )
        q = (
            supabase.table("file_workspaces")
            .select(sel)
            .eq("workspace_id", workspace_id)
            .eq("ingested", False)
            .eq("deleted", False)
            .limit(limit)
        )
        res = q.execute()
        rows = getattr(res, "data", []) or []
        eligible = []
        for r in rows:
            f = r.get("files") or {}
            if (not f.get("ocr_needed")) or f.get("ocr_scanned"):
                eligible.append(r)
        return eligible
    except Exception as e:
        logger.error(f"Failed to query eligible files (per-workspace): {e}")
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

    workspace_id = settings.GDRIVE_WORKSPACE_ID
    files = _get_eligible_files(batch_limit, workspace_id)
    uploaded = 0
    skipped = 0
    errors = 0

    for fw in files:
        file_id = fw["file_id"]
        f = fw.get("files") or {}
        name = f.get("name") or os.path.basename(f.get("file_path", "")) or f"file-{file_id}"
        file_path = f.get("file_path")
        content_type = f.get("type", "application/octet-stream")

        # Prefer OCR text if available
        temp_path = None
        try:
            if f.get("ocr_scanned") and f.get("ocr_text_path"):
                try:
                    content = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(f["ocr_text_path"])  # type: ignore
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb") as tmp:
                        tmp.write(content if isinstance(content, (bytes, bytearray)) else content.encode("utf-8"))
                        temp_path = tmp.name
                except Exception as e:
                    logger.warning(f"Failed downloading OCR text for {name}, falling back to original: {e}")

            if temp_path is None:
                content = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)  # type: ignore
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
            vs_file_id = _retry_call(_attach_file_to_vector_store, client, vector_store_id, created.id, retries=4, base_delay=1.0)

            # Mark per-workspace ingestion success on file_workspaces
            try:
                upd = {"ingested": True, "openai_file_id": created.id}
                if vs_file_id:
                    upd["vs_file_id"] = vs_file_id
                supabase.table("file_workspaces").update(upd).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
            except Exception as e:
                logger.warning(f"Uploaded {name} to VS but failed to mark file_workspaces.ingested=True: {e}")

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
