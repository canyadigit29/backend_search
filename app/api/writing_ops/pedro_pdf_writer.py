from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from reportlab.lib import colors
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, 
    Paragraph, 
    Spacer, 
    Image, 
    PageBreak
)
import json
from dataclasses import dataclass

from core.config.paths import path_config
from core.config.constants import PDF_CONSTANTS
from core.logging.logger import get_logger, log_execution
from domain.exceptions.custom import PDFGenerationError
from .pdf_styles import get_custom_styles

logger = get_logger(__name__)

@dataclass
class TOCEntry:
    """Table of Contents entry with proper page tracking"""
    title: str
    level: int
    page_number: Optional[int] = None
    parent_id: Optional[str] = None

class DynamicTOC:
    """Dynamic Table of Contents handler with improved page tracking"""
    def __init__(self):
        self.entries = []
        self._current_page = 1
        self._last_section_id = None
        self._toc_pages = 0  # Track TOC pages for offset

    def add_entry(self, title: str, level: int) -> None:
        """Add a new entry to the TOC with proper page number"""
        # Clean the title of any special characters and formatting
        clean_title = re.sub(r'[^\w\s-]', '', title)
        
        entry = TOCEntry(
            title=title,
            level=level,
            page_number=self._current_page + self._toc_pages,
            parent_id=self._last_section_id if level > 1 else None
        )
        
        if level == 1:
            self._last_section_id = clean_title
            
        self.entries.append(entry)

    def increment_page(self, count: int = 1) -> None:
        """Increment the current page count"""
        self._current_page += count

    def set_toc_pages(self, pages: int) -> None:
        """Set the number of pages taken by TOC for offset calculation"""
        self._toc_pages = pages
        # Update all existing entries to account for TOC pages
        for entry in self.entries:
            if entry.page_number is not None:
                entry.page_number += self._toc_pages

    def create_toc_content(self, styles) -> List:
        """Create table of contents with proper spacing and formatting"""
        elements = []
        
        # Add TOC title
        elements.append(Paragraph("Table of Contents", styles['CustomChapterTitle']))
        elements.append(Spacer(1, 0.2*inch))
        
        for entry in self.entries:
            # Skip the TOC entry itself
            if entry.title == "Table of Contents":
                continue
                
            # Calculate indentation based on level
            indent = "  " * (entry.level - 1)
            title = f"{indent}{entry.title}"
            
            # Calculate dots with proper spacing
            title_length = len(title)
            dots_count = max(3, 70 - title_length)  # Ensure minimum 3 dots
            dots = "." * dots_count
            
            # Create TOC line with proper spacing and page number
            if entry.level == 1:
                toc_line = f"{title}{dots}{entry.page_number}"
                style = styles['CustomTOCEntry']
            elif entry.level == 2:
                toc_line = f"{title}{dots}{entry.page_number}"
                style = styles['CustomTOCEntry2']
            else:
                toc_line = f"{title}{dots}{entry.page_number}"
                style = styles['CustomTOCEntry3']
            
            elements.append(Paragraph(toc_line, style))
        
        elements.append(PageBreak())
        return elements

    def reset(self) -> None:
        """Reset the TOC for reprocessing"""
        self._current_page = 1
        self._toc_pages = 0
        self.entries = []


