import io
import os
import tempfile
import logging
from typing import List, Optional, Tuple
import re
from datetime import date

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel

from openai import OpenAI
import httpx
import time

from app.core.supabase_client import supabase
from app.core.config import settings

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


# ------------------------------
# OpenAI REST helpers (HTTP-first)
# ------------------------------

def _openai_headers() -> dict:
    # Only auth header for Vector Stores and Files REST endpoints.
    # Do NOT include Assistants v2 beta header here; it is not required for these routes
    # and can cause schema/validation mismatches.
    return {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
    }


async def _http_request(method: str, url: str, *, json: dict | None = None, timeout: float = 15.0, retries: int = 3) -> httpx.Response:
    last_exc: Exception | None = None
    backoff = 0.5
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries):
            try:
                resp = await client.request(method, url, headers=_openai_headers(), json=json)
                # Treat 429/5xx as retryable
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = HTTPException(status_code=resp.status_code, detail=f"{resp.text}")
                    await asyncio_sleep(backoff)
                    backoff = min(4.0, backoff * 2)
                    continue
                return resp
            except Exception as e:  # network/timeout
                last_exc = e
                await asyncio_sleep(backoff)
                backoff = min(4.0, backoff * 2)
        raise HTTPException(status_code=500, detail=f"OpenAI HTTP request failed: {last_exc}")


async def asyncio_sleep(seconds: float):
    # small wrapper to avoid importing asyncio at top-level if not needed elsewhere
    import asyncio as _asyncio
    await _asyncio.sleep(seconds)


async def _list_vs_files_http(vector_store_id: str) -> list[dict]:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    url = f"{base}/v1/vector_stores/{vector_store_id}/files?limit=100"
    resp = await _http_request("GET", url)
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=f"VS list failed: {resp.text}")
    data = resp.json() or {}
    return data.get("data", [])


@router.get("/vector-store/progress")
async def vector_store_progress(
    workspace_id: Optional[str] = Query(None),
    vector_store_id: Optional[str] = Query(None),
    include_files: bool = Query(False),
):
    """
    Lightweight progress summary for a Vector Store's attachments.
    Returns counts by status and an overall done flag.

    Query:
      - workspace_id: resolve the store via DB mapping (preferred)
      - vector_store_id: use this store directly (override)
      - include_files: if true, include a compact per-file list
    """
    if not vector_store_id:
        if not workspace_id:
            raise HTTPException(status_code=400, detail="Provide either workspace_id or vector_store_id")
        vector_store_id = _get_vector_store_id(workspace_id)

    data = await _list_vs_files_http(vector_store_id)

    total = len(data)
    counts = {"in_progress": 0, "completed": 0, "failed": 0, "other": 0}
    files: list[dict] = []
    for it in data:
        status = (it.get("status") or "").lower()
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
        if include_files:
            files.append({
                "vs_file_id": it.get("id"),
                "file_id": it.get("file_id") or it.get("id"),
                "status": it.get("status"),
                "created_at": it.get("created_at"),
            })

    done = total > 0 and counts["in_progress"] == 0 and counts["failed"] == 0

    resp = {
        "vector_store_id": vector_store_id,
        "total": total,
        "counts": counts,
        "done": done,
    }
    if include_files:
        resp["files"] = files
    return resp


async def _delete_vs_attachment_http(vector_store_id: str, id_or_file_id: str) -> None:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    url = f"{base}/v1/vector_stores/{vector_store_id}/files/{id_or_file_id}"
    resp = await _http_request("DELETE", url)
    if not resp.is_success and resp.status_code != 404:
        raise HTTPException(status_code=resp.status_code, detail=f"VS detach failed: {resp.text}")


async def _delete_openai_file_http(file_id: str) -> None:
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
    url = f"{base}/v1/files/{file_id}"
    resp = await _http_request("DELETE", url)
    if not resp.is_success and resp.status_code != 404:
        raise HTTPException(status_code=resp.status_code, detail=f"File delete failed: {resp.text}")


