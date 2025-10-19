import os
from fastapi.responses import JSONResponse
from .supabase_client import create_client  # assuming this exists

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
SUPABASE_BUCKET_ID = "files"  # Hardcoded bucket name for consistency
SUPABASE_FOLDER = "4a867500-7423-4eaa-bc79-94e368555e05"  # Hardcoded folder name
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

def search_docs(summary, summary_chunks):
    """
    Given a summary and its corresponding summary_chunks (list of dicts with file info),
    generate signed URLs for each file source and return an excerpt.
    """
    # --- Generate signed URLs for each source ---
    excerpt_length = 300
    sources = []

    # Iterate over the same 'summary_chunks' list to build the sources.
    for c in summary_chunks:
        file_name = c.get("file_name")
        signed_url = None
        if file_name:
            try:
                # Always prepend the hardcoded folder
                file_path = f"{SUPABASE_FOLDER}/{file_name}"
                # Create a temporary, secure download link valid for 5 minutes.
                res = supabase.storage.from_(SUPABASE_BUCKET_ID).create_signed_url(file_path, 300)
                signed_url = res.get('signedURL')
            except Exception:
                signed_url = None  # Fail gracefully if URL generation fails

        content = c.get("content") or ""
        excerpt = content.strip().replace("\n", " ")[:excerpt_length]
        sources.append({
            "id": c.get("id"),
            "file_name": file_name,
            "page_number": c.get("page_number"),
            "score": c.get("score"),
            "excerpt": excerpt,
            "url": signed_url  # Add the new URL field
        })

    return JSONResponse({"summary": summary, "sources": sources})
