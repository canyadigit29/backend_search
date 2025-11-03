import os
import re
import sys
import math
from typing import Optional, Dict, Any

from app.core.config import settings
from app.core.supabase_client import supabase

# Reuse simple derivation logic compatible with vs_ingest_worker

def _file_ext_from_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in base:
        return None
    ext = base.rsplit(".", 1)[-1].lower()
    return ext or None


def _derive_year_and_doctype(filename: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    if not filename:
        return None, None
    year: Optional[int] = None
    try:
        m = re.search(r"\b(20\d{2}|19\d{2})\b", filename)
        if m:
            year = int(m.group(1))
    except Exception:
        year = None
    low = filename.lower()
    doc_type: Optional[str] = None
    if "agenda" in low:
        doc_type = "agenda"
    elif "minutes" in low or "minute" in low:
        doc_type = "minutes"
    elif "ordinance" in low or "ordinances" in low:
        doc_type = "ordinance"
    elif "transcript" in low or "transcripts" in low:
        doc_type = "transcript"
    return year, doc_type


def _derive_month_from_filename(filename: Optional[str]) -> Optional[int]:
    if not filename:
        return None
    low = filename.lower()
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
        mnum = re.search(r"\b(20\d{2}|19\d{2})[\-_/ ](1[0-2]|0?[1-9])\b", low)
        if mnum:
            val = int(mnum.group(2))
            return val if 1 <= val <= 12 else None
        mnum2 = re.search(r"\b(1[0-2]|0?[1-9])[\-_/ ](20\d{2}|19\d{2})\b", low)
        if mnum2:
            val = int(mnum2.group(1))
            return val if 1 <= val <= 12 else None
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


def is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def backfill_metadata_for_workspace(workspace_id: str, page_size: int = 500, dry_run: bool = False) -> Dict[str, int]:
    """Backfill file_workspaces metadata columns for rows missing values.
    - Targets columns: has_ocr (bool), file_ext (text), doc_type (text), meeting_year (int)
    - Derives from joined files record: name, ocr_scanned, ocr_text_path
    """
    target_cols = ("has_ocr", "file_ext", "doc_type", "meeting_year", "meeting_month")

    # Count rows for progress
    cnt_res = (
        supabase.table("file_workspaces")
        .select("file_id", count="exact")
        .eq("workspace_id", workspace_id)
        .or_("has_ocr.is.null,file_ext.is.null,doc_type.is.null,meeting_year.is.null,meeting_month.is.null")
        .execute()
    )
    total = getattr(cnt_res, "count", None) or 0
    pages = int(math.ceil(total / float(page_size))) if total else 0

    updated = 0
    failed = 0
    examined = 0

    for p in range(pages or 1):
        start = p * page_size
        end = start + page_size - 1
        sel = (
            "file_id, workspace_id, has_ocr, file_ext, doc_type, meeting_year, meeting_month, "
            "files(id,name,ocr_scanned,ocr_text_path)"
        )
        res = (
            supabase.table("file_workspaces")
            .select(sel)
            .eq("workspace_id", workspace_id)
            .or_("has_ocr.is.null,file_ext.is.null,doc_type.is.null,meeting_year.is.null,meeting_month.is.null")
            .range(start, end)
            .execute()
        )
        rows = getattr(res, "data", []) or []
        if not rows:
            break
        for r in rows:
            examined += 1
            f = r.get("files") or {}
            name = f.get("name")
            # Compute candidates from filename/OCR fields
            cand_has_ocr = bool(f.get("ocr_scanned")) or bool(f.get("ocr_text_path"))
            cand_ext = _file_ext_from_name(name)
            cand_year, cand_doctype = _derive_year_and_doctype(name)
            cand_month = _derive_month_from_filename(name)

            # Build partial update only for missing fields
            upd: Dict[str, Any] = {}
            if is_missing(r.get("has_ocr")):
                upd["has_ocr"] = cand_has_ocr
            if is_missing(r.get("file_ext")) and cand_ext:
                upd["file_ext"] = cand_ext
            if is_missing(r.get("doc_type")) and cand_doctype:
                upd["doc_type"] = cand_doctype
            if is_missing(r.get("meeting_year")) and cand_year:
                upd["meeting_year"] = cand_year
            if is_missing(r.get("meeting_month")) and cand_month:
                upd["meeting_month"] = cand_month

            if not upd:
                continue

            if dry_run:
                print(f"DRY_RUN: would update file_id={r.get('file_id')} with {upd}")
            else:
                try:
                    supabase.table("file_workspaces").update(upd).eq("file_id", r.get("file_id")).eq("workspace_id", workspace_id).execute()
                    updated += 1
                except Exception as e:
                    failed += 1
                    print(f"WARN: failed to update file_id={r.get('file_id')} with {upd}: {e}", file=sys.stderr)

    return {"workspace_id": workspace_id, "examined": examined, "updated": updated, "failed": failed}


def main():
    # Resolve workspace_id: prefer CLI arg > BACKFILL_WORKSPACE_ID env > settings.GDRIVE_WORKSPACE_ID
    workspace_id = None
    if len(sys.argv) > 1:
        workspace_id = sys.argv[1]
    if not workspace_id:
        workspace_id = os.getenv("BACKFILL_WORKSPACE_ID")
    if not workspace_id:
        workspace_id = settings.GDRIVE_WORKSPACE_ID
    if not workspace_id:
        print("Error: workspace_id not provided. Pass as argv[1] or set BACKFILL_WORKSPACE_ID or GDRIVE_WORKSPACE_ID.", file=sys.stderr)
        sys.exit(2)

    dry_run = os.getenv("DRY_RUN", "0").strip() not in ("", "0", "false", "False")
    page_size_str = os.getenv("PAGE_SIZE", "500").strip()
    try:
        page_size = max(50, min(2000, int(page_size_str)))
    except Exception:
        page_size = 500

    print(f"Starting backfill for workspace {workspace_id} (page_size={page_size}, dry_run={dry_run})")
    result = backfill_metadata_for_workspace(workspace_id, page_size=page_size, dry_run=dry_run)
    print(f"Done. Examined={result['examined']} Updated={result['updated']} Failed={result['failed']}")


if __name__ == "__main__":
    main()
