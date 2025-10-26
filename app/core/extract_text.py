import os
import re
import fitz  # PyMuPDF
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from docx import Document

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
    # Try to extract text with pdfplumber
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(
                f"---PAGE {i+1}---\n" + (page.extract_text() or "")
                for i, page in enumerate(pdf.pages)
            )
            # New, more robust check:
            # Remove all page markers and whitespace to see if any real text remains.
            text_content_only = re.sub(r'---PAGE \d+---', '', text).strip()
            if len(text_content_only) > 100: # Check if there's substantial content
                return clean_text(text)
    except Exception as e:
        print(f"pdfplumber failed: {e}")

    # If pdfplumber fails, try with PyMuPDF
    try:
        doc = fitz.open(path)
        text = "\n".join(
            f"---PAGE {i+1}---\n" + page.get_text()
            for i, page in enumerate(doc)
        )
        # Apply the same robust check here
        text_content_only = re.sub(r'---PAGE \d+---', '', text).strip()
        if len(text_content_only) > 100:
            return clean_text(text)
    except Exception as e:
        print(f"PyMuPDF failed: {e}")

    # If both methods fail, return None to indicate OCR is needed
    return None

def extract_text_from_docx(path):
    try:
        doc = Document(path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return clean_text(text)
    except Exception as e:
        raise TextExtractionError(f"Failed to extract text from DOCX: {e}")

def extract_text_from_txt(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
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
        raise TextExtractionError(f"Unsupported file type: {file_extension}")
