create extension if not exists "vector" with schema "public" version '0.8.0';

create table "public"."document_chunks" (
    "id" uuid not null default gen_random_uuid(),
    "file_id" uuid,
    "user_id" uuid,
    "content" text not null,
    "embedding" vector(1536),
    "chunk_index" integer,
    "page_number" integer,
    "document_type" text,
    "meeting_date" date,
    "ordinance_title" text,
    "ordinance_number" text,
    "content_tsv" tsvector,
    "created_at" timestamp with time zone default now()
);


alter table "public"."document_chunks" enable row level security;

create table "public"."files" (
    "id" uuid not null default gen_random_uuid(),
    "user_id" uuid,
    "file_path" text not null,
    "file_name" text not null,
    "file_size" bigint,
    "file_type" text,
    "status" text default 'pending'::text,
    "error_message" text,
    "created_at" timestamp with time zone default now()
);


alter table "public"."files" enable row level security;

CREATE INDEX document_chunks_content_tsv_idx ON public.document_chunks USING gin (content_tsv);

CREATE INDEX document_chunks_document_type_idx ON public.document_chunks USING btree (document_type);

CREATE INDEX document_chunks_embedding_idx ON public.document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX document_chunks_meeting_date_idx ON public.document_chunks USING btree (meeting_date);

CREATE UNIQUE INDEX document_chunks_pkey ON public.document_chunks USING btree (id);

CREATE INDEX document_chunks_user_id_idx ON public.document_chunks USING btree (user_id);

CREATE UNIQUE INDEX files_pkey ON public.files USING btree (id);

alter table "public"."document_chunks" add constraint "document_chunks_pkey" PRIMARY KEY using index "document_chunks_pkey";

alter table "public"."files" add constraint "files_pkey" PRIMARY KEY using index "files_pkey";

alter table "public"."document_chunks" add constraint "document_chunks_file_id_fkey" FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE not valid;

alter table "public"."document_chunks" validate constraint "document_chunks_file_id_fkey";

alter table "public"."document_chunks" add constraint "document_chunks_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE not valid;

alter table "public"."document_chunks" validate constraint "document_chunks_user_id_fkey";

alter table "public"."files" add constraint "files_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE not valid;

alter table "public"."files" validate constraint "files_user_id_fkey";

set check_function_bodies = off;

CREATE OR REPLACE FUNCTION public.match_documents(query_embedding vector, match_threshold double precision, match_count integer, user_id_filter uuid, filter_document_type text DEFAULT NULL::text, filter_start_date date DEFAULT NULL::date, filter_end_date date DEFAULT NULL::date)
 RETURNS TABLE(id uuid, file_id uuid, content text, page_number integer, score double precision)
 LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.file_id,
        dc.content,
        dc.page_number,
        (1 - (dc.embedding <=> query_embedding)) AS score
    FROM
        document_chunks AS dc
    WHERE
        dc.user_id = user_id_filter
        AND (1 - (dc.embedding <=> query_embedding)) > match_threshold
        AND (filter_document_type IS NULL OR dc.document_type = filter_document_type)
        AND (filter_start_date IS NULL OR dc.meeting_date >= filter_start_date)
        AND (filter_end_date IS NULL OR dc.meeting_date <= filter_end_date)
    ORDER BY
        score DESC
    LIMIT
        match_count;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.update_document_chunks_tsv()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.content_tsv := to_tsvector('english', NEW.content);
    RETURN NEW;
END;
$function$
;

CREATE OR REPLACE FUNCTION public.get_all_file_names()
 RETURNS TABLE(name text)
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
BEGIN
  RETURN QUERY SELECT f.file_name AS name FROM files f;
END;
$function$
;

create policy "Allow service roles to bypass RLS for chunks"
on "public"."document_chunks"
as permissive
for all
to public
using ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


create policy "Allow users to read their own document chunks"
on "public"."document_chunks"
as permissive
for select
to public
using ((auth.uid() = user_id));


create policy "Allow service roles to bypass RLS"
on "public"."files"
as permissive
for all
to public
using ((((current_setting('request.jwt.claims'::text, true))::jsonb ->> 'role'::text) = 'service_role'::text));


create policy "Allow users to manage their own files"
on "public"."files"
as permissive
for all
to public
using ((auth.uid() = user_id));


CREATE TRIGGER tsvector_update BEFORE INSERT OR UPDATE ON public.document_chunks FOR EACH ROW EXECUTE FUNCTION update_document_chunks_tsv();



