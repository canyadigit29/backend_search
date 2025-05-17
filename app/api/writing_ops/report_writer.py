import os
import uuid
from datetime import datetime
from typing import List, Optional
import pandas as pd
from slugify import slugify

from app.core.supabase_client import supabase
from app.api.writing_ops.pedro_description_generator import generate_descriptions
from app.api.writing_ops.pedro_code_generator import CodeGenerator
from app.api.writing_ops.pedro_pdf_writer import generate_pdf
from app.api.writing_ops.pedro_docx_writer import generate_docx

HARDCODED_USER_ID = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9"
BUCKET_NAME = "maxgptstorage"


def generate_report_from_chunks(
    chunks: List[dict],
    user_prompt: str,
    tone: Optional[str] = "formal"
) -> dict:
    """
    Generates a professional PDF and DOCX report from semantic document chunks.
    Returns a dict containing public download URLs.
    """
    # 1. Create DataFrame from chunks
    df = pd.DataFrame([{
        "file_name": chunk.get("file_name", "unknown"),
        "chunk_index": chunk.get("chunk_index", 0),
        "content": chunk.get("content", "")
    } for chunk in chunks])

    # 2. Derive topic from prompt or fallback
    topic = generate_title_from_prompt(user_prompt)
    slug = slugify(topic)
    today = datetime.utcnow().strftime("%Y-%m-%d")

    base_filename = f"{slug}_{today}"
    pdf_path = f"/mnt/data/reports/{base_filename}.pdf"
    docx_path = f"/mnt/data/reports/{base_filename}.docx"
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    # 3. Generate report descriptions (executive summary + section summaries)
    descriptions = generate_descriptions(df, tone=tone)

    # 4. Generate visualizations
    codegen = CodeGenerator()
    chart_paths = codegen.generate(df)

    # 5. Generate both PDF and DOCX reports
    generate_pdf(descriptions, chart_paths, pdf_path)
    generate_docx(descriptions, chart_paths, docx_path)

    # 6. Upload to Supabase and return public URLs
    pdf_url = upload_to_supabase(pdf_path, base_filename + ".pdf")
    docx_url = upload_to_supabase(docx_path, base_filename + ".docx")

    return {"pdf_url": pdf_url, "docx_url": docx_url}


def upload_to_supabase(local_path: str, filename: str) -> str:
    with open(local_path, "rb") as f:
        storage_path = f"{HARDCODED_USER_ID}/downloads/{filename}"
        supabase.storage.from_(BUCKET_NAME).upload(storage_path, f, file_options={"content-type": "application/pdf"})
        public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(storage_path)
        return public_url


def generate_title_from_prompt(prompt: str) -> str:
    if not prompt:
        return "report"
    prompt = prompt.strip().lower()
    if len(prompt) > 100:
        prompt = prompt[:100]
    prompt = prompt.replace("generate a report on", "")
    prompt = prompt.replace("report", "")
    prompt = prompt.strip()
    return prompt or "report"