class PDFGenerator:
    """PDF Report Generator with Reliable Image Handling"""
    
    def __init__(self):
        self.styles = get_custom_styles()
        self.toc = DynamicTOC()
        self.figures_list = []
        self.report_title = "Data Analysis Report"

    def _validate_graph_path(self, graph_path: str) -> bool:
        """Validate that a graph file exists and is readable"""
        try:
            path = Path(graph_path)
            return path.exists() and path.is_file() and path.stat().st_size > 0
        except Exception as e:
            logger.error(f"Error validating graph path {graph_path}: {str(e)}")
            return False    
    
    def create_header_footer(self, canvas, doc):
        """Create header and footer on each page and update TOC page numbers"""
        canvas.saveState()
        
        # Header
        header_text = self.report_title
        canvas.setFont('Helvetica', 9)
        canvas.setFillColor(colors.HexColor('#1F497D'))
        canvas.drawString(PDF_CONSTANTS['MARGIN'], 
                         A4[1] - 40, 
                         header_text)
        canvas.line(PDF_CONSTANTS['MARGIN'], 
                   A4[1] - 45, 
                   A4[0] - PDF_CONSTANTS['MARGIN'], 
                   A4[1] - 45)
        
        # Footer with page number
        footer_text = f"Page {doc.page}"
        canvas.drawString(A4[0]/2 - 20, 30, footer_text)
        canvas.line(PDF_CONSTANTS['MARGIN'], 
                   50, 
                   A4[0] - PDF_CONSTANTS['MARGIN'], 
                   50)
                   
        # Update TOC entries with actual page numbers
        if hasattr(self, 'toc') and hasattr(self, '_processing_section'):
            current_section = self._processing_section
            for entry in self.toc.entries:
                if entry.title == current_section:
                    entry.page_number = doc.page
                    
        canvas.restoreState()

    def create_cover_page(self) -> List:
        """Create the report cover page"""
        elements = []
        elements.append(Spacer(1, 2*inch))
        
        self.toc.add_entry("Cover", 1)
        
        elements.append(Paragraph(self.report_title, 
                                self.styles['CustomMainTitle']))
        elements.append(Spacer(1, inch))
        
        date_str = datetime.now().strftime("%B %d, %Y")
        elements.append(Paragraph(date_str, 
                                self.styles['CustomHeader']))
        elements.append(Spacer(1, 2*inch))
        elements.append(PageBreak())
        
        self.toc.increment_page()
        return elements

    def create_executive_summary(self, analysis_data: List[Dict]) -> List:
        """Create executive summary section"""
        elements = []
        
        self.toc.add_entry("Executive Summary", 1)
        elements.append(Paragraph("Executive Summary", 
                            self.styles['CustomChapterTitle']))
        
        # Overview Section
        self.toc.add_entry("Overview", 2)
        elements.append(Paragraph("Overview", 
                            self.styles['CustomSectionTitle']))
        
        overview = """This report presents a comprehensive analysis of the provided data, 
        highlighting key patterns, trends, and actionable insights derived from the analysis."""
        elements.append(Paragraph(overview, 
                                self.styles['CustomBodyText']))
        
        # Key Findings Section
        self.toc.add_entry("Key Findings", 2)
        elements.append(Paragraph("Key Findings", 
                            self.styles['CustomSectionTitle']))
        
        for data in analysis_data:
            content = data.get('content', {})
            for section in content.get('sections', []):
                if section.get('heading') == 'Analysis Overview':
                    elements.append(Paragraph(
                        f"• {section.get('content', '')}",
                        self.styles['CustomBodyText']
                    ))
        
        # Key Conclusions Section
        self.toc.add_entry("Key Conclusions", 2)
        elements.append(Paragraph("Key Conclusions", 
                            self.styles['CustomSectionTitle']))
        
        for data in analysis_data:
            content = data.get('content', {})
            for section in content.get('sections', []):
                if section.get('heading') == 'Conclusions and Recommendations':
                    for conclusion in section.get('key_conclusions', []):
                        elements.append(Paragraph(
                            f"• Finding: {conclusion.get('finding', '')}",
                            self.styles['CustomDataPoint']
                        ))
                        elements.append(Paragraph(
                            f"  Impact: {conclusion.get('impact', '')}",
                            self.styles['CustomBodyText']
                        ))
                        elements.append(Paragraph(
                            f"  Recommendation: {conclusion.get('recommendation', '')}",
                            self.styles['CustomBodyText']
                        ))
        
        elements.append(PageBreak())
        self.toc.increment_page()
        return elements

    def create_table_of_contents(self) -> List:
        """Create table of contents with all sections and subsections"""
        elements = []
        elements.append(Paragraph("Table of Contents", 
                                self.styles['CustomChapterTitle']))
        elements.append(Spacer(1, 0.2*inch))
        
        for entry in self.toc.get_entries():
            # Calculate indentation based on level
            indent = "  " * (entry.level - 1)
            title = f"{indent}{entry.title}"
            
            # Calculate dots based on level-specific spacing
            available_space = 60 - len(title)  # Adjust total width as needed
            dots = "." * max(available_space, 3)
            
            # Create TOC line with appropriate style based on level
            toc_line = f"{title}{dots}{entry.page_number}"
            
            # Select style based on level
            if entry.level == 1:
                style = self.styles['CustomTOCEntry']
            elif entry.level == 2:
                style = self.styles['CustomTOCEntry2']
            else:
                style = self.styles['CustomTOCEntry3']
            
            elements.append(Paragraph(toc_line, style))
        
        elements.append(PageBreak())
        self.toc.increment_page()
        return elements

    def create_conclusions(self, analysis_data: List[Dict]) -> List:
        """Create conclusions and next steps section"""
        elements = []
        
        self.toc.add_entry("Limitations & Next Steps", 1)
        elements.append(Paragraph("Limitations & Next Steps", 
                            self.styles['CustomChapterTitle']))
        
        # Collect unique limitations and next steps
        limitations = set()
        next_steps = set()
        
        for data in analysis_data:
            content = data.get('content', {})
            for section in content.get('sections', []):
                if section.get('heading') == 'Conclusions and Recommendations':
                    limitations.update(section.get('limitations', []))
                    next_steps.update(section.get('next_steps', []))
        
        # Add Limitations
        if limitations:
            self.toc.add_entry("Limitations", 2)
            elements.append(Paragraph("Limitations", 
                                    self.styles['CustomSectionTitle']))
            for limitation in limitations:
                elements.append(Paragraph(
                    f"• {limitation}",
                    self.styles['CustomBodyText']
                ))
            elements.append(Spacer(1, 0.1*inch))
        
        # Add Next Steps
        if next_steps:
            self.toc.add_entry("Next Steps", 2)
            elements.append(Paragraph("Next Steps", 
                                    self.styles['CustomSectionTitle']))
            for step in next_steps:
                elements.append(Paragraph(
                    f"• {step}",
                    self.styles['CustomBodyText']
                ))
        
        elements.append(PageBreak())
        return elements

    def _format_analysis_section(self, section: Dict) -> List:
        """Format a single analysis section"""
        elements = []
        
        # Add content
        if content := section.get('content'):
            elements.append(Paragraph(content, 
                                    self.styles['CustomBodyText']))
        
        # Add data points
        if data_points := section.get('data_points'):
            for point in data_points:
                point_text = f"• {point.get('metric')}: {point.get('value')} ({point.get('significance')})"
                elements.append(Paragraph(point_text, 
                                        self.styles['CustomDataPoint']))
        
        # Add calculations
        if calculations := section.get('calculations'):
            elements.append(Spacer(1, 0.1*inch))
            for calc in calculations:
                name = calc.get('name', '')
                value = calc.get('value', '')
                if name and value:
                    elements.append(Paragraph(
                        f"• {name}: {value}",
                        self.styles['CustomCalculation']
                    ))
                    if interpretation := calc.get('interpretation'):
                        elements.append(Paragraph(
                            f"  {interpretation}", 
                            self.styles['CustomBodyText']
                        ))
        
        # Add key conclusions if present
        if key_conclusions := section.get('key_conclusions'):
            for conclusion in key_conclusions:
                elements.append(Paragraph(
                    f"• Finding: {conclusion.get('finding', '')}",
                    self.styles['CustomKeyFinding']
                ))
                if impact := conclusion.get('impact'):
                    elements.append(Paragraph(
                        f"  Impact: {impact}",
                        self.styles['CustomBodyText']
                    ))
                if recommendation := conclusion.get('recommendation'):
                    elements.append(Paragraph(
                        f"  Recommendation: {recommendation}",
                        self.styles['CustomBodyText']
                    ))
        
        elements.append(Spacer(1, 0.2*inch))
        return elements

    def create_analysis_chapters(self, analysis_data: List[Dict]) -> List:
        """Create analysis chapters with proper image handling"""
        elements = []
        
        for i, data in enumerate(analysis_data, 1):
            try:
                content = data.get('content', {})
                title = content.get('sections', [{}])[0].get('title', 
                        content.get('question', f'Analysis {i}'))
                
                chapter_title = f"{i}. {self._format_title(title)}"
                self.toc.add_entry(chapter_title, 1)
                
                # Add chapter title
                elements.append(Paragraph(chapter_title, 
                                        self.styles['CustomChapterTitle']))
                
                # Handle visualization with proper validation
                graph_path = data.get('graph_path')
                if graph_path and self._validate_graph_path(graph_path):
                    try:
                        img = Image(graph_path, 
                                width=PDF_CONSTANTS['MAX_IMAGE_WIDTH'],
                                height=0.75*PDF_CONSTANTS['MAX_IMAGE_WIDTH'])
                        elements.append(img)
                        
                        figure_title = f"Figure {i}: {self._format_title(title)}"
                        elements.append(Paragraph(figure_title, 
                                            self.styles['CustomCaption']))
                        self.figures_list.append({
                            'title': figure_title,
                            'page': self.toc.current_page
                        })
                    except Exception as e:
                        logger.error(f"Failed to add image for chapter {i}: {str(e)}")
                else:
                    logger.warning(f"No valid graph found for chapter {i}")
                
                # Add sections with proper headings
                if sections := content.get('sections', []):
                    for section in sections:
                        if heading := section.get('heading'):
                            elements.append(Paragraph(
                                heading,
                                self.styles['CustomSectionTitle']
                            ))
                            self.toc.add_entry(heading, 2)
                        
                        elements.extend(self._format_analysis_section(section))
                
                elements.append(PageBreak())
                self.toc.increment_page()
                
            except Exception as e:
                logger.error(f"Error processing chapter {i}: {str(e)}")
                continue
        
        return elements
    

    def _format_title(self, text: str) -> str:
        """Format title text"""
        if not text:
            return "Untitled Analysis"
        return ' '.join(word.capitalize() 
                       for word in text.replace('_', ' ').split())

    def _load_analysis_data(self) -> List[Dict]:
        """Load and validate analysis data from JSON files"""
        analysis_data = []
        # Update to look for .json files without _analysis suffix
        json_files = sorted(path_config.DESCRIPTION_DIR.glob('*.json'))
        
        for json_file in json_files:
            try:
                with open(json_file, 'r') as file:
                    analysis = json.load(file)
                
                # Construct graph path - no need to replace _analysis anymore
                graph_path = path_config.GRAPHS_DIR / f"{json_file.stem}.png"
                
                if not self._validate_graph_path(str(graph_path)):
                    logger.warning(f"Graph file missing or invalid for {json_file.name}")
                    continue
                    
                analysis_data.append({
                    "content": analysis,
                    "graph_path": str(graph_path)
                })
                
            except Exception as e:
                logger.error(f"Error loading analysis file {json_file}: {str(e)}")
                
        # Sort analysis data by question number if available
        try:
            analysis_data.sort(key=lambda x: int(x["content"].get("question", "").split()[0]))
        except:
            logger.warning("Could not sort analysis data by question number")
            
        return analysis_data

    @log_execution
    def generate_pdf(self, report_title: str = "Data Analysis Report") -> str:
        """Generate the complete PDF report with correct TOC"""
        try:
            self.report_title = report_title
            self.toc = DynamicTOC()
            
            # Generate output filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = path_config.OUTPUT_DIR / f"analysis_report_{timestamp}.pdf"
            
            # Initialize document
            doc = SimpleDocTemplate(
                str(output_path),
                pagesize=A4,
                rightMargin=PDF_CONSTANTS['MARGIN'],
                leftMargin=PDF_CONSTANTS['MARGIN'],
                topMargin=PDF_CONSTANTS['MARGIN'],
                bottomMargin=PDF_CONSTANTS['MARGIN']
            )
            
            # First pass: Generate content and collect TOC entries
            content = []
            content.extend(self.create_cover_page())  # Page 1
            
            # Load and validate data
            analysis_data = self._load_analysis_data()
            if not analysis_data:
                raise PDFGenerationError("No valid analysis data found")
            
            # Create placeholders for TOC
            toc_start = len(content)
            content.extend([PageBreak()])  # TOC placeholder
            
            # Add remaining content
            content.extend(self.create_executive_summary(analysis_data))
            content.extend(self.create_analysis_chapters(analysis_data))
            content.extend(self.create_conclusions(analysis_data))
            
            # Calculate TOC pages (approximate)
            toc_entries = len(self.toc.entries)
            estimated_toc_pages = (toc_entries * 20 + 100) // 700 + 1  # Rough estimate
            self.toc.set_toc_pages(estimated_toc_pages)
            
            # Create actual TOC
            toc_content = self.toc.create_toc_content(self.styles)
            
            # Insert TOC at placeholder position
            content[toc_start:toc_start+1] = toc_content
            
            # Build PDF
            try:
                doc.build(
                    content,
                    onFirstPage=self.create_header_footer,
                    onLaterPages=self.create_header_footer
                )
            except Exception as e:
                raise PDFGenerationError(f"PDF build failed: {str(e)}")
            
            logger.info(f"Generated PDF successfully: {output_path}")
            return str(output_path)
            
        except Exception as e:
            logger.error(f"Failed to generate PDF: {str(e)}")
            raise PDFGenerationError(str(e))

@log_execution
def generate_pdf(report_title: str = "Data Analysis Report") -> str:
    """Main function to generate PDF report"""
    try:
        generator = PDFGenerator()
        return generator.generate_pdf(report_title=report_title)
    except Exception as e:
        logger.error(f"PDF generation failed: {str(e)}")
        raise PDFGenerationError(str(e))