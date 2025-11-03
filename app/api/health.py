import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from openai import OpenAI

from app.core.config import settings
from app.core.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"]) 


@router.get("")
def health_root():
    """Basic liveness check."""
    return {"status": "ok", "service": settings.PROJECT_NAME}


def _get_vector_store_id(workspace_id: str) -> Optional[str]:
    try:
        res = (
            supabase.table("workspace_vector_stores")
            .select("vector_store_id")
            .eq("workspace_id", workspace_id)
            .maybe_single()
            .execute()
        )
        row = getattr(res, "data", None)
        return row.get("vector_store_id") if row else None
    except Exception as e:
        logger.warning(f"workspace_vector_stores lookup failed: {e}")
        return None


def _count_rows(table: str, workspace_id: Optional[str] = None, **filters) -> int:
    try:
        q = supabase.table(table).select("id")
        if workspace_id is not None and "workspace_id" in [c["name"] for c in getattr(q, "_columns", [{"name": "workspace_id"}])]:
            q = q.eq("workspace_id", workspace_id)
        for k, v in filters.items():
            q = q.eq(k, v)
        res = q.execute()
        data = getattr(res, "data", None) or []
        return len(data)
    except Exception as e:
        logger.warning(f"count_rows failed for {table}: {e}")
        return 0


@router.get("/ingestion")
def health_ingestion(workspace_id: Optional[str] = Query(None)):
    """Summarize ingestion state from Supabase and Vector Store.

    If workspace_id is provided, restrict metrics to that workspace. Otherwise return global rollups.
    """
    # file_workspaces rollups
    fw_filters = {"deleted": False}
    total_join = _count_rows("file_workspaces", workspace_id, **{})
    ingested_true = _count_rows("file_workspaces", workspace_id, ingested=True, deleted=False)
    ingested_pending = _count_rows("file_workspaces", workspace_id, ingested=False, deleted=False)
    deleted_count = _count_rows("file_workspaces", workspace_id, deleted=True)

    # files-side OCR hints (best-effort; may be absent in schema)
    ocr_scanned = _count_rows("files", None, ocr_scanned=True)
    ocr_needed = _count_rows("files", None, ocr_needed=True)

    # Vector Store count
    vs_id = None
    vs_file_count = None
    try:
        if workspace_id:
            vs_id = _get_vector_store_id(workspace_id)
        if vs_id:
            client = OpenAI()
            try:
                lst = client.vector_stores.files.list(vector_store_id=vs_id)
            except Exception:
                lst = getattr(client, "beta").vector_stores.files.list(vector_store_id=vs_id)  # type: ignore
            data = getattr(lst, "data", None) or []
            vs_file_count = len(data)
    except Exception as e:
        logger.warning(f"Vector Store listing failed: {e}")
        vs_file_count = None

    return {
        "workspace_id": workspace_id,
        "vector_store_id": vs_id,
        "file_workspaces": {
            "total": total_join,
            "ingested": ingested_true,
            "pending": ingested_pending,
            "deleted": deleted_count,
        },
        "files": {
            "ocr_scanned": ocr_scanned,
            "ocr_needed": ocr_needed,
        },
        **({"vector_store_files": vs_file_count} if vs_file_count is not None else {}),
        "status": "ok",
    }
