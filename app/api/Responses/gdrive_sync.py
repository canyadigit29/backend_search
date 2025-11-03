import base64
import io
import json
import logging
import os
import tempfile
from typing import Dict, Set, Tuple, Optional

from fastapi import HTTPException
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.core.config import settings
from app.core.extract_text import extract_text
from app.core.supabase_client import supabase
from app.services.file_processing_service import FileProcessingService
from openai import OpenAI

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _get_drive_service():
    if not settings.GOOGLE_CREDENTIALS_BASE64:
        raise HTTPException(status_code=500, detail="Google credentials are not configured.")
    try:
        creds_json_str = base64.b64decode(settings.GOOGLE_CREDENTIALS_BASE64).decode("utf-8")
        creds_info = json.loads(creds_json_str)
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES, subject=settings.GOOGLE_ADMIN_EMAIL
        )
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Error creating Google Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Google Drive service: {e}")


def _fetch_workspace_files(workspace_id: str) -> Dict[str, Dict[str, str]]:
    """Return mapping of filename -> { id, file_path } for a given workspace.
    Queries file_workspaces (deleted=false) joined to files to ensure we only consider
    files that belong to this workspace and are currently active.
    """
    if not workspace_id:
        raise Exception("Workspace id is required to fetch files for Drive sync")
    try:
        sel = (
            supabase.table("file_workspaces")
            .select("file_id, deleted, files(name,file_path)")
            .eq("workspace_id", workspace_id)
            .eq("deleted", False)
        )
        res = sel.execute()
        rows = getattr(res, "data", None) or []
        out: Dict[str, Dict[str, str]] = {}
        for r in rows:
            f = r.get("files") or {}
            name = f.get("name")
            if not name:
                continue
            out[name] = {"id": r.get("file_id"), "file_path": f.get("file_path")}
        return out
    except Exception as e:
        raise Exception(f"Error fetching workspace files from Supabase: {e}")


