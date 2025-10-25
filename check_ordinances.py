import os
from app.core.supabase_client import create_client

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_SERVICE_ROLE = os.environ.get('SUPABASE_SERVICE_ROLE')

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# Query specifically for Ordinance document types
print('=== ORDINANCE DOCUMENTS ===')
ordinance_result = supabase.table('document_chunks').select('*').eq('document_type', 'Ordinance').limit(5).execute()

for i, row in enumerate(ordinance_result.data):
    print(f'Ordinance {i+1}:')
    print(f'  Document Type: {row.get("document_type")}')
    print(f'  Ordinance Title: {row.get("ordinance_title")}')
    print(f'  Ordinance Number: {row.get("ordinance_number")}')
    print(f'  Meeting Date: {row.get("meeting_date")}')
    print(f'  Content preview: {str(row.get("content", ""))[:100]}...')
    print('-' * 50)

print('\n=== MINUTES DOCUMENTS (should have no ordinance info) ===')
minutes_result = supabase.table('document_chunks').select('*').eq('document_type', 'Minutes').limit(3).execute()

for i, row in enumerate(minutes_result.data):
    print(f'Minutes {i+1}:')
    print(f'  Document Type: {row.get("document_type")}')
    print(f'  Ordinance Title: {row.get("ordinance_title")}')
    print(f'  Ordinance Number: {row.get("ordinance_number")}')
    print(f'  Meeting Date: {row.get("meeting_date")}')
    print('-' * 30)

# Check counts by document type
print('\n=== DOCUMENT TYPE SUMMARY ===')
all_docs = supabase.table('document_chunks').select('document_type,ordinance_title,ordinance_number').execute()

from collections import Counter
doc_types = Counter()
ordinances_with_titles = 0
ordinances_with_numbers = 0

for doc in all_docs.data:
    doc_type = doc.get('document_type', 'unknown')
    doc_types[doc_type] += 1
    
    if doc_type == 'Ordinance':
        if doc.get('ordinance_title'):
            ordinances_with_titles += 1
        if doc.get('ordinance_number'):
            ordinances_with_numbers += 1

print('Document type counts:')
for doc_type, count in doc_types.items():
    print(f'  {doc_type}: {count}')

print(f'\nOrdinance field analysis:')
print(f'  Ordinances with titles: {ordinances_with_titles}')
print(f'  Ordinances with numbers: {ordinances_with_numbers}')
print(f'  Total Ordinance chunks: {doc_types.get("Ordinance", 0)}')