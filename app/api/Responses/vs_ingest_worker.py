import io
import os
import tempfile
import time
import logging
from typing import Optional, List, Dict
from datetime import datetime, timezone

from openai import OpenAI
from fastapi import HTTPException

from app.core.config import settings
from app.core.supabase_client import supabase
from app.core.document_profiler import generate_profile_from_text
from app.core.openai_async_client import AsyncOpenAIClient
# Note: multi-store mapping helpers live in app.api.Responses.vs_store_mapping
# When enabling Drive subfolder → store routing, resolve a per-file target store
# via vs_store_mapping.resolve_vector_store_for(workspace_id, drive_folder_id=..., label=...)

logger = logging.getLogger(__name__)


def _extract_text_from_pdf_bytes(content: bytes) -> Optional[str]:
    """Best-effort text extraction from PDF bytes.
    Tries pypdf first; returns None on failure.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        logger.warning("[vs_ingest_worker] pypdf not available; cannot extract text from PDF.")
        return None

    try:
        reader = PdfReader(io.BytesIO(content))
        parts: List[str] = []
        for page in reader.pages:
            try:
                txt = page.extract_text() or ""
                if txt.strip():
                    parts.append(txt)
            except Exception:
                continue
        joined = "\n\n".join(parts).strip()
        return joined if joined else None
    except Exception as e:
        logger.warning(f"[vs_ingest_worker] Failed to extract PDF text: {e}")
        return None


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


def _derive_year_and_doctype(filename: str) -> tuple[Optional[int], Optional[str]]:
    """Lightweight metadata derivation from filename only (no content extraction).
    - Returns a (year, doc_type) tuple where doc_type ∈ {agenda, minutes, ordinance, transcript} when detected.
    """
    year: Optional[int] = None
    try:
        import re
        m = re.search(r"\b(20\d{2}|19\d{2})\b", filename or "")
        if m:
            year = int(m.group(1))
    except Exception:
        year = None
    low = (filename or "").lower()
    doc_type: Optional[str] = None
    if "agenda" in low:
        doc_type = "agenda"
    elif "minutes" in low:
        doc_type = "minutes"
    elif "ordinance" in low:
        doc_type = "ordinance"
    elif "transcript" in low:
        doc_type = "transcript"
    return year, doc_type


def _file_ext_from_name(name: str) -> Optional[str]:
    if not name:
        return None
    _, ext = os.path.splitext(name)
    return ext.lstrip(".").lower() if ext else None


def _derive_month_from_filename(filename: str) -> Optional[int]:
    """Best-effort month extraction from filename.
    Supports month names (jan, january, ... dec, december) and numeric patterns near a year.
    Returns 1-12 or None.
    """
    if not filename:
        return None
    low = filename.lower()
    # Month names
    months = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    try:
        import re
        mname = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", low)
        if mname:
            return months.get(mname.group(1), None)
        # Numeric patterns around a year
        # YYYY[-_/ ]MM
        mnum = re.search(r"\b(20\d{2}|19\d{2})[\-_/ ](1[0-2]|0?[1-9])\b", low)
        if mnum:
            val = int(mnum.group(2))
            return val if 1 <= val <= 12 else None
        # MM[-_/ ]YYYY
        mnum2 = re.search(r"\b(1[0-2]|0?[1-9])[\-_/ ](20\d{2}|19\d{2})\b", low)
        if mnum2:
            val = int(mnum2.group(1))
            return val if 1 <= val <= 12 else None
        # Fallback: YYYYMM or MMYYYY contiguous
        mnum3 = re.search(r"\b(20\d{2}|19\d{2})(1[0-2]|0[1-9])\b", low)
        if mnum3:
            val = int(mnum3.group(2))
            return val if 1 <= val <= 12 else None
        mnum4 = re.search(r"\b(1[0-2]|0[1-9])(20\d{2}|19\d{2})\b", low)
        if mnum4:
            val = int(mnum4.group(1))
            return val if 1 <= val <= 12 else None
    except Exception:
        return None
    return None


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
    ingest_failed=False AND either (files.ocr_needed=False) OR (files.ocr_scanned=True).
    """
    if not workspace_id:
        logger.error("[vs_ingest_worker] Workspace id not provided for VS ingestion worker")
        return []
    
    logger.info(f"[vs_ingest_worker] Querying for eligible files for workspace_id: {workspace_id}")
    
    try:
        # Join file_workspaces with files to get paths and OCR fields
        sel = (
            "file_id, workspace_id, ingested, deleted, openai_file_id, vs_file_id, ingest_retries, "
            "files(id,name,file_path,type,ocr_needed,ocr_scanned,ocr_text_path)"
        )
        q = (
            supabase.table("file_workspaces")
            .select(sel)
            .eq("workspace_id", workspace_id)
            .eq("ingested", False)
            .eq("deleted", False)
            .eq("ingest_failed", False)
            .limit(limit)
        )
        
        logger.info(f"[vs_ingest_worker] Executing query for pending ingestion.")
        res = q.execute()
        rows = getattr(res, "data", []) or []
        
        logger.info(f"[vs_ingest_worker] Found {len(rows)} candidate rows with ingested=false.")

        eligible = []
        for r in rows:
            f = r.get("files") or {}
            ocr_needed = f.get("ocr_needed", False)
            ocr_scanned = f.get("ocr_scanned", False)
            
            is_eligible = (not ocr_needed) or ocr_scanned
            if is_eligible:
                eligible.append(r)
            else:
                logger.info(f"[vs_ingest_worker] Skipping file_id {r.get('file_id')}: ocr_needed={ocr_needed}, ocr_scanned={ocr_scanned}")

        logger.info(f"[vs_ingest_worker] Found {len(eligible)} eligible files to process.")
        return eligible
    except Exception as e:
        logger.error(f"[vs_ingest_worker] Failed to query eligible files (per-workspace): {e}", exc_info=True)
        return []


