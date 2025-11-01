import io
import os
import tempfile
import logging
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from openai import OpenAI

from app.core.supabase_client import supabase

from app.core.extract_text import extract_text
from .gdrive_sync import run_responses_gdrive_sync
from .vs_ingest_worker import upload_missing_files_to_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/responses", tags=["responses-vector-store"])


class UploadResult(BaseModel):
    id: str
    name: str
    size: Optional[int] = None


def _get_vector_store_id(workspace_id: str) -> str:
    try:
        res = (
            supabase.table("workspace_vector_stores")
            .select("vector_store_id")
            .eq("workspace_id", workspace_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.error(f"Supabase error fetching vector_store_id: {e}")
        raise HTTPException(status_code=500, detail="Failed to query workspace vector store")
    row = getattr(res, "data", None)
    if not row or not row.get("vector_store_id"):
        raise HTTPException(status_code=404, detail="Vector store not found for workspace. Create it first.")
    return row["vector_store_id"]


def _attach_file_to_vector_store(client: OpenAI, vector_store_id: str, file_id: str):
    # Handle possible API variants
    try:
        client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)
        return
    except Exception as e1:
        # Older beta path
        try:
            getattr(client, "beta").vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)  # type: ignore
            return
        except Exception as e2:
            logger.error(f"Failed attaching file to vector store: {e1} | {e2}")
            raise HTTPException(status_code=500, detail="Failed to attach file to vector store")


def _delete_vs_file(client: OpenAI, vector_store_id: str, file_id: str):
    # Detach from Vector Store
    last_err = None
    for attr in ["delete", "del"]:
        try:
            getattr(client.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=file_id)
            last_err = None
            break
        except Exception as e:
            last_err = e
    if last_err:
        try:
            beta = getattr(client, "beta")
            for attr in ["delete", "del"]:
                try:
                    getattr(beta.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=file_id)
                    last_err = None
                    break
                except Exception as e2:
                    last_err = e2
        except Exception:
            pass
    if last_err:
        logger.error(f"Failed detaching file from vector store: {last_err}")
        raise HTTPException(status_code=500, detail="Failed detaching file from vector store")

    # Delete the underlying OpenAI File
    last_err = None
    for attr in ["delete", "del"]:
        try:
            getattr(client.files, attr)(file_id)
            last_err = None
            break
        except Exception as e:
            last_err = e
    if last_err:
        logger.warning(f"Detached but failed deleting OpenAI file: {last_err}")


def _flexible_detach(client: OpenAI, vector_store_id: str, vs_file_id: Optional[str], openai_file_id: Optional[str]) -> bool:
    """Attempt to detach using either vs_file_id or openai_file_id, tolerant of variants.
    Returns True if detachment appears successful or the file was already absent.
    """
    candidates = [c for c in [openai_file_id, vs_file_id] if c]
    if not candidates:
        return True  # nothing to detach

    # Try modern and beta namespaces; tolerate 404s/not-found
    for cid in candidates:
        last_err = None
        for api in [getattr(client, "vector_stores", None), getattr(getattr(client, "beta", None), "vector_stores", None)]:
            if api is None:
                continue
            for attr in ["delete", "del"]:
                try:
                    getattr(api.files, attr)(vector_store_id=vector_store_id, file_id=cid)
                    last_err = None
                    return True
                except Exception as e:
                    msg = f"{e}".lower()
                    if any(s in msg for s in ["not found", "no such", "not attached", "already"]) or getattr(e, "status", None) == 404:
                        return True
                    last_err = e
        if last_err:
            logger.debug(f"Detach attempt failed for candidate id {cid}: {last_err}")
    return False


