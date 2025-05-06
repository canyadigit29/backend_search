import os
import fitz  # PyMuPDF
from docx import Document

def extract_text(supabase_path: str, local_path: str) -> str:
    _, ext = os.path.splitext(local_path.lower())

    if ext == ".txt":
        with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    elif ext == ".pdf":
        text = ""
        try:
            doc = fitz.open(local_path)
            for page in doc:
                text += page.get_text()
            return text
        except Exception as e:
            raise RuntimeError(f"PDF extraction failed: {e}")

    elif ext in [".doc", ".docx"]:
        try:
            doc = Document(local_path)
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise RuntimeError(f"DOCX extraction failed: {e}")

    else:
        raise ValueError(f"Unsupported file type: {ext}")