def _get_unprofiled_files(limit: int, workspace_id: Optional[str]) -> List[Dict]:
    """Return files that are not deleted and have doc_profile_processed=false.
    We still require text availability: (ocr_needed=false) OR (ocr_scanned=true).
    """
    if not workspace_id:
        logger.error("[vs_ingest_worker] Workspace id not provided for doc profiling pass")
        return []

    logger.info(f"[vs_ingest_worker] Querying for unprofiled files for workspace_id: {workspace_id}")
    try:
        sel = (
            "file_id, workspace_id, ingested, deleted, openai_file_id, vs_file_id, doc_profile_processed, "
            "files(id,name,file_path,type,ocr_needed,ocr_scanned,ocr_text_path)"
        )
        q = (
            supabase.table("file_workspaces")
            .select(sel)
            .eq("workspace_id", workspace_id)
            .eq("deleted", False)
            .eq("doc_profile_processed", False)
            .limit(limit)
        )
        res = q.execute()
        rows = getattr(res, "data", []) or []
        logger.info(f"[vs_ingest_worker] Found {len(rows)} candidate rows with doc_profile_processed=false.")

        eligible: List[Dict] = []
        for r in rows:
            f = r.get("files") or {}
            ocr_needed = f.get("ocr_needed", False)
            ocr_scanned = f.get("ocr_scanned", False)
            if (not ocr_needed) or ocr_scanned:
                eligible.append(r)
            else:
                logger.info(
                    f"[vs_ingest_worker] Skipping file_id {r.get('file_id')} for profiling: ocr_needed={ocr_needed}, ocr_scanned={ocr_scanned}"
                )
        logger.info(f"[vs_ingest_worker] Found {len(eligible)} unprofiled eligible files to process.")
        return eligible
    except Exception as e:
        logger.warning("[vs_ingest_worker] Unprofiled query failed (column missing?). Skipping profiling pass.", exc_info=True)
        return []