def _attach_file_to_vector_store(client: OpenAI, vector_store_id: str, file_id: str) -> Optional[str]:
    """Attach file to Vector Store and return vs_file_id if the SDK returns it."""
    # Handle possible API variants
    try:
        obj = client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)
        # Try to pull id regardless of SDK shape
        return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)
    except Exception as e1:
        # Older beta path
        try:
            beta = getattr(client, "beta")
            obj = beta.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_id)  # type: ignore
            return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)
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


def _has_ocrmypdf() -> bool:
    import subprocess
    try:
        subprocess.run(["ocrmypdf", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except Exception:
        return False


def _run_ocrmypdf(src_pdf_path: str) -> Optional[str]:
    """Run ocrmypdf on src_pdf_path and return path to OCR'd PDF, or None on failure/missing binary."""
    import subprocess
    import tempfile
    if not _has_ocrmypdf():
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as dst:
        dst_path = dst.name
    cmd = [
        "ocrmypdf",
        "--skip-text",
        "--optimize",
        "0",
        src_pdf_path,
        dst_path,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            logger.warning(f"ocrmypdf failed: {proc.stderr.decode(errors='ignore')}")
            try:
                os.remove(dst_path)
            except Exception:
                pass
            return None
        return dst_path
    except Exception as e:
        logger.warning(f"ocrmypdf invocation error: {e}")
        try:
            os.remove(dst_path)
        except Exception:
            pass
        return None


def _derive_year_and_doctype(filename: str) -> tuple[Optional[str], Optional[str]]:
    fn = filename or ""
    m = re.search(r"\b(20\d{2}|19\d{2})\b", fn)
    year = m.group(1) if m else None
    low = fn.lower()
    doc_type = None
    # Recognize common civic document types
    if "agenda" in low:
        doc_type = "agenda"
    elif "minutes" in low:
        doc_type = "minutes"
    elif "ordinance" in low:
        doc_type = "ordinance"
    elif "transcript" in low or "transcipt" in low:  # tolerate misspelling
        doc_type = "transcript"
    elif "report" in low:
        doc_type = "report"
    return year, doc_type


def _parse_meeting_date_from_text(text: str) -> Optional[date]:
    """Best-effort meeting date parser from filename or small text snippet.
    Supports formats: YYYY-MM-DD, YYYY_MM_DD, MM-DD-YYYY, MM/DD/YYYY, Month D, YYYY.
    """
    if not text:
        return None
    t = text.strip()
    # 1) YYYY[-_/]MM[-_/]DD
    m = re.search(r"\b(20\d{2}|19\d{2})[-_\./](\d{1,2})[-_\./](\d{1,2})\b", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except Exception:
            pass
    # 2) MM[-_/]DD[-_/]YYYY
    m = re.search(r"\b(\d{1,2})[-_\./](\d{1,2})[-_\./](20\d{2}|19\d{2})\b", t)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d)
        except Exception:
            pass
    # 3) Month D, YYYY
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    m = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?\,\s*(20\d{2}|19\d{2})\b", t, flags=re.IGNORECASE)
    if m:
        mo = months[m.group(1).lower()]
        d = int(m.group(2))
        y = int(m.group(3))
        try:
            return date(y, mo, d)
        except Exception:
            pass
    return None


def _derive_meeting_body(text: str) -> Optional[str]:
    if not text:
        return None
    candidates = [
        "city council",
        "town council",
        "village council",
        "county commission",
        "planning commission",
        "board of education",
        "school board",
        "zoning board",
        "board of supervisors",
        "council meeting",
    ]
    low = text.lower()
    for c in candidates:
        if c in low:
            # Title case it for storage
            return " ".join(w.capitalize() for w in c.split())
    return None


def _derive_ordinance_number(text: str) -> Optional[str]:
    if not text:
        return None
    # Match patterns like: Ordinance 2023-15, Ord. No. 1234, Ordinance #4567
    m = re.search(r"\b(?:ordinance|ord\.)\s*(?:no\.|#)?\s*([A-Za-z0-9-]+)\b", text, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _file_ext_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    _, ext = os.path.splitext(name)
    return ext.lstrip(".").lower() if ext else None


def _safe_extract_text(path: str, limit: int = 2000) -> str:
    try:
        txt = extract_text(path) or ""
        return txt[:limit]
    except Exception as e:
        logger.debug(f"extract_text failed: {e}")
        return ""


def _upload_file_with_optional_metadata(client: OpenAI, file_path: str, metadata: Optional[dict] = None):
    """Create OpenAI File with purpose 'assistants'. Try with metadata, fallback without if unsupported."""
    with open(file_path, "rb") as fh:
        try:
            if metadata:
                return client.files.create(file=fh, purpose="assistants", metadata=metadata)  # type: ignore[arg-type]
            else:
                return client.files.create(file=fh, purpose="assistants")
        except Exception as e:
            # Retry without metadata if the SDK/server rejects it
            logger.debug(f"files.create with metadata failed, retrying without. err={e}")
            fh.seek(0)
            return client.files.create(file=fh, purpose="assistants")


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
        # Save to temp file (raw) but upload using a friendly filename path
        upload_dir = tempfile.mkdtemp(prefix="vs_upload_")
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
            # Write text to a path that uses the original filename (so OpenAI sees a friendly name)
            base = os.path.splitext(uf.filename or "upload")[0]
            desired = os.path.basename(f"{base}.txt")
            txt_path = os.path.join(upload_dir, desired)
            with open(txt_path, "w", encoding="utf-8") as t:
                t.write(text_to_use)
            with open(txt_path, "rb") as fh:
                try:
                    created = client.files.create(file=fh, purpose="assistants")
                except Exception:
                    fh.seek(0)
                    created = client.files.create(file=fh, purpose="assistants")
            _attach_file_to_vector_store(client, vector_store_id, created.id)
            results.append(UploadResult(id=created.id, name=f"{uf.filename}.txt", size=len(text_to_use)))
        else:
            # Fallback to original file: copy to a friendly-named path for upload
            desired = os.path.basename(uf.filename or "upload.bin")
            upload_path = os.path.join(upload_dir, desired)
            try:
                # Copy bytes to the upload path
                with open(tmp_path, "rb") as src, open(upload_path, "wb") as dst:
                    dst.write(src.read())
            except Exception:
                upload_path = tmp_path
            with open(upload_path, "rb") as fh:
                created = client.files.create(file=fh, purpose="assistants")
            _attach_file_to_vector_store(client, vector_store_id, created.id)
            results.append(UploadResult(id=created.id, name=uf.filename or os.path.basename(tmp_path)))

        # Cleanup tmp
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        try:
            # cleanup any upload artifacts
            if 'upload_path' in locals() and upload_path and os.path.exists(upload_path) and upload_path != tmp_path:
                os.remove(upload_path)
            if 'txt_path' in locals() and txt_path and os.path.exists(txt_path):
                os.remove(txt_path)
            if upload_dir and os.path.isdir(upload_dir):
                os.rmdir(upload_dir)
        except Exception:
            pass

    return results


@router.get("/list")
async def list_vector_store_files(workspace_id: str = Query(...), enrich: bool = Query(False)):
    vector_store_id = _get_vector_store_id(workspace_id)
    # HTTP-first for reliability across SDK variants
    data = await _list_vs_files_http(vector_store_id)

    items: list[dict] = []
    for it in data:
        # REST returns shape with id (vs_file_id) and file_id (underlying)
        vs_file_id = it.get("id")
        file_id = it.get("file_id") or it.get("id")
        item = {
            "vs_file_id": vs_file_id,
            "file_id": file_id,
            "status": it.get("status"),
            "created_at": it.get("created_at"),
        }
        items.append(item)

    # Optional enrichment with filename/bytes
    if enrich and items:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
        async with httpx.AsyncClient(timeout=10.0) as client:
            for item in items:
                fid = item.get("file_id")
                if not fid:
                    continue
                try:
                    r = await client.get(f"{base}/v1/files/{fid}", headers=_openai_headers())
                    if r.is_success:
                        meta = r.json() or {}
                        item["name"] = meta.get("filename")
                        item["size"] = meta.get("bytes")
                except Exception:
                    pass

    return {"vector_store_id": vector_store_id, "files": items}


@router.delete("/file/{id_or_file_id}")
async def delete_vector_store_file(id_or_file_id: str, workspace_id: str = Query(...), also_delete_file: bool = Query(True)):
    vector_store_id = _get_vector_store_id(workspace_id)
    # Try REST detach; tolerate 404
    await _delete_vs_attachment_http(vector_store_id, id_or_file_id)
    # Optionally delete underlying OpenAI file (best-effort)
    if also_delete_file and id_or_file_id.startswith("file-"):
        try:
            await _delete_openai_file_http(id_or_file_id)
        except Exception as e:
            logger.debug(f"OpenAI file delete failed (continuing): {e}")
    return {"ok": True, "vector_store_id": vector_store_id}


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
    if not settings.ENABLE_RESPONSES_GDRIVE_SYNC:
        raise HTTPException(status_code=403, detail="GDrive sync is disabled by configuration (ENABLE_RESPONSES_GDRIVE_SYNC=false)")
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


class PurgeBody(BaseModel):
    workspace_id: str
    delete_openai: bool = True
    reset_db_flags: bool = True


@router.post("/vector-store/purge")
async def purge_vector_store(body: PurgeBody):
    """
    HTTP-first purge: detach every attachment from the workspace's Vector Store via REST.
    Optionally delete the underlying OpenAI file. Optionally reset DB flags (ignored if DB empty).
    """
    vector_store_id = _get_vector_store_id(body.workspace_id)
    data = await _list_vs_files_http(vector_store_id)
    detached = 0
    for it in data:
        vs_file_id = it.get("id")
        file_id = it.get("file_id") or it.get("id")
        target = file_id or vs_file_id
        if not target:
            continue
        try:
            await _delete_vs_attachment_http(vector_store_id, target)
            detached += 1
        except Exception as e:
            logger.warning(f"Detach failed for {target}: {e}")
        if body.delete_openai and file_id and str(file_id).startswith("file-"):
            try:
                await _delete_openai_file_http(file_id)
            except Exception as e:
                logger.debug(f"OpenAI file delete failed (continuing): {e}")

    if body.reset_db_flags:
        try:
            supabase.table("file_workspaces").update({
                "ingested": False,
                "openai_file_id": None,
                "vs_file_id": None,
            }).eq("workspace_id", body.workspace_id).execute()
        except Exception as e:
            logger.debug(f"DB reset skipped/failed (continuing): {e}")

    return {"ok": True, "vector_store_id": vector_store_id, "detached": detached}


class HardPurgeBody(BaseModel):
    workspace_id: str
    also_delete_file: bool = True
    max_iters: int = 5
    sleep_ms: int = 500


@router.post("/vector-store/hard-purge")
async def hard_purge_vector_store(body: HardPurgeBody):
    """
    Aggressive purge loop that:
      - lists attachments via REST
      - deletes each by file_id (preferred) and falls back to id if needed
      - optionally deletes underlying OpenAI file
      - polls until empty or max_iters reached
    Ignores DB state entirely (safe for wiped DBs).
    """
    vector_store_id = _get_vector_store_id(body.workspace_id)

    iters = 0
    total_detached = 0
    while iters < body.max_iters:
        data = await _list_vs_files_http(vector_store_id)
        if not data:
            return {"ok": True, "vector_store_id": vector_store_id, "detached": total_detached, "iterations": iters}

        detached_this_round = 0
        for it in data:
            vs_file_id = it.get("id")
            file_id = it.get("file_id") or it.get("id")
            primary = file_id or vs_file_id
            if not primary:
                continue
            try:
                await _delete_vs_attachment_http(vector_store_id, primary)
                detached_this_round += 1
                total_detached += 1
            except Exception as e:
                logger.warning(f"Hard purge detach failed for {primary}: {e}")
            if body.also_delete_file and file_id and str(file_id).startswith("file-"):
                try:
                    await _delete_openai_file_http(file_id)
                except Exception as e:
                    logger.debug(f"OpenAI file delete failed (continuing): {e}")

        iters += 1
        if detached_this_round == 0:
            break
        await asyncio_sleep(body.sleep_ms / 1000.0)

    # Final check
    remaining = await _list_vs_files_http(vector_store_id)
    return {
        "ok": True,
        "vector_store_id": vector_store_id,
        "detached": total_detached,
        "iterations": iters,
        "remaining": len(remaining),
    }


@router.get("/vector-store/health")
def vector_store_health(workspace_id: str = Query(...)):
    """
    Report current health for a workspace's Vector Store and DB join state.
    Returns counts and small samples for quick diagnostics:
    - DB (file_workspaces) counts: active, ingested_true/false, with_openai_file_id, with_vs_file_id
    - Vector Store attachment count (OpenAI)
    - Dangling attachments: VS items without matching DB ids; DB ingested rows missing in VS
    """
    vector_store_id = _get_vector_store_id(workspace_id)
    client = OpenAI()

    # Gather DB rows for this workspace
    try:
        sel = (
            supabase.table("file_workspaces")
            .select("file_id, ingested, deleted, openai_file_id, vs_file_id, files(name)")
            .eq("workspace_id", workspace_id)
            .eq("deleted", False)
        )
        res = sel.execute()
        fw_rows = getattr(res, "data", None) or []
    except Exception as e:
        logger.error(f"Failed to query file_workspaces for health: {e}")
        raise HTTPException(status_code=500, detail="Failed to query DB for health")

    active = len(fw_rows)
    ing_true = sum(1 for r in fw_rows if r.get("ingested") is True)
    ing_false = sum(1 for r in fw_rows if r.get("ingested") is False)
    with_openai = sum(1 for r in fw_rows if (r.get("openai_file_id") or None))
    with_vs = sum(1 for r in fw_rows if (r.get("vs_file_id") or None))

    # Build lookup sets for DB-mapped ids
    db_openai_ids = {r.get("openai_file_id") for r in fw_rows if r.get("openai_file_id")}
    db_vs_ids = {r.get("vs_file_id") for r in fw_rows if r.get("vs_file_id")}

    # List VS attachments
    try:
        try:
            lst = client.vector_stores.files.list(vector_store_id=vector_store_id)
        except Exception:
            lst = getattr(client, "beta").vector_stores.files.list(vector_store_id=vector_store_id)  # type: ignore
    except Exception as e:
        logger.error(f"Failed listing vector store files for health: {e}")
        raise HTTPException(status_code=500, detail="Failed listing vector store files")

    vs_items = getattr(lst, "data", None) or []
    # Normalize to a set of candidate ids we can compare against DB mapping
    vs_ids = set()
    for it in vs_items:
        vid = getattr(it, "file_id", None) or getattr(it, "id", None) or (isinstance(it, dict) and (it.get("file_id") or it.get("id")))
        if vid:
            vs_ids.add(vid)

    # Dangling: in VS but not in DB
    vs_not_in_db = sorted(list(vs_ids - db_openai_ids - db_vs_ids))

    # Dangling: in DB (ingested) but not in VS (compare against either stored id)
    db_ing_missing = []
    for r in fw_rows:
        if r.get("ingested") is not True:
            continue
        cand_ids = [cid for cid in [r.get("openai_file_id"), r.get("vs_file_id")] if cid]
        if cand_ids and not any(cid in vs_ids for cid in cand_ids):
            # capture a small sample with filename for convenience
            name = (r.get("files") or {}).get("name")
            db_ing_missing.append({
                "file_id": r.get("file_id"),
                "name": name,
                "openai_file_id": r.get("openai_file_id"),
                "vs_file_id": r.get("vs_file_id"),
            })

    # Trim samples
    sample_vs_not_in_db = vs_not_in_db[:5]
    sample_db_ing_missing = db_ing_missing[:5]

    return {
        "workspace_id": workspace_id,
        "vector_store_id": vector_store_id,
        "db": {
            "file_workspaces": {
                "active": active,
                "ingested_true": ing_true,
                "ingested_false": ing_false,
                "with_openai_file_id": with_openai,
                "with_vs_file_id": with_vs,
            }
        },
        "vector_store": {
            "attachments": len(vs_ids),
        },
        "dangling": {
            "vs_without_db_mapping": {
                "count": len(vs_not_in_db),
                "sample_ids": sample_vs_not_in_db,
            },
            "db_ingested_missing_in_vs": {
                "count": len(db_ing_missing),
                "sample": sample_db_ing_missing,
            },
        },
    }


def _normalize_name(name: str) -> str:
    base = os.path.splitext(name or "")[0]
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "-", base.strip())
    s = re.sub(r"-+", "-", s)
    return s.strip("-").lower()


def _upsert_file_and_join(
    workspace_id: str,
    filename: str,
    openai_file_id: Optional[str],
    vs_file_id: Optional[str],
    meeting_date_iso: Optional[str] = None,
    meeting_year: Optional[int] = None,
    meeting_month: Optional[int] = None,
    meeting_day: Optional[int] = None,
    doc_type: Optional[str] = None,
    has_ocr: Optional[bool] = None,
    file_ext: Optional[str] = None,
    meeting_body: Optional[str] = None,
    ordinance_number: Optional[str] = None,
) -> str:
    """Create/update files and file_workspaces rows so the UI can list this upload.
    Returns the files.id used.
    """
    norm = _normalize_name(filename)

    # 1) Ensure a files row exists (lookup by exact name first)
    file_row = None
    try:
        sel = supabase.table("files").select("id,name").eq("name", filename).maybe_single().execute()
        file_row = getattr(sel, "data", None)
    except Exception:
        file_row = None
    if not file_row:
        # Insert a minimal files row; file_path can be null
        try:
            ins = supabase.table("files").insert({
                "name": filename,
            }).execute()
            data = getattr(ins, "data", None) or []
            file_row = data[0] if data else None
        except Exception as e:
            raise RuntimeError(f"Failed to insert files row: {e}")
    if not file_row or not file_row.get("id"):
        raise RuntimeError("files.id not found after upsert")
    file_id = file_row["id"]

    # 2) Upsert file_workspaces for this workspace + normalized_name
    join_row = None
    try:
        sel = (
            supabase.table("file_workspaces")
            .select("file_id, ingested, deleted, normalized_name, openai_file_id, vs_file_id")
            .eq("workspace_id", workspace_id)
            .eq("normalized_name", norm)
            .maybe_single()
            .execute()
        )
        join_row = getattr(sel, "data", None)
    except Exception:
        join_row = None

    payload = {
        "workspace_id": workspace_id,
        "file_id": file_id,
        "normalized_name": norm,
        "ingested": True,
        "deleted": False,
        "deleted_at": None,
        "openai_file_id": openai_file_id,
        "vs_file_id": vs_file_id,
    }

    # Add optional metadata fields
    if meeting_date_iso is not None:
        payload["meeting_date"] = meeting_date_iso
    if meeting_year is not None:
        payload["meeting_year"] = meeting_year
    if meeting_month is not None:
        payload["meeting_month"] = meeting_month
    if meeting_day is not None:
        payload["meeting_day"] = meeting_day
    if doc_type is not None:
        payload["doc_type"] = doc_type
    if has_ocr is not None:
        payload["has_ocr"] = has_ocr
    if file_ext is not None:
        payload["file_ext"] = file_ext
    if meeting_body is not None:
        payload["meeting_body"] = meeting_body
    if ordinance_number is not None:
        payload["ordinance_number"] = ordinance_number

    try:
        if join_row:
            supabase.table("file_workspaces").update(payload).eq("workspace_id", workspace_id).eq("normalized_name", norm).execute()
        else:
            supabase.table("file_workspaces").insert(payload).execute()
    except Exception as e:
        raise RuntimeError(f"Failed to upsert file_workspaces: {e}")

    return file_id


class IngestUploadResult(BaseModel):
    id: str
    name: str
    size: Optional[int] = None


@router.post("/vector-store/ingest/upload")
async def ingest_and_upload_to_vector_store(
    workspace_id: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """
    End-to-end ingestion under Responses:
    - Accept files (multipart)
    - For PDFs, attempt to create a single searchable OCR PDF via ocrmypdf
    - Derive small metadata (workspace_id, original_filename, mime_type, size, year, doc_type, source)
    - Optionally extract a short text sample and upload a tiny enrichment .context.txt
    - Upload the best artifact to OpenAI Files and attach to the workspace Vector Store
    Returns: { vector_store_id, files: [{ id, name, size }], failed: [{ name, reason }] }
    """
    vector_store_id = _get_vector_store_id(workspace_id)
    client = OpenAI()

    results: List[IngestUploadResult] = []
    failed: List[dict] = []

    for uf in files:
        # Persist upload to a tmp path, but upload using a friendly filename path
        upload_dir = tempfile.mkdtemp(prefix="vs_ingest_")
        suffix = os.path.splitext(uf.filename or "upload.bin")[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            raw = await uf.read()
            tmp.write(raw)
            tmp_path = tmp.name

        try:
            # If PDF, try to create a searchable OCR version
            ocr_path: Optional[str] = None
            if suffix.lower() == ".pdf":
                ocr_path = _run_ocrmypdf(tmp_path)

            # Build base metadata
            year, doc_type = _derive_year_and_doctype(uf.filename or "")
            base_metadata = {
                "workspace_id": workspace_id,
                "original_filename": uf.filename or None,
                "mime_type": getattr(uf, "content_type", None) or None,
                "size": len(raw) if raw else None,
                "year": year,
                "doc_type": doc_type,
            }

            # Extract a short sample for enrichment
            sample_text = ""
            if ocr_path:
                sample_text = _safe_extract_text(ocr_path, 2000)
            else:
                sample_text = _safe_extract_text(tmp_path, 2000)

            # Try to parse a more precise meeting date from filename+sample text
            meeting_date = _parse_meeting_date_from_text((uf.filename or "") + "\n" + sample_text)
            meeting_year = str(meeting_date.year) if meeting_date else (year or None)
            meeting_month = str(meeting_date.month) if meeting_date else None
            meeting_day = str(meeting_date.day) if meeting_date else None

            # Choose artifact and upload
            target_path = ocr_path or tmp_path
            source_label = "ocr-pdf" if ocr_path else "original"
            metadata = {**base_metadata, "source": source_label}
            if meeting_year:
                metadata["meeting_year"] = meeting_year
            if meeting_month:
                metadata["meeting_month"] = meeting_month
            if meeting_day:
                metadata["meeting_day"] = meeting_day
            # Additional soft-filter metadata
            has_ocr = bool(ocr_path)
            file_ext = _file_ext_from_name(uf.filename or "")
            meeting_body = _derive_meeting_body((uf.filename or "") + "\n" + sample_text)
            ord_no = _derive_ordinance_number((uf.filename or "") + "\n" + sample_text)
            metadata["has_ocr"] = has_ocr
            if file_ext:
                metadata["file_ext"] = file_ext
            if meeting_body:
                metadata["meeting_body"] = meeting_body
            if ord_no:
                metadata["ordinance_number"] = ord_no

            # Construct a friendly-named upload path for OpenAI (preserve human filename)
            res_name = (uf.filename or os.path.basename(target_path))
            if ocr_path and res_name.lower().endswith(".pdf"):
                res_name = res_name[:-4] + ".ocr.pdf"
            desired = os.path.basename(res_name)
            upload_path = os.path.join(upload_dir, desired)
            try:
                with open(target_path, "rb") as src, open(upload_path, "wb") as dst:
                    dst.write(src.read())
            except Exception:
                upload_path = target_path

            created = _upload_file_with_optional_metadata(client, upload_path, metadata)
            vs_file_id = _attach_file_to_vector_store(client, vector_store_id, created.id)
            results.append(IngestUploadResult(id=created.id, name=res_name, size=os.path.getsize(target_path)))

            # Upsert into Supabase DB: files and file_workspaces (primary artifact only)
            try:
                file_row_id = _upsert_file_and_join(
                    workspace_id=workspace_id,
                    filename=res_name,
                    openai_file_id=created.id,
                    vs_file_id=vs_file_id,
                    meeting_date_iso=(meeting_date.isoformat() if meeting_date else None),
                    meeting_year=(int(meeting_year) if meeting_year else None),
                    meeting_month=(int(meeting_month) if meeting_month else None),
                    meeting_day=(int(meeting_day) if meeting_day else None),
                    doc_type=doc_type,
                    has_ocr=has_ocr,
                    file_ext=file_ext,
                    meeting_body=meeting_body,
                    ordinance_number=ord_no,
                )
                logger.debug(f"Upserted DB rows for file '{res_name}' (file_id={file_row_id})")
            except Exception as db_e:
                logger.warning(f"DB upsert failed for '{res_name}': {db_e}")

            # Enrichment: small context file to reinforce year/doc_type and provide a brief excerpt
            lines: List[str] = []
            title = uf.filename or "file"
            context_header = ["[Context] Title: " + title]
            if year:
                context_header.append(f"Year: {year}")
            if doc_type:
                context_header.append(f"DocType: {doc_type}")
            lines.append(" | ".join(context_header))
            if year:
                # repeat year a couple times to increase chunk-level recall
                lines += [f"Year: {year}", f"Year: {year}"]
            if sample_text:
                lines.append("\nExcerpt:\n" + sample_text)
            enrichment_text = "\n".join(lines).strip()
            if enrichment_text:
                ctx_name = os.path.basename(f"{title}.context.txt")
                ctx_path = os.path.join(upload_dir, ctx_name)
                with open(ctx_path, "w", encoding="utf-8") as ctx:
                    ctx.write(enrichment_text)
                try:
                    ctx_meta = {**base_metadata, "source": "enrichment"}
                    if meeting_year:
                        ctx_meta["meeting_year"] = meeting_year
                    if meeting_month:
                        ctx_meta["meeting_month"] = meeting_month
                    if meeting_day:
                        ctx_meta["meeting_day"] = meeting_day
                    created_ctx = _upload_file_with_optional_metadata(client, ctx_path, ctx_meta)
                    _attach_file_to_vector_store(client, vector_store_id, created_ctx.id)
                    results.append(IngestUploadResult(id=created_ctx.id, name=f"{title}.context.txt", size=len(enrichment_text)))
                finally:
                    try:
                        os.remove(ctx_path)
                    except Exception:
                        pass

        except Exception as e:
            failed.append({"name": uf.filename or os.path.basename(tmp_path), "reason": str(e)})
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            try:
                if 'upload_path' in locals() and upload_path and os.path.exists(upload_path) and upload_path != tmp_path:
                    os.remove(upload_path)
                if upload_dir and os.path.isdir(upload_dir):
                    os.rmdir(upload_dir)
            except Exception:
                pass

    status = 200 if results else 500
    return {
        "vector_store_id": vector_store_id,
        "files": [r.dict() for r in results],
        **({"failed": failed} if failed else {}),
        "status": status,
    }
