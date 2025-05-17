from pdfdocument.document import PDFDocument
from datetime import datetime
import os

def generate_pdf_report(title: str, content: str, output_dir: str = "reports") -> str:
    """
    Generates a simple PDF report using the pdfdocument library.

    Args:
        title (str): Title of the report.
        content (str): Body text of the report.
        output_dir (str): Directory where the PDF will be saved.

    Returns:
        str: Full path to the generated PDF file.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{title.replace(' ', '_')}_{timestamp}.pdf"
    filepath = os.path.join(output_dir, filename)

    pdf = PDFDocument(filepath)
    pdf.init_report()
    pdf.h1(title)
    pdf.p(content)
    pdf.generate()

    return filepath
