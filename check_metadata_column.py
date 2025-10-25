import os
from app.core.supabase_client import create_client

supabase = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE'])

# Check the actual metadata column content
result = supabase.table('document_chunks').select('id,document_type,metadata').limit(10).execute()

print('=== METADATA COLUMN CONTENTS ===')
for i, row in enumerate(result.data):
    metadata = row.get('metadata', 'missing')
    print(f'{i+1}. Type: {row.get("document_type")}, Metadata: {metadata}')

# Check if ANY row has non-empty metadata
all_result = supabase.table('document_chunks').select('metadata').execute()
non_empty_metadata = [row for row in all_result.data if row.get('metadata') and row.get('metadata') != {}]

print(f'\nMetadata analysis:')
print(f'  Total rows checked: {len(all_result.data)}')
print(f'  Rows with non-empty metadata: {len(non_empty_metadata)}')

if non_empty_metadata:
    print('Sample non-empty metadata:')
    for i, row in enumerate(non_empty_metadata[:3]):
        print(f'  {i+1}: {row["metadata"]}')
else:
    print('  All metadata columns are empty ({}) or null')
    print('  This is fine - we extract metadata from content instead!')