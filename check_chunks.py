import os
from app.core.supabase_client import create_client

def check_document_chunks():
    # Get Supabase connection
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_SERVICE_ROLE = os.environ.get('SUPABASE_SERVICE_ROLE')

    if not SUPABASE_URL:
        print('❌ SUPABASE_URL environment variable not set')
        return
        
    print('✅ Environment variables found')
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)
        
        # Query sample rows with all columns to see structure
        result = supabase.table('document_chunks').select('*').limit(5).execute()
        
        print(f'Found {len(result.data)} sample rows:')
        print('=' * 80)
        
        for i, row in enumerate(result.data):
            print(f'Row {i+1}:')
            print(f'  ID: {row.get("id", "N/A")}')
            print(f'  File ID: {row.get("file_id", "N/A")}')
            print(f'  Document Type: {row.get("document_type", "N/A")}')
            print(f'  Meeting Date: {row.get("meeting_date", "N/A")}')
            print(f'  Ordinance Title: {row.get("ordinance_title", "N/A")}')
            print(f'  Ordinance Number: {row.get("ordinance_number", "N/A")}')
            print(f'  Metadata: {row.get("metadata", "N/A")}')
            print(f'  Content (first 100 chars): {str(row.get("content", ""))[:100]}...')
            print(f'  Page Number: {row.get("page_number", "N/A")}')
            print(f'  Chunk Index: {row.get("chunk_index", "N/A")}')
            print('-' * 40)
            
        # Also check total count
        count_result = supabase.table('document_chunks').select('id', count='exact').execute()
        print(f'Total rows in document_chunks: {count_result.count}')
        
    except Exception as e:
        print(f'❌ Error connecting to database: {e}')

if __name__ == "__main__":
    check_document_chunks()