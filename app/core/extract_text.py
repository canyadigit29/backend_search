import os
import re

# Optional dependencies: degrade gracefully if not installed in test/light environments.
try:  # PyMuPDF
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - absence handled dynamically
    fitz = None

try:
    import pdfplumber  # type: ignore
except ImportError:  # pragma: no cover
    pdfplumber = None

try:
    import pytesseract  # type: ignore
except ImportError:  # pragma: no cover
    pytesseract = None

try:
    from pdf2image import convert_from_path  # type: ignore
except ImportError:  # pragma: no cover
    convert_from_path = None

try:
    from docx import Document  # type: ignore
except ImportError:  # pragma: no cover
    Document = None

class TextExtractionError(Exception):
    """Custom exception for text extraction failures."""
    pass

def clean_text(text):
    # ... (keep existing clean_text function)
    text = re.sub(r'Page \\d+|\\d+ of \\d+', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'^[-_]+$', '', text, flags=re.MULTILINE)
    return text.strip()

def extract_text_from_pdf(path):
    # Try pdfplumber if available
    if pdfplumber is not None:
        try:
            with pdfplumber.open(path) as pdf:
                text = "\n".join(
                    f"---PAGE {i+1}---\n" + (page.extract_text() or "")
                    for i, page in enumerate(pdf.pages)
                )
                text_content_only = re.sub(r'---PAGE \d+---', '', text).strip()
                if len(text_content_only) > 100:
                    return clean_text(text)
        except Exception as e:  # pragma: no cover
            print(f"pdfplumber failed: {e}")

    # Fallback to PyMuPDF if available
    if fitz is not None:
        try:
            doc = fitz.open(path)
            text = "\n".join(
                f"---PAGE {i+1}---\n" + page.get_text()
                for i, page in enumerate(doc)
            )
            text_content_only = re.sub(r'---PAGE \d+---', '', text).strip()
            if len(text_content_only) > 100:
                return clean_text(text)
        except Exception as e:  # pragma: no cover
            print(f"PyMuPDF failed: {e}")

    # Indicate OCR or other fallback needed
    return None

def extract_text_from_docx(path):
    if Document is None:
        raise TextExtractionError("python-docx not installed")
    try:
        doc = Document(path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return clean_text(text)
    except Exception as e:
        raise TextExtractionError(f"Failed to extract text from DOCX: {e}")

def extract_text_from_txt(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return clean_text(f.read())
    except Exception as e:
        raise TextExtractionError(f"Failed to read text file: {e}")

def extract_text(path):
    file_extension = os.path.splitext(path)[1].lower()

    if file_extension == '.pdf':
        return extract_text_from_pdf(path)
    elif file_extension == '.docx':
        return extract_text_from_docx(path)
    elif file_extension == '.txt':
        return extract_text_from_txt(path)
    else:
        # For any other file type, attempt to read as plain text.
        # This will handle .md, .py, .html, etc.
        return extract_text_from_txt(path)
