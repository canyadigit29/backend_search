
DROP INDEX IF EXISTS document_chunks_embedding_idx;

ALTER TABLE public.document_chunks
ALTER COLUMN embedding TYPE vector(3072);


