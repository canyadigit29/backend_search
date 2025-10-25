import asyncio
import json
import os
from app.core.supabase_client import create_client
from app.api.assistant.document_index import extract_metadata_from_content, extract_key_terms_from_content

async def test_real_data_analysis():
    """Test content analysis with real data from the database"""
    
    # Get Supabase connection
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_SERVICE_ROLE = os.environ.get('SUPABASE_SERVICE_ROLE')

    if not SUPABASE_URL:
        print('❌ SUPABASE_URL environment variable not set')
        return
        
    print('Testing with real database content...')
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)
        
        # Get a few chunks with content to analyze
        result = supabase.table('document_chunks').select('content,document_type,meeting_date').limit(10).execute()
        
        print(f'Analyzing {len(result.data)} real chunks:')
        print('=' * 60)
        
        all_metadata = []
        all_terms = []
        
        for i, chunk in enumerate(result.data):
            content = chunk.get('content', '')
            if not content:
                continue
                
            print(f"\nChunk {i+1} ({chunk.get('document_type')}, {chunk.get('meeting_date')}):")
            print(f"Content preview: {content[:100]}...")
            
            # Extract metadata
            metadata = extract_metadata_from_content(content)
            if metadata:
                print(f"Metadata extracted: {json.dumps(metadata, indent=2)}")
                all_metadata.append(metadata)
            else:
                print("No metadata patterns found")
                
            # Extract key terms
            terms = extract_key_terms_from_content(content, max_terms=10)
            print(f"Key terms: {terms[:5]}...")  # Show first 5
            all_terms.extend(terms)
            
            print("-" * 40)
        
        print(f"\nSummary:")
        print(f"- Total metadata items extracted: {len(all_metadata)}")
        print(f"- Total key terms found: {len(all_terms)}")
        print(f"- Unique terms: {len(set(all_terms))}")
        
        # Show most common terms
        from collections import Counter
        term_counts = Counter(all_terms)
        print(f"- Most common terms: {term_counts.most_common(10)}")
        
    except Exception as e:
        print(f'❌ Error: {e}')

if __name__ == "__main__":
    asyncio.run(test_real_data_analysis())