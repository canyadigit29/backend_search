from datetime import datetime
import io
import os
from pdfdocument.document import PDFDocument
from app.core.supabase_client import supabase

def generate_pdf_report(title: str, content: str, user_id: str = "2532a036-5988-4e0b-8c0e-b0e94aabc1c9") -> str:
    """
    Generates a PDF in-memory and uploads it to Supabase storage.

    Args:
        title (str): Report title.
        content (str): Body content of the report.
        user_id (str): Supabase user ID (used for folder path).

    Returns:
        str: Public URL of the uploaded report.
    """
    # Create in-memory PDF
    buffer = io.BytesIO()
    pdf = PDFDocument(buffer)
    pdf.init_report()
    pdf.h1(title)
    pdf.p(content)
    pdf.generate()

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{title.replace(' ', '_')}_{timestamp}.pdf"
    upload_path = f"{user_id}/Uploads/{filename}"

    # Upload to Supabase
    buffer.seek(0)
    upload = supabase.storage.from_("maxgptstorage").upload(upload_path, buffer, file_options={"content-type": "application/pdf", "upsert": True})

    # Generate public URL
    public_url = supabase.storage.from_("maxgptstorage").get_public_url(upload_path)

    return public_url