@router.post("/upload", response_model=list[UploadResult])
async def upload_to_vector_store(
    workspace_id: str = Form(...),
    files: List[UploadFile] = File(...),
    ocr_pages: int = Form(0)
):
    """
    End-to-end pipeline for vector store upload:
    - Accepts files
    - Attempts text extraction; if low-content PDF and ocr_pages>0, runs lightweight OCR on first N pages
    - Uploads the best artifact (text, else original) to OpenAI Files
    - Attaches each file to the workspace vector store
    Returns: list of { id, name, size }
    """
    vector_store_id = _get_vector_store_id(workspace_id)
    client = OpenAI()

    results: List[UploadResult] = []

    for uf in files:
        # Save to temp file
        suffix = os.path.splitext(uf.filename or "upload.bin")[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            raw = await uf.read()
            tmp.write(raw)
            tmp_path = tmp.name

        text_to_use: Optional[str] = None
        try:
            text = extract_text(tmp_path)
            if text and len(text.strip()) >= 200:
                text_to_use = text
        except Exception as e:
            logger.warning(f"Text extraction failed for {uf.filename}: {e}")

        # Optional lightweight OCR on first N pages if PDF and no usable text
        if text_to_use is None and ocr_pages and suffix.lower() == ".pdf":
            try:
                from pdf2image import convert_from_path
                import pytesseract
                pages = convert_from_path(tmp_path)
                pages = pages[: max(1, min(len(pages), ocr_pages))]
                ocr_text = "\n\n".join(pytesseract.image_to_string(p) for p in pages)
                if ocr_text and len(ocr_text.strip()) >= 200:
                    text_to_use = ocr_text
            except Exception as e:
                logger.warning(f"Lightweight OCR failed for {uf.filename}: {e}")

        # Prepare and upload to OpenAI
        if text_to_use is not None:
            # Write text to temp .txt and upload
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as t:
                t.write(text_to_use)
                txt_path = t.name
            try:
                with open(txt_path, "rb") as fh:
                    created = client.files.create(file=fh, purpose="assistants")
                _attach_file_to_vector_store(client, vector_store_id, created.id)
                results.append(UploadResult(id=created.id, name=f"{uf.filename}.txt", size=len(text_to_use)))
            finally:
                try:
                    os.remove(txt_path)
                except Exception:
                    pass
        else:
            # Fallback to original file
            with open(tmp_path, "rb") as fh:
                created = client.files.create(file=fh, purpose="assistants")
            _attach_file_to_vector_store(client, vector_store_id, created.id)
            results.append(UploadResult(id=created.id, name=uf.filename or os.path.basename(tmp_path)))

        # Cleanup tmp
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return results


@router.get("/list")
def list_vector_store_files(workspace_id: str = Query(...)):
    vector_store_id = _get_vector_store_id(workspace_id)
    client = OpenAI()
    try:
        try:
            lst = client.vector_stores.files.list(vector_store_id=vector_store_id)
        except Exception:
            lst = getattr(client, "beta").vector_stores.files.list(vector_store_id=vector_store_id)  # type: ignore
    except Exception as e:
        logger.error(f"Failed listing vector store files: {e}")
        raise HTTPException(status_code=500, detail="Failed listing vector store files")

    # Normalize response
    items = []
    data = getattr(lst, "data", None) or []
    for it in data:
        items.append({
            "id": getattr(it, "id", None) or it.get("id"),
            "filename": getattr(it, "filename", None) or it.get("filename") or it.get("name"),
        })
    return {"vector_store_id": vector_store_id, "files": items}


@router.delete("/file/{file_id}")
def delete_vector_store_file(file_id: str, workspace_id: str = Query(...)):
    vector_store_id = _get_vector_store_id(workspace_id)
    client = OpenAI()
    _delete_vs_file(client, vector_store_id, file_id)
    return {"ok": True}


class SoftDeleteBody(BaseModel):
    workspace_id: str
    file_id: str
    also_delete_openai: bool = False
    also_delete_storage: bool = True


@router.post("/file/soft-delete")
def soft_delete_file(body: SoftDeleteBody):
    """
    Per-workspace soft delete:
    - Detach from Vector Store using stored ids (vs_file_id/openai_file_id)
    - Optionally delete the underlying OpenAI File and Storage object
    - Mark file_workspaces.deleted=true, ingested=false, clear ids, set deleted_at
    """
    ws_id = body.workspace_id
    file_id = body.file_id
    vector_store_id = _get_vector_store_id(ws_id)

    # Lookup join row + file path
    try:
        sel = (
            "openai_file_id, vs_file_id, ingested, deleted, files(file_path,name)"
        )
        res = (
            supabase.table("file_workspaces")
            .select(sel)
            .eq("workspace_id", ws_id)
            .eq("file_id", file_id)
            .maybe_single()
            .execute()
        )
        row = getattr(res, "data", None)
        if not row:
            raise HTTPException(status_code=404, detail="file_workspaces row not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Supabase error getting join row: {e}")
        raise HTTPException(status_code=500, detail="Failed to query file_workspaces")

    # Detach (tolerant)
    client = OpenAI()
    try:
        _flexible_detach(client, vector_store_id, row.get("vs_file_id"), row.get("openai_file_id"))
    except Exception as e:
        logger.warning(f"Detach attempt reported error (continuing): {e}")

    # Optionally delete OpenAI File
    if body.also_delete_openai and row.get("openai_file_id"):
        last_err = None
        for attr in ["delete", "del"]:
            try:
                getattr(client.files, attr)(row["openai_file_id"])  # type: ignore
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err:
            logger.warning(f"Failed deleting OpenAI file {row.get('openai_file_id')}: {last_err}")

    # Optionally delete storage object
    f = row.get("files") or {}
    if body.also_delete_storage and f.get("file_path"):
        try:
            supabase.storage.from_(os.getenv("SUPABASE_STORAGE_BUCKET", "files")).remove([f["file_path"]])  # type: ignore
        except Exception as e:
            logger.warning(f"Failed removing storage object {f.get('file_path')}: {e}")

    # Update join flags
    try:
        supabase.table("file_workspaces").update(
            {
                "deleted": True,
                "deleted_at": __import__("datetime").datetime.utcnow().isoformat(),
                "ingested": False,
                "openai_file_id": None,
                "vs_file_id": None,
            }
        ).eq("workspace_id", ws_id).eq("file_id", file_id).execute()
    except Exception as e:
        logger.error(f"Failed to update file_workspaces soft-delete flags: {e}")
        raise HTTPException(status_code=500, detail="Failed to update soft-delete flags")

    return {"ok": True}


@router.get("/file/status")
def get_file_status(file_id: str = Query(...), workspace_id: str = Query(...)):
    """
    Return per-workspace state for a file: ingested/deleted flags and stored IDs.
    """
    try:
        res = (
            supabase.table("file_workspaces")
            .select("ingested,deleted,deleted_at,openai_file_id,vs_file_id,files(name,file_path,ocr_scanned,ocr_needed)")
            .eq("workspace_id", workspace_id)
            .eq("file_id", file_id)
            .maybe_single()
            .execute()
        )
        row = getattr(res, "data", None)
        if not row:
            raise HTTPException(status_code=404, detail="file_workspaces row not found")
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get file status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get file status")


@router.post("/gdrive/sync", status_code=202)
async def trigger_gdrive_sync(background_tasks: BackgroundTasks):
    """
    Kick off a background Google Drive → Supabase sync that:
    - Finds new files in the configured Drive folder
    - Uploads to Supabase Storage + files table
    - For PDFs, performs OCR only when text extraction is insufficient
    - Does NOT run chunk/embedding
    """
    background_tasks.add_task(run_responses_gdrive_sync)
    return {"message": "GDrive sync (Supabase upload + OCR, no embedding) started."}


@router.post("/vector-store/ingest", status_code=202)
async def trigger_vector_store_ingest(background_tasks: BackgroundTasks):
    """
    Process backlog of Supabase files for the configured workspace where
    file_workspaces.ingested = false AND deleted = false, and upload them to the
    workspace Vector Store with retry/backoff and a configurable rate limit.
    """
    background_tasks.add_task(upload_missing_files_to_vector_store)
    return {"message": "Vector Store ingestion (pending files) started."}