def _list_drive_files(service) -> Tuple[Set[str], list]:
    query = f"'{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
    drive_files = []
    page_token = None
    while True:
        results = (
            service.files()
            .list(
                q=query,
                pageSize=100,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        drive_files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break
    names = {f["name"] for f in drive_files}
    return names, drive_files


def _pdf_needs_ocr(temp_path: str) -> bool:
    """Heuristic: run extract_text and consider OCR if very little text.
    Matches the ingestion logic threshold (>= ~100 chars after removing page markers).
    """
    try:
        text = extract_text(temp_path)
        if not text:
            return True
        # Remove page delimiters if any and trim
        import re

        stripped = re.sub(r"---PAGE \d+---", "", text).strip()
        return len(stripped) < 100
    except Exception as e:
        logger.warning(f"extract_text failed on {temp_path}: {e}")
        return True


async def run_responses_gdrive_sync():
    """
    Responses-oriented GDrive sync:
    - Detects new files in the configured Drive folder
    - Uploads to Supabase (files row + Storage)
    - For PDFs, runs OCR if direct text extraction is insufficient
    - Does NOT perform chunking/embedding
    Deletions: If a file exists in Supabase but not in Drive, it is removed from Supabase
    storage and DB, and we also attempt to detach/delete it from the configured
    Vector Store by matching filename.
    """
    # Early exit if disabled in configuration
    if not settings.ENABLE_RESPONSES_GDRIVE_SYNC:
        logger.info("[responses.gdrive] Sync is disabled by configuration (ENABLE_RESPONSES_GDRIVE_SYNC=false)")
        return {"status": "disabled"}
    try:
        service = _get_drive_service()

        # Resolve workspace id early; Drive sync must be workspace-scoped
        workspace_id = settings.GDRIVE_WORKSPACE_ID or ""
        if not workspace_id:
            logger.error("[responses.gdrive] GDRIVE_WORKSPACE_ID is not set; cannot scope deletions safely.")
            return {"status": "error", "detail": "Missing GDRIVE_WORKSPACE_ID"}

        # Existing Supabase filenames (scoped to this workspace)
        sb_map = _fetch_workspace_files(workspace_id)
        sb_names = set(sb_map.keys())

        # Drive files and names
        drive_names, drive_files = _list_drive_files(service)

        new_names = drive_names - sb_names
        new_files = [f for f in drive_files if f["name"] in new_names]
        files_to_delete_names = sb_names - drive_names

        processed = 0
        ocr_started = 0

        def _normalize_name(name: str) -> str:
            base = os.path.splitext(name or "")[0]
            import re
            s = re.sub(r"[^a-zA-Z0-9]+", "-", base.strip())
            s = re.sub(r"-+", "-", s)
            return s.strip("-").lower()

    # workspace_id already resolved above
        # Ingest user to attribute ownership for joins; prefer workspace owner if available
        ingest_user_id: Optional[str] = None
        if workspace_id:
            try:
                ws = (
                    supabase.table("workspaces")
                    .select("user_id")
                    .eq("id", workspace_id)
                    .maybe_single()
                    .execute()
                )
                row = getattr(ws, "data", None)
                if row and row.get("user_id"):
                    ingest_user_id = row["user_id"]
            except Exception:
                ingest_user_id = None
        # Fallback to the same system user used when creating files if workspace owner isn't found
        if not ingest_user_id:
            ingest_user_id = "773e2630-2cca-44c3-957c-0cf5ccce7411"

        for gfile in new_files:
            file_id = gfile["id"]
            file_name = gfile["name"]
            content_type = gfile.get("mimeType", "application/octet-stream")
            logger.info(f"[responses.gdrive] New file: {file_name} ({file_id}) type={content_type}")

            # Download into memory and a temp file for optional extraction
            request = service.files().get_media(fileId=file_id)
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.info(f"Downloading {file_name}: {int(status.progress() * 100)}%")
            raw_bytes = buf.getvalue()

            # Upload to Supabase first (source-of-truth)
            result = await FileProcessingService.upload_and_register_file(
                user_id=ingest_user_id,
                file_content=raw_bytes,
                file_name=file_name,
                content_type=content_type,
                sharing="public",
            )
            processed += 1
            file_rec_id = result["file_id"]
            file_path = result["file_path"]

            # Upsert per-workspace join row so VS ingest worker can act
            try:
                if workspace_id:
                    norm = _normalize_name(file_name)
                    payload = {
                        "user_id": ingest_user_id,
                        "workspace_id": workspace_id,
                        "file_id": file_rec_id,
                        "normalized_name": norm,
                        "ingested": False,
                        "deleted": False,
                        "deleted_at": None,
                        "openai_file_id": None,
                        "vs_file_id": None,
                    }
                    # Update-if-exists else insert
                    try:
                        sel = (
                            supabase.table("file_workspaces")
                            .select("file_id")
                            .eq("workspace_id", workspace_id)
                            .eq("normalized_name", norm)
                            .maybe_single()
                            .execute()
                        )
                        row = getattr(sel, "data", None)
                    except Exception:
                        row = None
                    if row:
                        supabase.table("file_workspaces").update(payload).eq("workspace_id", workspace_id).eq("normalized_name", norm).execute()
                        logger.info(f"[responses.gdrive] file_workspaces updated: ws={workspace_id} name={file_name} norm={norm}")
                    else:
                        supabase.table("file_workspaces").insert(payload).execute()
                        logger.info(f"[responses.gdrive] file_workspaces inserted: ws={workspace_id} name={file_name} norm={norm}")
            except Exception as e:
                logger.warning(f"Failed to upsert file_workspaces join for {file_name}: {e}")

            # If PDF, decide whether to OCR
            if file_name.lower().endswith(".pdf"):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(raw_bytes)
                    tmp_path = tmp.name
                try:
                    if _pdf_needs_ocr(tmp_path):
                        logger.info(f"{file_name} appears to need OCR; starting OCR pipeline.")
                        # Mark OCR intent similar to ingestion path
                        try:
                            supabase.table("files").update({"ocr_needed": True}).eq("id", file_rec_id).execute()
                        except Exception:
                            pass
                        FileProcessingService.process_file_for_ocr(file_rec_id)
                        ocr_started += 1
                    else:
                        logger.info(f"{file_name} has sufficient text; skipping OCR.")
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        # Deletions: mirror original behavior (remove from storage + DB), plus delete from Vector Store if configured
        deleted = 0
        vs_deleted = 0

        if files_to_delete_names:
            # Vector store prep (optional)
            vector_store_id: Optional[str] = None
            if settings.GDRIVE_VECTOR_STORE_ID:
                vector_store_id = settings.GDRIVE_VECTOR_STORE_ID
            elif settings.GDRIVE_WORKSPACE_ID:
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
                        vector_store_id = row["vector_store_id"]
                except Exception as e:
                    logger.warning(f"Failed to resolve vector_store_id from Supabase: {e}")

            client: Optional[OpenAI] = None
            vs_name_to_ids: Dict[str, Set[str]] = {}

            if vector_store_id:
                try:
                    client = OpenAI()
                    # List vector store files, then retrieve filenames for mapping
                    try:
                        lst = client.vector_stores.files.list(vector_store_id=vector_store_id)
                    except Exception:
                        lst = getattr(client, "beta").vector_stores.files.list(vector_store_id=vector_store_id)  # type: ignore
                    data = getattr(lst, "data", None) or []
                    for it in data:
                        # Prefer explicit file_id; fall back to id
                        file_id = getattr(it, "file_id", None) or getattr(it, "id", None) or (isinstance(it, dict) and (it.get("file_id") or it.get("id")))
                        if not file_id:
                            continue
                        # Retrieve file meta to get filename
                        try:
                            meta = client.files.retrieve(file_id)
                            fname = (
                                getattr(meta, "filename", None)
                                or getattr(meta, "name", None)
                                or (isinstance(meta, dict) and (meta.get("filename") or meta.get("name")))
                            )
                        except Exception:
                            fname = None
                        if fname:
                            vs_name_to_ids.setdefault(fname, set()).add(file_id)
                except Exception as e:
                    logger.warning(f"Could not build vector store filename map: {e}")

            # Execute deletions
            for file_name in files_to_delete_names:
                file_info = sb_map[file_name]
                file_id = file_info["id"]
                file_path = file_info["file_path"]
                try:
                    supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).remove([file_path])
                except Exception as e:
                    logger.warning(f"Storage remove failed for {file_path}: {e}")
                try:
                    # Best-effort: remove per-workspace join before deleting file row (in case FK is not cascading)
                    try:
                        supabase.table("file_workspaces").delete().eq("workspace_id", workspace_id).eq("file_id", file_id).execute()
                    except Exception as je:
                        logger.debug(f"file_workspaces delete warning for file {file_id}: {je}")

                    supabase.table("files").delete().eq("id", file_id).execute()
                    deleted += 1
                except Exception as e:
                    logger.warning(f"DB delete failed for file id {file_id}: {e}")

                # Vector Store delete: prefer IDs from file_workspaces, fallback to filename map
                if client and vector_store_id:
                    # Try using stored IDs first
                    fw = None
                    try:
                        if workspace_id:
                            fw_res = (
                                supabase.table("file_workspaces")
                                .select("openai_file_id, vs_file_id, normalized_name")
                                .eq("workspace_id", workspace_id)
                                .eq("file_id", file_id)
                                .maybe_single()
                                .execute()
                            )
                            fw = getattr(fw_res, "data", None)
                    except Exception:
                        fw = None

                    candidate_ids = []
                    if fw:
                        if fw.get("openai_file_id"):
                            candidate_ids.append(fw["openai_file_id"])  # OpenAI File ID
                        if fw.get("vs_file_id") and fw["vs_file_id"] not in candidate_ids:
                            candidate_ids.append(fw["vs_file_id"])     # Vector Store file id (if applicable)

                    detached_any = False
                    deleted_any = False

                    # Attempt detach/delete using stored IDs
                    for fid in candidate_ids:
                        # Detach from VS
                        detached = False
                        for attr in ("delete", "del"):
                            try:
                                getattr(client.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=fid)
                                detached = True
                                break
                            except Exception:
                                pass
                        if not detached:
                            try:
                                beta = getattr(client, "beta")
                                for attr in ("delete", "del"):
                                    try:
                                        getattr(beta.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=fid)
                                        detached = True
                                        break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        if detached:
                            detached_any = True
                            vs_deleted += 1

                        # Delete underlying OpenAI file (try both ids)
                        for did in (fid,):
                            try:
                                for attr in ("delete", "del"):
                                    try:
                                        getattr(client.files, attr)(did)
                                        deleted_any = True
                                        break
                                    except Exception:
                                        continue
                            except Exception:
                                pass

                    # Fallback: use filename mapping if no stored IDs or all attempts failed
                    if not detached_any and vs_name_to_ids.get(file_name):
                        for fid in list(vs_name_to_ids[file_name]):
                            # Detach from VS
                            detached = False
                            for attr in ("delete", "del"):
                                try:
                                    getattr(client.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=fid)
                                    detached = True
                                    break
                                except Exception:
                                    pass
                            if not detached:
                                try:
                                    beta = getattr(client, "beta")
                                    for attr in ("delete", "del"):
                                        try:
                                            getattr(beta.vector_stores.files, attr)(vector_store_id=vector_store_id, file_id=fid)
                                            detached = True
                                            break
                                        except Exception:
                                            pass
                                except Exception:
                                    pass

                            # Delete underlying OpenAI file
                            try:
                                for attr in ("delete", "del"):
                                    try:
                                        getattr(client.files, attr)(fid)
                                        break
                                    except Exception:
                                        continue
                            except Exception:
                                pass

                            if detached:
                                vs_deleted += 1

        return {"status": "success", "new_files_processed": processed, "ocr_started": ocr_started, "files_deleted": deleted, "vs_deleted": vs_deleted}

    except Exception as e:
        logger.error(f"[responses.gdrive] Sync error: {e}")
        return {"status": "error", "detail": str(e)}
