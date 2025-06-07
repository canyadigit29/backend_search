import pytest
from app.api.file_ops import chunk, embed
import tiktoken

class DummySupabase:
    def __init__(self):
        self.chunks = []
    def table(self, name):
        return self
    def select(self, *args, **kwargs):
        return self
    def eq(self, *args, **kwargs):
        return self
    def execute(self):
        return type('Result', (), {'data': []})()
    def insert(self, data):
        self.chunks.extend(data)
        return self

@pytest.fixture(autouse=True)
def patch_supabase(monkeypatch):
    dummy = DummySupabase()
    monkeypatch.setattr(chunk, 'supabase', dummy)
    monkeypatch.setattr(embed, 'supabase', dummy)
    yield dummy

def test_token_chunking_and_dedup():
    # Simulate a long text
    text = "This is a test sentence. " * 200
    # Patch extract_text to return our text
    chunk.extract_text = lambda path: text
    # Patch file_entry
    file_id = 'test-file-id'
    user_id = 'user-1'
    chunk.supabase.table = lambda name: type('T', (), {'select': lambda *a, **k: type('R', (), {'eq': lambda *a, **k: type('E', (), {'execute': lambda: type('D', (), {'data': [{'id': file_id, 'file_path': 'dummy.txt', 'user_id': user_id, 'project_id': 'proj-1'}]})()})()})()})
    # Run chunking
    records = chunk.chunk_file(file_id, user_id, enrich_metadata=True)
    assert records, 'Should return chunk records'
    # Check deduplication: rerun with same text, should skip all
    records2 = chunk.chunk_file(file_id, user_id, enrich_metadata=True)
    assert not records2, 'Should deduplicate and return no new chunks'
    # Check metadata
    for rec in records:
        assert 'chunk_hash' in rec
        assert 'content' in rec
        assert 'section_header' in rec
        assert 'page_number' in rec

def test_embedding_metadata(monkeypatch):
    # Patch OpenAI embedding
    monkeypatch.setattr(embed, 'embed_texts', lambda texts, model='text-embedding-3-large': [[0.0]*3072 for _ in texts])
    # Prepare dummy chunks
    chunks = ['chunk1', 'chunk2']
    hashes = ['hash1', 'hash2']
    headers = ['header1', 'header2']
    pages = [1, 2]
    results = embed.embed_chunks(chunks, 'proj-1', 'file.txt', chunk_hashes=hashes, section_headers=headers, page_numbers=pages)
    assert all(r.get('success') for r in results), 'All embeddings should succeed'
