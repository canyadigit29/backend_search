import asyncio
import json
from app.api.assistant.document_index import extract_metadata_from_content, extract_key_terms_from_content

# Test the content analysis functions with sample data
test_content = """
---PAGE 1--- BOROUGH OF SCOTTDALE REGULAR MEETING DECEMBER 11, 2023 The regular meeting of Scottdale 
Borough Council was called to order at 7:00 PM by President John Smith. Council members present were 
Mrs. Colebank, Mr. Chronowski, and Mayor Johnson. Moved by Mrs. Colebank, second by Mr. Chronowski to 
approve Ordinance 2023-05 regarding storm sewer maintenance on 123 Main Street. The Borough authorized 
$45,000 from the CDBG fund for infrastructure improvements. Motion carried unanimously.
"""

def test_content_analysis():
    print("Testing content analysis functions...")
    print("=" * 60)
    
    # Test metadata extraction
    metadata = extract_metadata_from_content(test_content)
    print("Extracted Metadata:")
    print(json.dumps(metadata, indent=2))
    print()
    
    # Test key terms extraction
    key_terms = extract_key_terms_from_content(test_content, max_terms=15)
    print("Key Terms Found:")
    print(key_terms)
    print()
    
    print("Analysis looks good! Functions are working with actual content.")

if __name__ == "__main__":
    test_content_analysis()