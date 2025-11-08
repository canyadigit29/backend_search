"""
Helpers to support mapping Google Drive subfolders (or labels) to multiple OpenAI Vector Stores per workspace.

This module is intentionally self-contained and safe to import from workers.
It introduces no runtime dependency on a specific table; if the mapping table
doesn't exist yet, it will gracefully fall back to the default Vector Store.

Proposed schema (Supabase): workspace_vector_store_buckets
  - workspace_id uuid references workspaces(id)
  - label text not null            # e.g., 'agendas', 'minutes', 'transcripts'
  - vector_store_id text not null  # OpenAI VS id (vs_...)
  - drive_folder_id text           # optional Google Drive folder id
  - created_at timestamptz default now()
  - updated_at timestamptz

Resolution order:
  1) If a drive_folder_id is provided, match by that.
  2) Else, if a label is provided, match by label.
  3) Else, or not found -> fall back to default store for the workspace.
"""

from typing import Optional

from fastapi import HTTPException

from app.core.supabase_client import supabase
from app.core.config import settings


def _default_vector_store_for_workspace(workspace_id: Optional[str]) -> str:
    # Explicit override
    if settings.GDRIVE_VECTOR_STORE_ID:
        return settings.GDRIVE_VECTOR_STORE_ID

    # Fallback to workspace mapping
    if workspace_id:
        try:
            res = (
                supabase.table("workspace_vector_stores")
                .select("vector_store_id")
                .eq("workspace_id", workspace_id)
                .maybe_single()
                .execute()
            )
            row = getattr(res, "data", None) or {}
            vsid = row.get("vector_store_id")
            if vsid:
                return vsid
        except Exception:
            pass
    raise HTTPException(status_code=404, detail="No vector store mapping for workspace")


def resolve_vector_store_for(workspace_id: Optional[str], *, drive_folder_id: Optional[str] = None, label: Optional[str] = None) -> str:
    """
    Resolve the appropriate vector_store_id for a file based on its Drive folder or a logical label.
    Falls back to default store if no mapping is found or the mapping table is not present.
    """
    # If neither folder nor label provided, use default
    if not drive_folder_id and not label:
        return _default_vector_store_for_workspace(workspace_id)

    # Try the mapping table, tolerate absence
    try:
        sel = "workspace_id,label,vector_store_id,drive_folder_id"
        q = supabase.table("workspace_vector_store_buckets").select(sel).eq("workspace_id", workspace_id)
        if drive_folder_id:
            q = q.eq("drive_folder_id", drive_folder_id)
        elif label:
            q = q.eq("label", label)
        res = q.maybe_single().execute()
        row = getattr(res, "data", None)
        if row and row.get("vector_store_id"):
            return row["vector_store_id"]
    except Exception:
        # Table may not exist yet; ignore and fall back
        pass

    return _default_vector_store_for_workspace(workspace_id)


def resolve_multiple_stores(workspace_id: str, labels: list[str] | None = None) -> list[str]:
    """Return a list of vector_store_ids for a workspace given logical labels.
    If labels is None/empty, returns the default store only. Missing labels are ignored.
    """
    if not labels:
        return [
            _default_vector_store_for_workspace(workspace_id)
        ]
    vs_ids: list[str] = []
    seen: set[str] = set()
    for lab in labels:
        try:
            vid = resolve_vector_store_for(workspace_id, label=lab)
            if vid and vid not in seen:
                vs_ids.append(vid)
                seen.add(vid)
        except Exception:
            continue
    if not vs_ids:
        vs_ids.append(_default_vector_store_for_workspace(workspace_id))
    return vs_ids
