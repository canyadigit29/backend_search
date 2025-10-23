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
    # ... (keep existing PDF extraction logic, but raise TextExtractionError on failure)
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(
                f"---PAGE {i+1}---\n" + (page.extract_text() or "")
                for i, page in enumerate(pdf.pages)
            )
            if len(text.strip()) > 100:
                return clean_text(text)
    except Exception as e:
        print(f"🛑 pdfplumber failed: {e}")

    try:
        doc = fitz.open(path)
        text = "\n".join(
            f"---PAGE {i+1}---\n" + page.get_text()
            for i, page in enumerate(doc)
        )
        if len(text.strip()) > 100:
            return clean_text(text)
    except Exception as e:
        print(f"🛑 PyMuPDF failed: {e}")

    try:
        images = convert_from_path(path)
        ocr_text = ""
        for i, image in enumerate(images):
            ocr_text += f"---PAGE {i+1}---\n" + pytesseract.image_to_string(image) + "\n"
        if len(ocr_text.strip()) > 100:
            return clean_text(ocr_text)
    except Exception as e:
        print(f"� OCR failed: {e}")

    raise TextExtractionError("PDF text extraction failed. The document may be scanned or corrupted. Please OCR it manually.")

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
