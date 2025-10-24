-- Create/replace vector and FTS match functions aligned with app expectations
-- Embedding dimension: 3072 (text-embedding-3-large)

-- Ensure HNSW index exists on embedding for fast ANN search
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
ON public.document_chunks USING hnsw (embedding vector_cosine_ops);

-- Vector search (input threshold is DISTANCE; function returns SIMILARITY score)
CREATE OR REPLACE FUNCTION public.match_documents_v3(
    query_embedding public.vector(3072),
    match_threshold double precision DEFAULT 0.6, -- distance threshold (0..2 for cosine), app passes (1 - similarity)
    match_count integer DEFAULT 25,
    file_name_filter text DEFAULT NULL,
    description_filter text DEFAULT NULL, -- reserved; not used
    start_date date DEFAULT NULL,
    end_date date DEFAULT NULL,
    filter_document_type text DEFAULT NULL,
    filter_meeting_year integer DEFAULT NULL,
    filter_meeting_month integer DEFAULT NULL,
    filter_meeting_month_name text DEFAULT NULL,
    filter_meeting_day integer DEFAULT NULL,
    filter_ordinance_title text DEFAULT NULL
)
RETURNS TABLE(
    id uuid,
    file_id uuid,
    file_name text,
    content text,
    page_number integer,
    chunk_index integer,
    score double precision
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.file_id,
        f.file_name,
        dc.content,
        dc.page_number,
        dc.chunk_index,
        (1 - (dc.embedding <=> query_embedding)) AS score -- return SIMILARITY for app-side sorting
    FROM public.document_chunks AS dc
    JOIN public.files AS f ON f.id = dc.file_id
    WHERE
        -- distance threshold: app already converts similarity->distance
        (dc.embedding <=> query_embedding) < match_threshold
        -- file name filter (ILIKE contains)
        AND (file_name_filter IS NULL OR f.file_name ILIKE '%' || file_name_filter || '%')
        -- metadata filters
        AND (filter_document_type IS NULL OR dc.document_type = filter_document_type)
        AND (filter_meeting_year IS NULL OR EXTRACT(YEAR FROM dc.meeting_date) = filter_meeting_year)
        AND (filter_meeting_month IS NULL OR EXTRACT(MONTH FROM dc.meeting_date) = filter_meeting_month)
        AND (filter_meeting_month_name IS NULL OR TO_CHAR(dc.meeting_date, 'Month') ILIKE filter_meeting_month_name || '%')
        AND (filter_meeting_day IS NULL OR EXTRACT(DAY FROM dc.meeting_date) = filter_meeting_day)
        AND (filter_ordinance_title IS NULL OR dc.ordinance_title ILIKE '%' || filter_ordinance_title || '%')
        -- date bounds
        AND (start_date IS NULL OR dc.meeting_date >= start_date)
        AND (end_date IS NULL OR dc.meeting_date <= end_date)
    ORDER BY score DESC
    LIMIT match_count;
END;
$$;

-- Keyword FTS search using websearch_to_tsquery for OR/phrases support
CREATE OR REPLACE FUNCTION public.match_documents_fts_v3(
    keyword_query text,
    match_count integer DEFAULT 150,
    file_name_filter text DEFAULT NULL,
    description_filter text DEFAULT NULL, -- reserved; not used
    start_date date DEFAULT NULL,
    end_date date DEFAULT NULL,
    filter_document_type text DEFAULT NULL,
    filter_meeting_year integer DEFAULT NULL,
    filter_meeting_month integer DEFAULT NULL,
    filter_meeting_month_name text DEFAULT NULL,
    filter_meeting_day integer DEFAULT NULL,
    filter_ordinance_title text DEFAULT NULL
)
RETURNS TABLE(
    id uuid,
    file_id uuid,
    file_name text,
    content text,
    page_number integer,
    chunk_index integer,
    ts_rank double precision
)
LANGUAGE sql
SECURITY DEFINER
AS $$
    SELECT
        dc.id,
        dc.file_id,
        f.file_name,
        dc.content,
        dc.page_number,
        dc.chunk_index,
        ts_rank_cd(dc.content_tsv, websearch_to_tsquery('english', keyword_query)) AS ts_rank
    FROM public.document_chunks AS dc
    JOIN public.files AS f ON f.id = dc.file_id
    WHERE
        (keyword_query IS NULL OR dc.content_tsv @@ websearch_to_tsquery('english', keyword_query))
        AND (file_name_filter IS NULL OR f.file_name ILIKE '%' || file_name_filter || '%')
        AND (filter_document_type IS NULL OR dc.document_type = filter_document_type)
        AND (filter_meeting_year IS NULL OR EXTRACT(YEAR FROM dc.meeting_date) = filter_meeting_year)
        AND (filter_meeting_month IS NULL OR EXTRACT(MONTH FROM dc.meeting_date) = filter_meeting_month)
        AND (filter_meeting_month_name IS NULL OR TO_CHAR(dc.meeting_date, 'Month') ILIKE filter_meeting_month_name || '%')
        AND (filter_meeting_day IS NULL OR EXTRACT(DAY FROM dc.meeting_date) = filter_meeting_day)
        AND (filter_ordinance_title IS NULL OR dc.ordinance_title ILIKE '%' || filter_ordinance_title || '%')
        AND (start_date IS NULL OR dc.meeting_date >= start_date)
        AND (end_date IS NULL OR dc.meeting_date <= end_date)
    ORDER BY ts_rank DESC
    LIMIT match_count;
$$;

-- Optional: allow roles to execute (service_role will bypass RLS, but keep explicit grants)
GRANT EXECUTE ON FUNCTION public.match_documents_v3(public.vector(3072), double precision, integer, text, text, date, date, text, integer, integer, text, integer, text) TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.match_documents_fts_v3(text, integer, text, text, date, date, text, integer, integer, text, integer, text) TO anon, authenticated, service_role;