async def upload_missing_files_to_vector_store():
    """Uploads pending Supabase files into the OpenAI Vector Store with backoff.
    On success, sets files.ingested=True (repurposed to mean VS-uploaded).
    Rate is controlled by VS_UPLOAD_DELAY_MS and VS_UPLOAD_BATCH_LIMIT envs.
    """
    logger.info("[vs_ingest_worker] Starting upload_missing_files_to_vector_store task.")
    try:
        vector_store_id = _resolve_vector_store_id()
        logger.info(f"[vs_ingest_worker] Resolved vector_store_id: {vector_store_id}")
    except Exception as e:
        logger.error(f"[vs_ingest_worker] Could not resolve vector_store_id. Aborting. Error: {e}", exc_info=True)
        return {"error": "Failed to resolve vector_store_id"}

    client = OpenAI()
    async_openai_client = AsyncOpenAIClient()
    delay_ms = max(0, int(settings.VS_UPLOAD_DELAY_MS))
    per_call_sleep = delay_ms / 1000.0
    batch_limit = max(1, int(settings.VS_UPLOAD_BATCH_LIMIT))
    max_retries = int(os.environ.get("VS_INGEST_MAX_RETRIES", 5))

    workspace_id = settings.GDRIVE_WORKSPACE_ID
    if not workspace_id:
        logger.error("[vs_ingest_worker] GDRIVE_WORKSPACE_ID is not set. Aborting.")
        return {"error": "GDRIVE_WORKSPACE_ID not set"}

    files = _get_eligible_files(batch_limit, workspace_id)
    uploaded = 0
    skipped = 0
    errors = 0
    profiles_attempted = 0
    profiles_saved = 0

    if not files:
        logger.info("[vs_ingest_worker] No eligible files to upload.")

    for fw in (files or []):
        file_id = fw["file_id"]
        f = fw.get("files") or {}
        name = f.get("name") or os.path.basename(f.get("file_path", "")) or f"file-{file_id}"
        file_path = f.get("file_path")
        content_type = f.get("type", "application/octet-stream")
        
        logger.info(f"[vs_ingest_worker] Processing file: {name} (file_id: {file_id})")

        # Prefer OCR text if available
        temp_path = None
        upload_dir = None
        try:
            ocr_text_path = f.get("ocr_text_path")
            # Reflect actual OCR status from files.ocr_scanned; don't rely solely on text path presence
            has_ocr = bool(f.get("ocr_scanned"))
            text_content_for_profiling = None

            if f.get("ocr_scanned") and ocr_text_path:
                logger.info(f"[vs_ingest_worker] Attempting to download OCR text from: {ocr_text_path}")
                try:
                    content_bytes = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(ocr_text_path)
                    # Text path present implies we used OCR-extracted text
                    has_ocr = True
                    # Create a stable temp file path using the original base name, so OpenAI sees a friendly filename
                    upload_dir = tempfile.mkdtemp(prefix="vs_ingest_")
                    base, _ = os.path.splitext(name)
                    desired = f"{base}.txt"
                    temp_path = os.path.join(upload_dir, desired)
                    
                    # Decode for profiling, write bytes to temp file for upload
                    try:
                        text_content_for_profiling = content_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning(f"[vs_ingest_worker] Could not decode OCR text as utf-8 for profiling, file_id: {file_id}")
                        text_content_for_profiling = None # Will skip profiling

                    with open(temp_path, "wb") as tmp:
                        tmp.write(content_bytes)

                    logger.info(f"[vs_ingest_worker] Successfully created temp file with OCR text at: {temp_path}")
                except Exception as e:
                    logger.warning(f"[vs_ingest_worker] Failed downloading OCR text for {name}, falling back to original: {e}")
            
            if temp_path is None:
                logger.info(f"[vs_ingest_worker] No OCR text used. Downloading original file from: {file_path}")
                content_bytes = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
                # Create a stable temp file path using the original filename, preserving its extension
                upload_dir = tempfile.mkdtemp(prefix="vs_ingest_") if upload_dir is None else upload_dir
                suffix = os.path.splitext(name)[1] or ".bin"
                desired = name if os.path.splitext(name)[1] else f"{name}{suffix}"
                # Sanitize desired filename minimally to avoid path traversal
                desired = os.path.basename(desired)
                temp_path = os.path.join(upload_dir, desired)
                with open(temp_path, "wb") as tmp:
                    tmp.write(content_bytes)
                logger.info(f"[vs_ingest_worker] Successfully created temp file with original content at: {temp_path}")

                # Attempt to get text for profiling from text-based files (including PDFs)
                if temp_path.lower().endswith(('.txt', '.md', '.json')):
                    try:
                        text_content_for_profiling = content_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning(f"[vs_ingest_worker] Could not decode text file as utf-8 for profiling, file_id: {file_id}")
                elif temp_path.lower().endswith('.pdf'):
                    pdf_text = _extract_text_from_pdf_bytes(content_bytes)
                    if pdf_text:
                        text_content_for_profiling = pdf_text


            # Upload to OpenAI Files with retry/backoff
            def _create_file(p):
                with open(p, "rb") as fh:
                    logger.info(f"[vs_ingest_worker] Uploading {p} to OpenAI Files API.")
                    # Attach a tiny bit of metadata to aid later debugging (optional)
                    try:
                        return client.files.create(file=fh, purpose="assistants", metadata={
                            "source": "ocr_text" if temp_path and temp_path.lower().endswith('.txt') else "original",
                            "workspace_id": workspace_id or "",
                            "original_filename": name,
                        })
                    except Exception:
                        # Fallback for SDKs/environments that don't accept metadata
                        logger.warning("[vs_ingest_worker] OpenAI files.create with metadata failed, retrying without.")
                        fh.seek(0)
                        return client.files.create(file=fh, purpose="assistants")

            created = _retry_call(_create_file, temp_path, retries=4, base_delay=1.0)
            logger.info(f"[vs_ingest_worker] Successfully created OpenAI File ID: {created.id}")

            # Attach to Vector Store with retry/backoff
            vs_file_id = _retry_call(_attach_file_to_vector_store, client, vector_store_id, created.id, retries=4, base_delay=1.0)
            logger.info(f"[vs_ingest_worker] Successfully attached to Vector Store. VS File ID: {vs_file_id}")

            # --- Persist baseline ingestion metadata on file_workspaces ---
            try:
                # Derive light metadata from filename
                year, derived_doc_type = _derive_year_and_doctype(name)
                month = _derive_month_from_filename(name)
                ext = _file_ext_from_name(name)

                supabase.table("file_workspaces").update({
                    "ingested": True,
                    "openai_file_id": created.id,
                    "vs_file_id": vs_file_id,
                    "has_ocr": bool(has_ocr),
                    "file_ext": ext,
                    "doc_type": derived_doc_type,
                    "meeting_year": year,
                    "meeting_month": month,
                }).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
                logger.info(f"[vs_ingest_worker] Updated file_workspaces baseline metadata for file_id: {file_id}")
            except Exception as meta_e:
                logger.error(f"[vs_ingest_worker] Failed to update baseline ingestion metadata for file_id {file_id}: {meta_e}")

            # --- Document Profiling Step ---
            profile_saved = False
            if text_content_for_profiling:
                logger.info(f"[vs_ingest_worker] Generating document profile for file_id: {file_id}")
                try:
                    profile = await generate_profile_from_text(text_content_for_profiling)
                    if profile:
                        profile_data = {
                            "file_id": file_id,
                            "workspace_id": workspace_id,
                            "summary": profile.get("summary"),
                            "keywords": profile.get("keywords"),
                            "entities": profile.get("entities"),
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                        }
                        # Deprecated: document_profiles table removed; persist directly to file_workspaces only
                        # Best-effort: also persist profile onto file_workspaces if columns exist
                        try:
                            # Persist profile onto file_workspaces and mark processed
                            now_iso = datetime.now(timezone.utc).isoformat()
                            supabase.table("file_workspaces").update({
                                "profile_summary": profile.get("summary"),
                                "profile_keywords": profile.get("keywords"),
                                "profile_entities": profile.get("entities"),
                                "doc_profile_processed": True,
                                "doc_profile_processed_at": now_iso,
                            }).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
                        except Exception as e_profile_cols:
                            logger.debug(f"[vs_ingest_worker] file_workspaces profile columns update failed (continuing): {e_profile_cols}")
                        profile_saved = True
                        profiles_saved += 1
                        logger.info(f"[vs_ingest_worker] Successfully saved document profile for file_id: {file_id}")
                    else:
                        logger.warning(f"[vs_ingest_worker] Document profiling returned no data for file_id: {file_id}")
                except Exception as e_profile_gen:
                    logger.error(f"[vs_ingest_worker] Failed to generate or save document profile for file_id {file_id}: {e_profile_gen}", exc_info=True)
                finally:
                    profiles_attempted += 1
            else:
                logger.info(f"[vs_ingest_worker] Skipping document profiling for file_id {file_id} (no text content).")

            uploaded += 1
            if per_call_sleep:
                time.sleep(per_call_sleep)
        except Exception as e:
            logger.error(f"[vs_ingest_worker] Failed VS upload for {name} (id={file_id}): {e}", exc_info=True)
            errors += 1
            # Increment retry counter and mark as failed if limit is exceeded
            try:
                retries = (fw.get("ingest_retries") or 0) + 1
                update_payload = {"ingest_retries": retries}
                if retries >= max_retries:
                    update_payload["ingest_failed"] = True
                    logger.error(f"File {name} (id={file_id}) has failed ingestion {retries} times and will be marked as failed.")
                supabase.table("file_workspaces").update(update_payload).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
            except Exception as db_e:
                logger.error(f"Failed to update retry count for file {file_id}: {db_e}")
        finally:
            # Cleanup temp file and directory
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            try:
                if upload_dir and os.path.isdir(upload_dir):
                    os.rmdir(upload_dir)
            except Exception:
                pass
    
    # Second pass: profile-only for already-ingested but unprofiled files
    profile_only = _get_unprofiled_files(batch_limit, workspace_id)
    profiled = 0
    if profile_only:
        logger.info(f"[vs_ingest_worker] Starting profile-only pass for {len(profile_only)} files.")
    else:
        logger.info("[vs_ingest_worker] No unprofiled files found for profile-only pass.")

    for fw in profile_only:
        file_id = fw["file_id"]
        f = fw.get("files") or {}
        name = f.get("name") or os.path.basename(f.get("file_path", "")) or f"file-{file_id}"
        file_path = f.get("file_path")

        try:
            ocr_text_path = f.get("ocr_text_path")
            text_content_for_profiling = None

            if f.get("ocr_scanned") and ocr_text_path:
                try:
                    content_bytes = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(ocr_text_path)
                    try:
                        text_content_for_profiling = content_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning(f"[vs_ingest_worker] Could not decode OCR text as utf-8 for profiling (profile-only), file_id: {file_id}")
                except Exception as e:
                    logger.warning(f"[vs_ingest_worker] Failed downloading OCR text (profile-only) for {name}: {e}")

            if text_content_for_profiling is None:
                try:
                    content_bytes = supabase.storage.from_(settings.SUPABASE_STORAGE_BUCKET).download(file_path)
                    lname = (name or "").lower()
                    if lname.endswith((".txt", ".md", ".json")):
                        try:
                            text_content_for_profiling = content_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            logger.warning(f"[vs_ingest_worker] Could not decode text file as utf-8 (profile-only), file_id: {file_id}")
                    elif lname.endswith('.pdf'):
                        pdf_text = _extract_text_from_pdf_bytes(content_bytes)
                        if pdf_text:
                            text_content_for_profiling = pdf_text
                except Exception as e:
                    logger.warning(f"[vs_ingest_worker] Failed downloading original content (profile-only) for {name}: {e}")

            if not text_content_for_profiling:
                logger.info(f"[vs_ingest_worker] Skipping profile-only for file_id {file_id} (no text content).")
                continue

            # Generate and save profile
            try:
                profile = await generate_profile_from_text(text_content_for_profiling)
                if profile:
                    profile_data = {
                        "file_id": file_id,
                        "workspace_id": workspace_id,
                        "summary": profile.get("summary"),
                        "keywords": profile.get("keywords"),
                        "entities": profile.get("entities"),
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                    }
                    # Deprecated: document_profiles table removed; persist directly to file_workspaces only
                    # Best-effort: also persist profile onto file_workspaces if columns exist
                    try:
                        supabase.table("file_workspaces").update({
                            "profile_summary": profile.get("summary"),
                            "profile_keywords": profile.get("keywords"),
                            "profile_entities": profile.get("entities"),
                            "profile_generated_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
                    except Exception as e_profile_cols:
                        logger.debug(f"[vs_ingest_worker] file_workspaces profile columns not present or update failed (continuing): {e_profile_cols}")
                    supabase.table("file_workspaces").update({
                        "doc_profile_processed": True,
                        "doc_profile_processed_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("file_id", file_id).eq("workspace_id", workspace_id).execute()
                    profiled += 1
                    profiles_saved += 1
                    logger.info(f"[vs_ingest_worker] Profile-only saved for file_id: {file_id}")
                else:
                    logger.warning(f"[vs_ingest_worker] Profile-only generation returned no data for file_id: {file_id}")
            except Exception as e_profile:
                logger.error(f"[vs_ingest_worker] Profile-only generation failed for file_id {file_id}: {e_profile}", exc_info=True)
            finally:
                profiles_attempted += 1

            if per_call_sleep:
                time.sleep(per_call_sleep)
        except Exception as e:
            logger.error(f"[vs_ingest_worker] Profile-only pass failed for {name} (id={file_id}): {e}", exc_info=True)

    logger.info(
        f"[vs_ingest_worker] Task finished. Uploaded: {uploaded}, Skipped: {skipped}, Errors: {errors}, Profiles attempted: {profiles_attempted}, Profiles saved: {profiles_saved}, Profiled (profile-only): {profiled}"
    )
    return {
        "vector_store_id": vector_store_id,
        "uploaded": uploaded,
        "skipped": skipped,
        "errors": errors,
        "profiles_attempted": profiles_attempted,
        "profiles_saved": profiles_saved,
        "profiled": profiled,
    }
