
import pdfplumber

def extract_text(path):
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            print(f"📜 Extracted {len(text)} characters using pdfplumber.")
            return text
    except Exception as e:
        print(f"🛑 pdfplumber failed: {e}")
        return ""
