import pdfplumber
import fitz  # PyMuPDF
import re

import pdfplumber
import fitz  # PyMuPDF
import re

def clean_text(text):
    # Remove page numbers like "Page 1", "1 of 10", etc.
    text = re.sub(r'Page \\d+|\\d+ of \\d+', '', text)
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove lines with only dashes or underscores
    text = re.sub(r'^[-_]+$', '', text, flags=re.MULTILINE)
    return text.strip()

def extract_text(path):
    # Try pdfplumber first, with page markers
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(
                f"---PAGE {i+1}---\n" + (page.extract_text() or "")
                for i, page in enumerate(pdf.pages)
            )
            if len(text.strip()) > 100:
                print(f"📜 Extracted {len(text)} characters using pdfplumber.")
                return clean_text(text)
    except Exception as e:
        print(f"🛑 pdfplumber failed: {e}")

    # Fallback: PyMuPDF
    try:
        doc = fitz.open(path)
        text = "\n".join(
            f"---PAGE {i+1}---\n" + page.get_text()
            for i, page in enumerate(doc)
        )
        if len(text.strip()) > 100:
            print(f"📜 Extracted {len(text)} characters using PyMuPDF.")
            return clean_text(text)
    except Exception as e:
        print(f"🛑 PyMuPDF failed: {e}")

    # Optionally: fallback to OCR here
    print(f"🛑 All extractors failed for {path}")
    return ""
