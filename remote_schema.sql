


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


CREATE SCHEMA IF NOT EXISTS "public";


ALTER SCHEMA "public" OWNER TO "pg_database_owner";


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE OR REPLACE FUNCTION "public"."create_duplicate_messages_for_new_chat"("old_chat_id" "uuid", "new_chat_id" "uuid", "new_user_id" "uuid") RETURNS "void"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    INSERT INTO messages (user_id, chat_id, content, role, model, sequence_number, tokens, created_at, updated_at)
    SELECT new_user_id, new_chat_id, content, role, model, sequence_number, tokens, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
    FROM messages
    WHERE chat_id = old_chat_id;
END;
$$;


ALTER FUNCTION "public"."create_duplicate_messages_for_new_chat"("old_chat_id" "uuid", "new_chat_id" "uuid", "new_user_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."create_profile_and_workspace"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO 'public'
    AS $$
DECLARE
    random_username TEXT;
BEGIN
    -- Generate a random username
    random_username := 'user' || substr(replace(gen_random_uuid()::text, '-', ''), 1, 16);

    -- Create a profile for the new user
    INSERT INTO public.profiles(user_id, anthropic_api_key, azure_openai_35_turbo_id, azure_openai_45_turbo_id, azure_openai_45_vision_id, azure_openai_api_key, azure_openai_endpoint, google_gemini_api_key, has_onboarded, image_url, image_path, mistral_api_key, display_name, bio, openai_api_key, openai_organization_id, perplexity_api_key, profile_context, use_azure_openai, username)
    VALUES(
        NEW.id,
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        FALSE,
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        FALSE,
        random_username
    );

    INSERT INTO public.workspaces(user_id, is_home, name, default_context_length, default_model, default_prompt, default_temperature, description, embeddings_provider, include_profile_context, include_workspace_instructions, instructions)
    VALUES(
        NEW.id,
        TRUE,
        'Home',
        4096,
        'gpt-4-turbo-preview', -- Updated default model
        'You are a friendly, helpful AI assistant.',
        0.5,
        'My home workspace.',
        'openai',
        TRUE,
        TRUE,
        ''
    );

    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."create_profile_and_workspace"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_message_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) RETURNS "void"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    DELETE FROM messages 
    WHERE user_id = p_user_id AND chat_id = p_chat_id AND sequence_number >= p_sequence_number;
END;
$$;


ALTER FUNCTION "public"."delete_message_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_messages_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) RETURNS "void"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    DELETE FROM messages 
    WHERE user_id = p_user_id AND chat_id = p_chat_id AND sequence_number >= p_sequence_number;
END;
$$;


ALTER FUNCTION "public"."delete_messages_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_old_assistant_image"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  status INT;
  content TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT
      INTO status, content
      result.status, result.content
      FROM public.delete_storage_object_from_bucket('assistant_images', OLD.image_path) AS result;
    IF status <> 200 THEN
      RAISE WARNING 'Could not delete assistant image: % %', status, content;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."delete_old_assistant_image"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_old_file"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  status INT;
  content TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT
      INTO status, content
      result.status, result.content
      FROM public.delete_storage_object_from_bucket('files', OLD.file_path) AS result;
    IF status <> 200 THEN
      RAISE WARNING 'Could not delete file: % %', status, content;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."delete_old_file"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_old_message_images"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  status INT;
  content TEXT;
  image_path TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    FOREACH image_path IN ARRAY OLD.image_paths
    LOOP
      SELECT
        INTO status, content
        result.status, result.content
        FROM public.delete_storage_object_from_bucket('message_images', image_path) AS result;
      IF status <> 200 THEN
        RAISE WARNING 'Could not delete message image: % %', status, content;
      END IF;
    END LOOP;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."delete_old_message_images"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_old_profile_image"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  status INT;
  content TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT
      INTO status, content
      result.status, result.content
      FROM public.delete_storage_object_from_bucket('profile_images', OLD.image_path) AS result;
    IF status <> 200 THEN
      RAISE WARNING 'Could not delete profile image: % %', status, content;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."delete_old_profile_image"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_old_workspace_image"() RETURNS "trigger"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  status INT;
  content TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT
      INTO status, content
      result.status, result.content
      FROM public.delete_storage_object_from_bucket('workspace_images', OLD.image_path) AS result;
    IF status <> 200 THEN
      RAISE WARNING 'Could not delete workspace image: % %', status, content;
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."delete_old_workspace_image"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_storage_object"("bucket" "text", "object" "text", OUT "status" integer, OUT "content" "text") RETURNS "record"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  project_url TEXT := 'https://wxqltcthpmrlmtxexzoe.supabase.co';
  service_role_key TEXT := 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Ind4cWx0Y3RocG1ybG10eGV4em9lIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2MTQxOTQ4NCwiZXhwIjoyMDc2OTk1NDg0fQ.tvbHBkGff-RPWZ440B7TfJdOWRFxCKm3LZiwBIirSw4';
  url TEXT := project_url || '/storage/v1/object/' || bucket || '/' || object;
BEGIN
  SELECT
      INTO status, content
           result.status::INT, result.content::TEXT
      FROM extensions.http((
    'DELETE',
    url,
    ARRAY[extensions.http_header('authorization','Bearer ' || service_role_key)],
    NULL,
    NULL)::extensions.http_request) AS result;
END;
$$;


ALTER FUNCTION "public"."delete_storage_object"("bucket" "text", "object" "text", OUT "status" integer, OUT "content" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."delete_storage_object_from_bucket"("bucket_name" "text", "object_path" "text", OUT "status" integer, OUT "content" "text") RETURNS "record"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  SELECT
      INTO status, content
           result.status, result.content
      FROM public.delete_storage_object(bucket_name, object_path) AS result;
END;
$$;


ALTER FUNCTION "public"."delete_storage_object_from_bucket"("bucket_name" "text", "object_path" "text", OUT "status" integer, OUT "content" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_all_file_names"() RETURNS TABLE("id" "uuid", "name" "text", "file_path" "text")
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  RETURN QUERY
  SELECT f.id, f.name AS name, f.file_path FROM public.files f;
END;
$$;


ALTER FUNCTION "public"."get_all_file_names"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_gdrive_file_names"() RETURNS TABLE("file_name" "text")
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  RETURN QUERY
  SELECT name FROM public.files WHERE metadata->>'source' = 'google_drive';
END;
$$;


ALTER FUNCTION "public"."get_gdrive_file_names"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_file_items_fts"("keyword_query" "text", "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text" DEFAULT NULL::"text", "filter_description" "text" DEFAULT NULL::"text", "filter_document_type" "text" DEFAULT NULL::"text", "filter_meeting_year" integer DEFAULT NULL::integer, "filter_meeting_month" integer DEFAULT NULL::integer, "filter_meeting_month_name" "text" DEFAULT NULL::"text", "filter_meeting_day" integer DEFAULT NULL::integer, "filter_ordinance_title" "text" DEFAULT NULL::"text") RETURNS TABLE("id" "uuid", "file_id" "uuid", "content" "text", "ts_rank" real, "file_name" "text", "description" "text", "document_type" "text", "meeting_year" integer, "meeting_month" integer, "meeting_month_name" "text", "meeting_day" integer, "ordinance_title" "text")
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        fi.id,
        fi.file_id,
        fi.content,
        ts_rank(fi.content_fts, websearch_to_tsquery('english', keyword_query)) AS ts_rank,
        fi.file_name,
        fi.description,
        fi.document_type,
        fi.meeting_year,
        fi.meeting_month,
        fi.meeting_month_name,
        fi.meeting_day,
        fi.ordinance_title
    FROM
        public.file_items AS fi
    WHERE
        fi.content_fts @@ websearch_to_tsquery('english', keyword_query)
        AND (file_ids IS NULL OR fi.file_id = ANY(file_ids))
        AND (filter_file_name IS NULL OR fi.file_name ILIKE ('%' || filter_file_name || '%'))
        AND (filter_description IS NULL OR fi.description ILIKE ('%' || filter_description || '%'))
        AND (filter_document_type IS NULL OR fi.document_type = filter_document_type)
        AND (filter_meeting_year IS NULL OR fi.meeting_year = filter_meeting_year)
        AND (filter_meeting_month IS NULL OR fi.meeting_month = filter_meeting_month)
        AND (filter_meeting_month_name IS NULL OR fi.meeting_month_name = filter_meeting_month_name)
        AND (filter_meeting_day IS NULL OR fi.meeting_day = filter_meeting_day)
        AND (filter_ordinance_title IS NULL OR fi.ordinance_title ILIKE ('%' || filter_ordinance_title || '%'))
    ORDER BY
        ts_rank DESC
    LIMIT match_count;
END;
$$;


ALTER FUNCTION "public"."match_file_items_fts"("keyword_query" "text", "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_file_items_local"("query_embedding" "public"."vector", "match_count" integer DEFAULT NULL::integer, "file_ids" "uuid"[] DEFAULT NULL::"uuid"[]) RETURNS TABLE("id" "uuid", "file_id" "uuid", "content" "text", "tokens" integer, "similarity" double precision)
    LANGUAGE "plpgsql"
    AS $$
#variable_conflict use_column
begin
  return query
  select
    id,
    file_id,
    content,
    tokens,
    1 - (file_items.local_embedding <=> query_embedding) as similarity
  from file_items
  where (file_id = ANY(file_ids))
  order by file_items.local_embedding <=> query_embedding
  limit match_count;
end;
$$;


ALTER FUNCTION "public"."match_file_items_local"("query_embedding" "public"."vector", "match_count" integer, "file_ids" "uuid"[]) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_file_items_openai"("query_embedding" "public"."vector", "match_threshold" double precision, "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text" DEFAULT NULL::"text", "filter_description" "text" DEFAULT NULL::"text", "filter_document_type" "text" DEFAULT NULL::"text", "filter_meeting_year" integer DEFAULT NULL::integer, "filter_meeting_month" integer DEFAULT NULL::integer, "filter_meeting_month_name" "text" DEFAULT NULL::"text", "filter_meeting_day" integer DEFAULT NULL::integer, "filter_ordinance_title" "text" DEFAULT NULL::"text") RETURNS TABLE("id" "uuid", "file_id" "uuid", "content" "text", "similarity" double precision, "file_name" "text", "description" "text", "document_type" "text", "meeting_year" integer, "meeting_month" integer, "meeting_month_name" "text", "meeting_day" integer, "ordinance_title" "text")
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        fi.id,
        fi.file_id,
        fi.content,
        1 - (fi.openai_embedding <=> query_embedding) AS similarity,
        fi.file_name,
        fi.description,
        fi.document_type,
        fi.meeting_year,
        fi.meeting_month,
        fi.meeting_month_name,
        fi.meeting_day,
        fi.ordinance_title
    FROM
        public.file_items AS fi
    WHERE
        (1 - (fi.openai_embedding <=> query_embedding)) > match_threshold
        AND (file_ids IS NULL OR fi.file_id = ANY(file_ids))
        AND (filter_file_name IS NULL OR fi.file_name ILIKE ('%' || filter_file_name || '%'))
        AND (filter_description IS NULL OR fi.description ILIKE ('%' || filter_description || '%'))
        AND (filter_document_type IS NULL OR fi.document_type = filter_document_type)
        AND (filter_meeting_year IS NULL OR fi.meeting_year = filter_meeting_year)
        AND (filter_meeting_month IS NULL OR fi.meeting_month = filter_meeting_month)
        AND (filter_meeting_month_name IS NULL OR fi.meeting_month_name = filter_meeting_month_name)
        AND (filter_meeting_day IS NULL OR fi.meeting_day = filter_meeting_day)
        AND (filter_ordinance_title IS NULL OR fi.ordinance_title ILIKE ('%' || filter_ordinance_title || '%'))
    ORDER BY
        similarity DESC
    LIMIT match_count;
END;
$$;


ALTER FUNCTION "public"."match_file_items_openai"("query_embedding" "public"."vector", "match_threshold" double precision, "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."non_private_assistant_exists"("p_name" "text") RETURNS boolean
    LANGUAGE "sql" SECURITY DEFINER
    AS $$
    SELECT EXISTS (
        SELECT 1
        FROM assistants
        WHERE (id::text = (storage.filename(p_name))) AND sharing <> 'private'
    );
$$;


ALTER FUNCTION "public"."non_private_assistant_exists"("p_name" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."non_private_file_exists"("p_name" "text") RETURNS boolean
    LANGUAGE "sql" SECURITY DEFINER
    AS $$
    SELECT EXISTS (
        SELECT 1
        FROM files
        WHERE (id::text = (storage.foldername(p_name))[2]) AND sharing <> 'private'
    );
$$;


ALTER FUNCTION "public"."non_private_file_exists"("p_name" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."non_private_workspace_exists"("p_name" "text") RETURNS boolean
    LANGUAGE "sql" SECURITY DEFINER
    AS $$
    SELECT EXISTS (
        SELECT 1
        FROM workspaces
        WHERE (id::text = (storage.filename(p_name))) AND sharing <> 'private'
    );
$$;


ALTER FUNCTION "public"."non_private_workspace_exists"("p_name" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."prevent_home_field_update"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  IF NEW.is_home IS DISTINCT FROM OLD.is_home THEN
    RAISE EXCEPTION 'Updating the home field of workspace is not allowed.';
  END IF;
  
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."prevent_home_field_update"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."prevent_home_workspace_deletion"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  IF OLD.is_home THEN
    RAISE EXCEPTION 'Home workspace deletion is not allowed.';
  END IF;
  
  RETURN OLD;
END;
$$;


ALTER FUNCTION "public"."prevent_home_workspace_deletion"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_updated_at_column"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = now(); 
    RETURN NEW; 
END;
$$;


ALTER FUNCTION "public"."update_updated_at_column"() OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."assistant_collections" (
    "user_id" "uuid" NOT NULL,
    "assistant_id" "uuid" NOT NULL,
    "collection_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."assistant_collections" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."assistant_files" (
    "user_id" "uuid" NOT NULL,
    "assistant_id" "uuid" NOT NULL,
    "file_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."assistant_files" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."assistant_tools" (
    "user_id" "uuid" NOT NULL,
    "assistant_id" "uuid" NOT NULL,
    "tool_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."assistant_tools" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."assistant_workspaces" (
    "user_id" "uuid" NOT NULL,
    "assistant_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."assistant_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."assistants" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "context_length" integer NOT NULL,
    "description" "text" NOT NULL,
    "embeddings_provider" "text" NOT NULL,
    "include_profile_context" boolean NOT NULL,
    "include_workspace_instructions" boolean NOT NULL,
    "model" "text" NOT NULL,
    "name" "text" NOT NULL,
    "image_path" "text" NOT NULL,
    "prompt" "text" NOT NULL,
    "temperature" real NOT NULL,
    CONSTRAINT "assistants_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "assistants_embeddings_provider_check" CHECK (("char_length"("embeddings_provider") <= 1000)),
    CONSTRAINT "assistants_image_path_check" CHECK (("char_length"("image_path") <= 1000)),
    CONSTRAINT "assistants_model_check" CHECK (("char_length"("model") <= 1000)),
    CONSTRAINT "assistants_name_check" CHECK (("char_length"("name") <= 100)),
    CONSTRAINT "assistants_prompt_check" CHECK (("char_length"("prompt") <= 100000))
);


ALTER TABLE "public"."assistants" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."chat_files" (
    "user_id" "uuid" NOT NULL,
    "chat_id" "uuid" NOT NULL,
    "file_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."chat_files" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."chats" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "assistant_id" "uuid",
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "context_length" integer NOT NULL,
    "embeddings_provider" "text" NOT NULL,
    "include_profile_context" boolean NOT NULL,
    "include_workspace_instructions" boolean NOT NULL,
    "model" "text" NOT NULL,
    "name" "text" NOT NULL,
    "prompt" "text" NOT NULL,
    "temperature" real NOT NULL,
    CONSTRAINT "chats_embeddings_provider_check" CHECK (("char_length"("embeddings_provider") <= 1000)),
    CONSTRAINT "chats_model_check" CHECK (("char_length"("model") <= 1000)),
    CONSTRAINT "chats_name_check" CHECK (("char_length"("name") <= 200)),
    CONSTRAINT "chats_prompt_check" CHECK (("char_length"("prompt") <= 100000))
);


ALTER TABLE "public"."chats" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."collection_files" (
    "user_id" "uuid" NOT NULL,
    "collection_id" "uuid" NOT NULL,
    "file_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."collection_files" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."collection_workspaces" (
    "user_id" "uuid" NOT NULL,
    "collection_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."collection_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."collections" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "description" "text" NOT NULL,
    "name" "text" NOT NULL,
    CONSTRAINT "collections_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "collections_name_check" CHECK (("char_length"("name") <= 100))
);


ALTER TABLE "public"."collections" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."file_items" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "file_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "content" "text" NOT NULL,
    "local_embedding" "public"."vector"(384),
    "openai_embedding" "public"."vector"(3072),
    "tokens" integer NOT NULL,
    "file_name" "text",
    "description" "text",
    "document_type" "text",
    "meeting_year" integer,
    "meeting_month" integer,
    "meeting_month_name" "text",
    "meeting_day" integer,
    "ordinance_title" "text",
    "content_fts" "tsvector" GENERATED ALWAYS AS ("to_tsvector"('"english"'::"regconfig", "content")) STORED,
    "page_number" integer,
    "meeting_date" "date",
    "ordinance_number" "text",
    "section_header" "text",
    "section_header_fts" "tsvector" GENERATED ALWAYS AS ("to_tsvector"('"english"'::"regconfig", "section_header")) STORED,
    "chunk_index" integer
);


ALTER TABLE "public"."file_items" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."file_workspaces" (
    "user_id" "uuid" NOT NULL,
    "file_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."file_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."files" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "description" "text" NOT NULL,
    "file_path" "text" NOT NULL,
    "name" "text" NOT NULL,
    "size" integer NOT NULL,
    "tokens" integer NOT NULL,
    "type" "text" NOT NULL,
    "ingested" boolean DEFAULT false,
    "ocr_needed" boolean DEFAULT false,
    "ocr_scanned" boolean DEFAULT false,
    "ocr_started_at" timestamp with time zone,
    "ocr_completed_at" timestamp with time zone,
    "ocr_error" "text",
    "ingestion_started_at" timestamp with time zone,
    "ingestion_completed_at" timestamp with time zone,
    "ingestion_error" "text",
    "ocr_text_path" "text",
    CONSTRAINT "files_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "files_file_path_check" CHECK (("char_length"("file_path") <= 1000)),
    CONSTRAINT "files_name_check" CHECK (("char_length"("name") <= 100)),
    CONSTRAINT "files_type_check" CHECK (("char_length"("type") <= 100))
);


ALTER TABLE "public"."files" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."folders" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "name" "text" NOT NULL,
    "description" "text" NOT NULL,
    "type" "text" NOT NULL
);


ALTER TABLE "public"."folders" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."message_file_items" (
    "user_id" "uuid" NOT NULL,
    "message_id" "uuid" NOT NULL,
    "file_item_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."message_file_items" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."messages" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "chat_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "content" "text" NOT NULL,
    "image_paths" "text"[] NOT NULL,
    "model" "text" NOT NULL,
    "role" "text" NOT NULL,
    "sequence_number" integer NOT NULL,
    "assistant_id" "uuid",
    CONSTRAINT "check_image_paths_length" CHECK (("array_length"("image_paths", 1) <= 16)),
    CONSTRAINT "messages_content_check" CHECK (("char_length"("content") <= 1000000)),
    CONSTRAINT "messages_model_check" CHECK (("char_length"("model") <= 1000)),
    CONSTRAINT "messages_role_check" CHECK (("char_length"("role") <= 1000))
);


ALTER TABLE "public"."messages" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."model_workspaces" (
    "user_id" "uuid" NOT NULL,
    "model_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."model_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."models" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "api_key" "text" NOT NULL,
    "base_url" "text" NOT NULL,
    "description" "text" NOT NULL,
    "model_id" "text" NOT NULL,
    "name" "text" NOT NULL,
    "context_length" integer DEFAULT 4096 NOT NULL,
    CONSTRAINT "models_api_key_check" CHECK (("char_length"("api_key") <= 1000)),
    CONSTRAINT "models_base_url_check" CHECK (("char_length"("base_url") <= 1000)),
    CONSTRAINT "models_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "models_model_id_check" CHECK (("char_length"("model_id") <= 1000)),
    CONSTRAINT "models_name_check" CHECK (("char_length"("name") <= 100))
);


ALTER TABLE "public"."models" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."preset_workspaces" (
    "user_id" "uuid" NOT NULL,
    "preset_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."preset_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."presets" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "context_length" integer NOT NULL,
    "description" "text" NOT NULL,
    "embeddings_provider" "text" NOT NULL,
    "include_profile_context" boolean NOT NULL,
    "include_workspace_instructions" boolean NOT NULL,
    "model" "text" NOT NULL,
    "name" "text" NOT NULL,
    "prompt" "text" NOT NULL,
    "temperature" real NOT NULL,
    CONSTRAINT "presets_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "presets_embeddings_provider_check" CHECK (("char_length"("embeddings_provider") <= 1000)),
    CONSTRAINT "presets_model_check" CHECK (("char_length"("model") <= 1000)),
    CONSTRAINT "presets_name_check" CHECK (("char_length"("name") <= 100)),
    CONSTRAINT "presets_prompt_check" CHECK (("char_length"("prompt") <= 100000))
);


ALTER TABLE "public"."presets" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."profiles" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "bio" "text" NOT NULL,
    "has_onboarded" boolean DEFAULT false NOT NULL,
    "image_url" "text" NOT NULL,
    "image_path" "text" NOT NULL,
    "profile_context" "text" NOT NULL,
    "display_name" "text" NOT NULL,
    "use_azure_openai" boolean NOT NULL,
    "username" "text" NOT NULL,
    "anthropic_api_key" "text",
    "azure_openai_35_turbo_id" "text",
    "azure_openai_45_turbo_id" "text",
    "azure_openai_45_vision_id" "text",
    "azure_openai_api_key" "text",
    "azure_openai_endpoint" "text",
    "google_gemini_api_key" "text",
    "mistral_api_key" "text",
    "openai_api_key" "text",
    "openai_organization_id" "text",
    "perplexity_api_key" "text",
    "openrouter_api_key" "text",
    "azure_openai_embeddings_id" "text",
    "groq_api_key" "text",
    CONSTRAINT "profiles_anthropic_api_key_check" CHECK (("char_length"("anthropic_api_key") <= 1000)),
    CONSTRAINT "profiles_azure_openai_35_turbo_id_check" CHECK (("char_length"("azure_openai_35_turbo_id") <= 1000)),
    CONSTRAINT "profiles_azure_openai_45_turbo_id_check" CHECK (("char_length"("azure_openai_45_turbo_id") <= 1000)),
    CONSTRAINT "profiles_azure_openai_45_vision_id_check" CHECK (("char_length"("azure_openai_45_vision_id") <= 1000)),
    CONSTRAINT "profiles_azure_openai_api_key_check" CHECK (("char_length"("azure_openai_api_key") <= 1000)),
    CONSTRAINT "profiles_azure_openai_embeddings_id_check" CHECK (("char_length"("azure_openai_embeddings_id") <= 1000)),
    CONSTRAINT "profiles_azure_openai_endpoint_check" CHECK (("char_length"("azure_openai_endpoint") <= 1000)),
    CONSTRAINT "profiles_bio_check" CHECK (("char_length"("bio") <= 500)),
    CONSTRAINT "profiles_display_name_check" CHECK (("char_length"("display_name") <= 100)),
    CONSTRAINT "profiles_google_gemini_api_key_check" CHECK (("char_length"("google_gemini_api_key") <= 1000)),
    CONSTRAINT "profiles_groq_api_key_check" CHECK (("char_length"("groq_api_key") <= 1000)),
    CONSTRAINT "profiles_image_path_check" CHECK (("char_length"("image_path") <= 1000)),
    CONSTRAINT "profiles_image_url_check" CHECK (("char_length"("image_url") <= 1000)),
    CONSTRAINT "profiles_mistral_api_key_check" CHECK (("char_length"("mistral_api_key") <= 1000)),
    CONSTRAINT "profiles_openai_api_key_check" CHECK (("char_length"("openai_api_key") <= 1000)),
    CONSTRAINT "profiles_openai_organization_id_check" CHECK (("char_length"("openai_organization_id") <= 1000)),
    CONSTRAINT "profiles_openrouter_api_key_check" CHECK (("char_length"("openrouter_api_key") <= 1000)),
    CONSTRAINT "profiles_perplexity_api_key_check" CHECK (("char_length"("perplexity_api_key") <= 1000)),
    CONSTRAINT "profiles_profile_context_check" CHECK (("char_length"("profile_context") <= 1500)),
    CONSTRAINT "profiles_username_check" CHECK ((("char_length"("username") >= 3) AND ("char_length"("username") <= 25)))
);


ALTER TABLE "public"."profiles" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."prompt_workspaces" (
    "user_id" "uuid" NOT NULL,
    "prompt_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."prompt_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."prompts" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "content" "text" NOT NULL,
    "name" "text" NOT NULL,
    CONSTRAINT "prompts_content_check" CHECK (("char_length"("content") <= 100000)),
    CONSTRAINT "prompts_name_check" CHECK (("char_length"("name") <= 100))
);


ALTER TABLE "public"."prompts" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."tool_workspaces" (
    "user_id" "uuid" NOT NULL,
    "tool_id" "uuid" NOT NULL,
    "workspace_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone
);


ALTER TABLE "public"."tool_workspaces" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."tools" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "folder_id" "uuid",
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "description" "text" NOT NULL,
    "name" "text" NOT NULL,
    "schema" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "url" "text" NOT NULL,
    "custom_headers" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    CONSTRAINT "tools_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "tools_name_check" CHECK (("char_length"("name") <= 100)),
    CONSTRAINT "tools_url_check" CHECK (("char_length"("url") <= 1000))
);


ALTER TABLE "public"."tools" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."workspaces" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updated_at" timestamp with time zone,
    "sharing" "text" DEFAULT 'private'::"text" NOT NULL,
    "default_context_length" integer NOT NULL,
    "default_model" "text" NOT NULL,
    "default_prompt" "text" NOT NULL,
    "default_temperature" real NOT NULL,
    "description" "text" NOT NULL,
    "embeddings_provider" "text" NOT NULL,
    "include_profile_context" boolean NOT NULL,
    "include_workspace_instructions" boolean NOT NULL,
    "instructions" "text" NOT NULL,
    "is_home" boolean DEFAULT false NOT NULL,
    "name" "text" NOT NULL,
    "image_path" "text" DEFAULT ''::"text" NOT NULL,
    CONSTRAINT "workspaces_default_model_check" CHECK (("char_length"("default_model") <= 1000)),
    CONSTRAINT "workspaces_default_prompt_check" CHECK (("char_length"("default_prompt") <= 100000)),
    CONSTRAINT "workspaces_description_check" CHECK (("char_length"("description") <= 500)),
    CONSTRAINT "workspaces_embeddings_provider_check" CHECK (("char_length"("embeddings_provider") <= 1000)),
    CONSTRAINT "workspaces_image_path_check" CHECK (("char_length"("image_path") <= 1000)),
    CONSTRAINT "workspaces_instructions_check" CHECK (("char_length"("instructions") <= 1500)),
    CONSTRAINT "workspaces_name_check" CHECK (("char_length"("name") <= 100))
);


ALTER TABLE "public"."workspaces" OWNER TO "postgres";


ALTER TABLE ONLY "public"."assistant_collections"
    ADD CONSTRAINT "assistant_collections_pkey" PRIMARY KEY ("assistant_id", "collection_id");



ALTER TABLE ONLY "public"."assistant_files"
    ADD CONSTRAINT "assistant_files_pkey" PRIMARY KEY ("assistant_id", "file_id");



ALTER TABLE ONLY "public"."assistant_tools"
    ADD CONSTRAINT "assistant_tools_pkey" PRIMARY KEY ("assistant_id", "tool_id");



ALTER TABLE ONLY "public"."assistant_workspaces"
    ADD CONSTRAINT "assistant_workspaces_pkey" PRIMARY KEY ("assistant_id", "workspace_id");



ALTER TABLE ONLY "public"."assistants"
    ADD CONSTRAINT "assistants_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."chat_files"
    ADD CONSTRAINT "chat_files_pkey" PRIMARY KEY ("chat_id", "file_id");



ALTER TABLE ONLY "public"."chats"
    ADD CONSTRAINT "chats_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."collection_files"
    ADD CONSTRAINT "collection_files_pkey" PRIMARY KEY ("collection_id", "file_id");



ALTER TABLE ONLY "public"."collection_workspaces"
    ADD CONSTRAINT "collection_workspaces_pkey" PRIMARY KEY ("collection_id", "workspace_id");



ALTER TABLE ONLY "public"."collections"
    ADD CONSTRAINT "collections_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."file_items"
    ADD CONSTRAINT "file_items_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."file_workspaces"
    ADD CONSTRAINT "file_workspaces_pkey" PRIMARY KEY ("file_id", "workspace_id");



ALTER TABLE ONLY "public"."files"
    ADD CONSTRAINT "files_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."folders"
    ADD CONSTRAINT "folders_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."message_file_items"
    ADD CONSTRAINT "message_file_items_pkey" PRIMARY KEY ("message_id", "file_item_id");



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."model_workspaces"
    ADD CONSTRAINT "model_workspaces_pkey" PRIMARY KEY ("model_id", "workspace_id");



ALTER TABLE ONLY "public"."models"
    ADD CONSTRAINT "models_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."preset_workspaces"
    ADD CONSTRAINT "preset_workspaces_pkey" PRIMARY KEY ("preset_id", "workspace_id");



ALTER TABLE ONLY "public"."presets"
    ADD CONSTRAINT "presets_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_user_id_key" UNIQUE ("user_id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_username_key" UNIQUE ("username");



ALTER TABLE ONLY "public"."prompt_workspaces"
    ADD CONSTRAINT "prompt_workspaces_pkey" PRIMARY KEY ("prompt_id", "workspace_id");



ALTER TABLE ONLY "public"."prompts"
    ADD CONSTRAINT "prompts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."tool_workspaces"
    ADD CONSTRAINT "tool_workspaces_pkey" PRIMARY KEY ("tool_id", "workspace_id");



ALTER TABLE ONLY "public"."tools"
    ADD CONSTRAINT "tools_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."workspaces"
    ADD CONSTRAINT "workspaces_pkey" PRIMARY KEY ("id");



CREATE INDEX "assistant_collections_assistant_id_idx" ON "public"."assistant_collections" USING "btree" ("assistant_id");



CREATE INDEX "assistant_collections_collection_id_idx" ON "public"."assistant_collections" USING "btree" ("collection_id");



CREATE INDEX "assistant_collections_user_id_idx" ON "public"."assistant_collections" USING "btree" ("user_id");



CREATE INDEX "assistant_files_assistant_id_idx" ON "public"."assistant_files" USING "btree" ("assistant_id");



CREATE INDEX "assistant_files_file_id_idx" ON "public"."assistant_files" USING "btree" ("file_id");



CREATE INDEX "assistant_files_user_id_idx" ON "public"."assistant_files" USING "btree" ("user_id");



CREATE INDEX "assistant_tools_assistant_id_idx" ON "public"."assistant_tools" USING "btree" ("assistant_id");



CREATE INDEX "assistant_tools_tool_id_idx" ON "public"."assistant_tools" USING "btree" ("tool_id");



CREATE INDEX "assistant_tools_user_id_idx" ON "public"."assistant_tools" USING "btree" ("user_id");



CREATE INDEX "assistant_workspaces_assistant_id_idx" ON "public"."assistant_workspaces" USING "btree" ("assistant_id");



CREATE INDEX "assistant_workspaces_user_id_idx" ON "public"."assistant_workspaces" USING "btree" ("user_id");



CREATE INDEX "assistant_workspaces_workspace_id_idx" ON "public"."assistant_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "assistants_user_id_idx" ON "public"."assistants" USING "btree" ("user_id");



CREATE INDEX "collection_workspaces_collection_id_idx" ON "public"."collection_workspaces" USING "btree" ("collection_id");



CREATE INDEX "collection_workspaces_user_id_idx" ON "public"."collection_workspaces" USING "btree" ("user_id");



CREATE INDEX "collection_workspaces_workspace_id_idx" ON "public"."collection_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "collections_user_id_idx" ON "public"."collections" USING "btree" ("user_id");



CREATE INDEX "file_items_content_fts_idx" ON "public"."file_items" USING "gin" ("content_fts");



CREATE INDEX "file_items_file_id_idx" ON "public"."file_items" USING "btree" ("file_id");



CREATE INDEX "file_items_local_embedding_idx" ON "public"."file_items" USING "hnsw" ("local_embedding" "public"."vector_cosine_ops");



CREATE INDEX "file_workspaces_file_id_idx" ON "public"."file_workspaces" USING "btree" ("file_id");



CREATE INDEX "file_workspaces_user_id_idx" ON "public"."file_workspaces" USING "btree" ("user_id");



CREATE INDEX "file_workspaces_workspace_id_idx" ON "public"."file_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "files_user_id_idx" ON "public"."files" USING "btree" ("user_id");



CREATE INDEX "folders_user_id_idx" ON "public"."folders" USING "btree" ("user_id");



CREATE INDEX "folders_workspace_id_idx" ON "public"."folders" USING "btree" ("workspace_id");



CREATE INDEX "idx_chat_files_chat_id" ON "public"."chat_files" USING "btree" ("chat_id");



CREATE INDEX "idx_chats_user_id" ON "public"."chats" USING "btree" ("user_id");



CREATE INDEX "idx_chats_workspace_id" ON "public"."chats" USING "btree" ("workspace_id");



CREATE INDEX "idx_collection_files_collection_id" ON "public"."collection_files" USING "btree" ("collection_id");



CREATE INDEX "idx_collection_files_file_id" ON "public"."collection_files" USING "btree" ("file_id");



CREATE INDEX "idx_message_file_items_message_id" ON "public"."message_file_items" USING "btree" ("message_id");



CREATE INDEX "idx_messages_chat_id" ON "public"."messages" USING "btree" ("chat_id");



CREATE INDEX "idx_profiles_user_id" ON "public"."profiles" USING "btree" ("user_id");



CREATE UNIQUE INDEX "idx_unique_home_workspace_per_user" ON "public"."workspaces" USING "btree" ("user_id") WHERE "is_home";



CREATE INDEX "idx_workspaces_user_id" ON "public"."workspaces" USING "btree" ("user_id");



CREATE INDEX "model_workspaces_model_id_idx" ON "public"."model_workspaces" USING "btree" ("model_id");



CREATE INDEX "model_workspaces_user_id_idx" ON "public"."model_workspaces" USING "btree" ("user_id");



CREATE INDEX "model_workspaces_workspace_id_idx" ON "public"."model_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "models_user_id_idx" ON "public"."models" USING "btree" ("user_id");



CREATE INDEX "preset_workspaces_preset_id_idx" ON "public"."preset_workspaces" USING "btree" ("preset_id");



CREATE INDEX "preset_workspaces_user_id_idx" ON "public"."preset_workspaces" USING "btree" ("user_id");



CREATE INDEX "preset_workspaces_workspace_id_idx" ON "public"."preset_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "presets_user_id_idx" ON "public"."presets" USING "btree" ("user_id");



CREATE INDEX "prompt_workspaces_prompt_id_idx" ON "public"."prompt_workspaces" USING "btree" ("prompt_id");



CREATE INDEX "prompt_workspaces_user_id_idx" ON "public"."prompt_workspaces" USING "btree" ("user_id");



CREATE INDEX "prompt_workspaces_workspace_id_idx" ON "public"."prompt_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "prompts_user_id_idx" ON "public"."prompts" USING "btree" ("user_id");



CREATE INDEX "tool_workspaces_tool_id_idx" ON "public"."tool_workspaces" USING "btree" ("tool_id");



CREATE INDEX "tool_workspaces_user_id_idx" ON "public"."tool_workspaces" USING "btree" ("user_id");



CREATE INDEX "tool_workspaces_workspace_id_idx" ON "public"."tool_workspaces" USING "btree" ("workspace_id");



CREATE INDEX "tools_user_id_idx" ON "public"."tools" USING "btree" ("user_id");



CREATE OR REPLACE TRIGGER "delete_old_assistant_image" AFTER DELETE ON "public"."assistants" FOR EACH ROW EXECUTE FUNCTION "public"."delete_old_assistant_image"();



CREATE OR REPLACE TRIGGER "delete_old_file" BEFORE DELETE ON "public"."files" FOR EACH ROW EXECUTE FUNCTION "public"."delete_old_file"();



CREATE OR REPLACE TRIGGER "delete_old_message_images" AFTER DELETE ON "public"."messages" FOR EACH ROW EXECUTE FUNCTION "public"."delete_old_message_images"();



CREATE OR REPLACE TRIGGER "delete_old_profile_image" AFTER DELETE ON "public"."profiles" FOR EACH ROW EXECUTE FUNCTION "public"."delete_old_profile_image"();



CREATE OR REPLACE TRIGGER "delete_old_workspace_image" AFTER DELETE ON "public"."workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."delete_old_workspace_image"();



CREATE OR REPLACE TRIGGER "prevent_update_of_home_field" BEFORE UPDATE ON "public"."workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."prevent_home_field_update"();



CREATE OR REPLACE TRIGGER "update_assistant_collections_updated_at" BEFORE UPDATE ON "public"."assistant_collections" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_assistant_files_updated_at" BEFORE UPDATE ON "public"."assistant_files" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_assistant_tools_updated_at" BEFORE UPDATE ON "public"."assistant_tools" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_assistant_workspaces_updated_at" BEFORE UPDATE ON "public"."assistant_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_assistants_updated_at" BEFORE UPDATE ON "public"."assistants" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_chat_files_updated_at" BEFORE UPDATE ON "public"."chat_files" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_chats_updated_at" BEFORE UPDATE ON "public"."chats" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_collection_files_updated_at" BEFORE UPDATE ON "public"."collection_files" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_collection_workspaces_updated_at" BEFORE UPDATE ON "public"."collection_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_collections_updated_at" BEFORE UPDATE ON "public"."collections" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_file_workspaces_updated_at" BEFORE UPDATE ON "public"."file_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_files_updated_at" BEFORE UPDATE ON "public"."files" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_folders_updated_at" BEFORE UPDATE ON "public"."folders" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_message_file_items_updated_at" BEFORE UPDATE ON "public"."message_file_items" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_messages_updated_at" BEFORE UPDATE ON "public"."messages" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_model_workspaces_updated_at" BEFORE UPDATE ON "public"."model_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_models_updated_at" BEFORE UPDATE ON "public"."models" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_preset_workspaces_updated_at" BEFORE UPDATE ON "public"."preset_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_presets_updated_at" BEFORE UPDATE ON "public"."presets" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_profiles_updated_at" BEFORE UPDATE ON "public"."file_items" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_profiles_updated_at" BEFORE UPDATE ON "public"."profiles" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_prompt_workspaces_updated_at" BEFORE UPDATE ON "public"."prompt_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_prompts_updated_at" BEFORE UPDATE ON "public"."prompts" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_tool_workspaces_updated_at" BEFORE UPDATE ON "public"."tool_workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_tools_updated_at" BEFORE UPDATE ON "public"."tools" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



CREATE OR REPLACE TRIGGER "update_workspaces_updated_at" BEFORE UPDATE ON "public"."workspaces" FOR EACH ROW EXECUTE FUNCTION "public"."update_updated_at_column"();



ALTER TABLE ONLY "public"."assistant_collections"
    ADD CONSTRAINT "assistant_collections_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_collections"
    ADD CONSTRAINT "assistant_collections_collection_id_fkey" FOREIGN KEY ("collection_id") REFERENCES "public"."collections"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_collections"
    ADD CONSTRAINT "assistant_collections_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_files"
    ADD CONSTRAINT "assistant_files_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_files"
    ADD CONSTRAINT "assistant_files_file_id_fkey" FOREIGN KEY ("file_id") REFERENCES "public"."files"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_files"
    ADD CONSTRAINT "assistant_files_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_tools"
    ADD CONSTRAINT "assistant_tools_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_tools"
    ADD CONSTRAINT "assistant_tools_tool_id_fkey" FOREIGN KEY ("tool_id") REFERENCES "public"."tools"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_tools"
    ADD CONSTRAINT "assistant_tools_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_workspaces"
    ADD CONSTRAINT "assistant_workspaces_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_workspaces"
    ADD CONSTRAINT "assistant_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistant_workspaces"
    ADD CONSTRAINT "assistant_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."assistants"
    ADD CONSTRAINT "assistants_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."assistants"
    ADD CONSTRAINT "assistants_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chat_files"
    ADD CONSTRAINT "chat_files_chat_id_fkey" FOREIGN KEY ("chat_id") REFERENCES "public"."chats"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chat_files"
    ADD CONSTRAINT "chat_files_file_id_fkey" FOREIGN KEY ("file_id") REFERENCES "public"."files"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chat_files"
    ADD CONSTRAINT "chat_files_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chats"
    ADD CONSTRAINT "chats_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chats"
    ADD CONSTRAINT "chats_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."chats"
    ADD CONSTRAINT "chats_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."chats"
    ADD CONSTRAINT "chats_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_files"
    ADD CONSTRAINT "collection_files_collection_id_fkey" FOREIGN KEY ("collection_id") REFERENCES "public"."collections"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_files"
    ADD CONSTRAINT "collection_files_file_id_fkey" FOREIGN KEY ("file_id") REFERENCES "public"."files"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_files"
    ADD CONSTRAINT "collection_files_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_workspaces"
    ADD CONSTRAINT "collection_workspaces_collection_id_fkey" FOREIGN KEY ("collection_id") REFERENCES "public"."collections"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_workspaces"
    ADD CONSTRAINT "collection_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collection_workspaces"
    ADD CONSTRAINT "collection_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."collections"
    ADD CONSTRAINT "collections_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."collections"
    ADD CONSTRAINT "collections_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."file_items"
    ADD CONSTRAINT "file_items_file_id_fkey" FOREIGN KEY ("file_id") REFERENCES "public"."files"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."file_items"
    ADD CONSTRAINT "file_items_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."file_workspaces"
    ADD CONSTRAINT "file_workspaces_file_id_fkey" FOREIGN KEY ("file_id") REFERENCES "public"."files"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."file_workspaces"
    ADD CONSTRAINT "file_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."file_workspaces"
    ADD CONSTRAINT "file_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."files"
    ADD CONSTRAINT "files_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."files"
    ADD CONSTRAINT "files_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."folders"
    ADD CONSTRAINT "folders_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."folders"
    ADD CONSTRAINT "folders_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."message_file_items"
    ADD CONSTRAINT "message_file_items_file_item_id_fkey" FOREIGN KEY ("file_item_id") REFERENCES "public"."file_items"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."message_file_items"
    ADD CONSTRAINT "message_file_items_message_id_fkey" FOREIGN KEY ("message_id") REFERENCES "public"."messages"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."message_file_items"
    ADD CONSTRAINT "message_file_items_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_assistant_id_fkey" FOREIGN KEY ("assistant_id") REFERENCES "public"."assistants"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_chat_id_fkey" FOREIGN KEY ("chat_id") REFERENCES "public"."chats"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."model_workspaces"
    ADD CONSTRAINT "model_workspaces_model_id_fkey" FOREIGN KEY ("model_id") REFERENCES "public"."models"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."model_workspaces"
    ADD CONSTRAINT "model_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."model_workspaces"
    ADD CONSTRAINT "model_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."models"
    ADD CONSTRAINT "models_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."models"
    ADD CONSTRAINT "models_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."preset_workspaces"
    ADD CONSTRAINT "preset_workspaces_preset_id_fkey" FOREIGN KEY ("preset_id") REFERENCES "public"."presets"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."preset_workspaces"
    ADD CONSTRAINT "preset_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."preset_workspaces"
    ADD CONSTRAINT "preset_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."presets"
    ADD CONSTRAINT "presets_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."presets"
    ADD CONSTRAINT "presets_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."prompt_workspaces"
    ADD CONSTRAINT "prompt_workspaces_prompt_id_fkey" FOREIGN KEY ("prompt_id") REFERENCES "public"."prompts"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."prompt_workspaces"
    ADD CONSTRAINT "prompt_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."prompt_workspaces"
    ADD CONSTRAINT "prompt_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."prompts"
    ADD CONSTRAINT "prompts_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."prompts"
    ADD CONSTRAINT "prompts_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."tool_workspaces"
    ADD CONSTRAINT "tool_workspaces_tool_id_fkey" FOREIGN KEY ("tool_id") REFERENCES "public"."tools"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."tool_workspaces"
    ADD CONSTRAINT "tool_workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."tool_workspaces"
    ADD CONSTRAINT "tool_workspaces_workspace_id_fkey" FOREIGN KEY ("workspace_id") REFERENCES "public"."workspaces"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."tools"
    ADD CONSTRAINT "tools_folder_id_fkey" FOREIGN KEY ("folder_id") REFERENCES "public"."folders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."tools"
    ADD CONSTRAINT "tools_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."workspaces"
    ADD CONSTRAINT "workspaces_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "auth"."users"("id") ON DELETE CASCADE;



CREATE POLICY "Allow full access to own assistant_collections" ON "public"."assistant_collections" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own assistant_files" ON "public"."assistant_files" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own assistant_tools" ON "public"."assistant_tools" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own assistant_workspaces" ON "public"."assistant_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own assistants" ON "public"."assistants" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own chat_files" ON "public"."chat_files" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own chats" ON "public"."chats" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own collection_files" ON "public"."collection_files" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own collection_workspaces" ON "public"."collection_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own collections" ON "public"."collections" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own file items" ON "public"."file_items" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own file_workspaces" ON "public"."file_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own files" ON "public"."files" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own folders" ON "public"."folders" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own message_file_items" ON "public"."message_file_items" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own messages" ON "public"."messages" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own model_workspaces" ON "public"."model_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own models" ON "public"."models" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own preset_workspaces" ON "public"."preset_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own presets" ON "public"."presets" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own profiles" ON "public"."profiles" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own prompt_workspaces" ON "public"."prompt_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own prompts" ON "public"."prompts" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own tool_workspaces" ON "public"."tool_workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own tools" ON "public"."tools" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow full access to own workspaces" ON "public"."workspaces" USING (("user_id" = "auth"."uid"())) WITH CHECK (("user_id" = "auth"."uid"()));



CREATE POLICY "Allow view access to collection files for non-private collectio" ON "public"."collection_files" FOR SELECT USING (("collection_id" IN ( SELECT "collections"."id"
   FROM "public"."collections"
  WHERE ("collections"."sharing" <> 'private'::"text"))));



CREATE POLICY "Allow view access to files for non-private collections" ON "public"."files" FOR SELECT USING (("id" IN ( SELECT "collection_files"."file_id"
   FROM "public"."collection_files"
  WHERE ("collection_files"."collection_id" IN ( SELECT "collections"."id"
           FROM "public"."collections"
          WHERE ("collections"."sharing" <> 'private'::"text"))))));



CREATE POLICY "Allow view access to messages for non-private chats" ON "public"."messages" FOR SELECT USING (("chat_id" IN ( SELECT "chats"."id"
   FROM "public"."chats"
  WHERE ("chats"."sharing" <> 'private'::"text"))));



CREATE POLICY "Allow view access to non-private assistants" ON "public"."assistants" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private chats" ON "public"."chats" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private collections" ON "public"."collections" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private file items" ON "public"."file_items" FOR SELECT USING (("file_id" IN ( SELECT "files"."id"
   FROM "public"."files"
  WHERE ("files"."sharing" <> 'private'::"text"))));



CREATE POLICY "Allow view access to non-private files" ON "public"."files" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private models" ON "public"."models" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private presets" ON "public"."presets" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private prompts" ON "public"."prompts" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private tools" ON "public"."tools" FOR SELECT USING (("sharing" <> 'private'::"text"));



CREATE POLICY "Allow view access to non-private workspaces" ON "public"."workspaces" FOR SELECT USING (("sharing" <> 'private'::"text"));



ALTER TABLE "public"."assistant_collections" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assistant_files" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assistant_tools" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assistant_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."assistants" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."chat_files" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."chats" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."collection_files" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."collection_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."collections" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."file_items" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."file_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."files" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."folders" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."message_file_items" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."messages" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."model_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."models" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."preset_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."presets" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."profiles" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."prompt_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."prompts" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."tool_workspaces" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."tools" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."workspaces" ENABLE ROW LEVEL SECURITY;


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT ALL ON FUNCTION "public"."create_duplicate_messages_for_new_chat"("old_chat_id" "uuid", "new_chat_id" "uuid", "new_user_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."create_duplicate_messages_for_new_chat"("old_chat_id" "uuid", "new_chat_id" "uuid", "new_user_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."create_duplicate_messages_for_new_chat"("old_chat_id" "uuid", "new_chat_id" "uuid", "new_user_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."create_profile_and_workspace"() TO "anon";
GRANT ALL ON FUNCTION "public"."create_profile_and_workspace"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."create_profile_and_workspace"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_message_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."delete_message_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_message_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_messages_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."delete_messages_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_messages_including_and_after"("p_user_id" "uuid", "p_chat_id" "uuid", "p_sequence_number" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_old_assistant_image"() TO "anon";
GRANT ALL ON FUNCTION "public"."delete_old_assistant_image"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_old_assistant_image"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_old_file"() TO "anon";
GRANT ALL ON FUNCTION "public"."delete_old_file"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_old_file"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_old_message_images"() TO "anon";
GRANT ALL ON FUNCTION "public"."delete_old_message_images"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_old_message_images"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_old_profile_image"() TO "anon";
GRANT ALL ON FUNCTION "public"."delete_old_profile_image"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_old_profile_image"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_old_workspace_image"() TO "anon";
GRANT ALL ON FUNCTION "public"."delete_old_workspace_image"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_old_workspace_image"() TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_storage_object"("bucket" "text", "object" "text", OUT "status" integer, OUT "content" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."delete_storage_object"("bucket" "text", "object" "text", OUT "status" integer, OUT "content" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_storage_object"("bucket" "text", "object" "text", OUT "status" integer, OUT "content" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."delete_storage_object_from_bucket"("bucket_name" "text", "object_path" "text", OUT "status" integer, OUT "content" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."delete_storage_object_from_bucket"("bucket_name" "text", "object_path" "text", OUT "status" integer, OUT "content" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."delete_storage_object_from_bucket"("bucket_name" "text", "object_path" "text", OUT "status" integer, OUT "content" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_all_file_names"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_all_file_names"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_all_file_names"() TO "service_role";



GRANT ALL ON FUNCTION "public"."get_gdrive_file_names"() TO "anon";
GRANT ALL ON FUNCTION "public"."get_gdrive_file_names"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_gdrive_file_names"() TO "service_role";



GRANT ALL ON FUNCTION "public"."match_file_items_fts"("keyword_query" "text", "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."match_file_items_fts"("keyword_query" "text", "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."match_file_items_fts"("keyword_query" "text", "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."match_file_items_local"("query_embedding" "public"."vector", "match_count" integer, "file_ids" "uuid"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."match_file_items_local"("query_embedding" "public"."vector", "match_count" integer, "file_ids" "uuid"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."match_file_items_local"("query_embedding" "public"."vector", "match_count" integer, "file_ids" "uuid"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."match_file_items_openai"("query_embedding" "public"."vector", "match_threshold" double precision, "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."match_file_items_openai"("query_embedding" "public"."vector", "match_threshold" double precision, "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."match_file_items_openai"("query_embedding" "public"."vector", "match_threshold" double precision, "match_count" integer, "file_ids" "uuid"[], "filter_file_name" "text", "filter_description" "text", "filter_document_type" "text", "filter_meeting_year" integer, "filter_meeting_month" integer, "filter_meeting_month_name" "text", "filter_meeting_day" integer, "filter_ordinance_title" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."non_private_assistant_exists"("p_name" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."non_private_assistant_exists"("p_name" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."non_private_assistant_exists"("p_name" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."non_private_file_exists"("p_name" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."non_private_file_exists"("p_name" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."non_private_file_exists"("p_name" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."non_private_workspace_exists"("p_name" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."non_private_workspace_exists"("p_name" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."non_private_workspace_exists"("p_name" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."prevent_home_field_update"() TO "anon";
GRANT ALL ON FUNCTION "public"."prevent_home_field_update"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."prevent_home_field_update"() TO "service_role";



GRANT ALL ON FUNCTION "public"."prevent_home_workspace_deletion"() TO "anon";
GRANT ALL ON FUNCTION "public"."prevent_home_workspace_deletion"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."prevent_home_workspace_deletion"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_updated_at_column"() TO "service_role";



GRANT ALL ON TABLE "public"."assistant_collections" TO "anon";
GRANT ALL ON TABLE "public"."assistant_collections" TO "authenticated";
GRANT ALL ON TABLE "public"."assistant_collections" TO "service_role";



GRANT ALL ON TABLE "public"."assistant_files" TO "anon";
GRANT ALL ON TABLE "public"."assistant_files" TO "authenticated";
GRANT ALL ON TABLE "public"."assistant_files" TO "service_role";



GRANT ALL ON TABLE "public"."assistant_tools" TO "anon";
GRANT ALL ON TABLE "public"."assistant_tools" TO "authenticated";
GRANT ALL ON TABLE "public"."assistant_tools" TO "service_role";



GRANT ALL ON TABLE "public"."assistant_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."assistant_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."assistant_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."assistants" TO "anon";
GRANT ALL ON TABLE "public"."assistants" TO "authenticated";
GRANT ALL ON TABLE "public"."assistants" TO "service_role";



GRANT ALL ON TABLE "public"."chat_files" TO "anon";
GRANT ALL ON TABLE "public"."chat_files" TO "authenticated";
GRANT ALL ON TABLE "public"."chat_files" TO "service_role";



GRANT ALL ON TABLE "public"."chats" TO "anon";
GRANT ALL ON TABLE "public"."chats" TO "authenticated";
GRANT ALL ON TABLE "public"."chats" TO "service_role";



GRANT ALL ON TABLE "public"."collection_files" TO "anon";
GRANT ALL ON TABLE "public"."collection_files" TO "authenticated";
GRANT ALL ON TABLE "public"."collection_files" TO "service_role";



GRANT ALL ON TABLE "public"."collection_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."collection_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."collection_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."collections" TO "anon";
GRANT ALL ON TABLE "public"."collections" TO "authenticated";
GRANT ALL ON TABLE "public"."collections" TO "service_role";



GRANT ALL ON TABLE "public"."file_items" TO "anon";
GRANT ALL ON TABLE "public"."file_items" TO "authenticated";
GRANT ALL ON TABLE "public"."file_items" TO "service_role";



GRANT ALL ON TABLE "public"."file_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."file_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."file_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."files" TO "anon";
GRANT ALL ON TABLE "public"."files" TO "authenticated";
GRANT ALL ON TABLE "public"."files" TO "service_role";



GRANT ALL ON TABLE "public"."folders" TO "anon";
GRANT ALL ON TABLE "public"."folders" TO "authenticated";
GRANT ALL ON TABLE "public"."folders" TO "service_role";



GRANT ALL ON TABLE "public"."message_file_items" TO "anon";
GRANT ALL ON TABLE "public"."message_file_items" TO "authenticated";
GRANT ALL ON TABLE "public"."message_file_items" TO "service_role";



GRANT ALL ON TABLE "public"."messages" TO "anon";
GRANT ALL ON TABLE "public"."messages" TO "authenticated";
GRANT ALL ON TABLE "public"."messages" TO "service_role";



GRANT ALL ON TABLE "public"."model_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."model_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."model_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."models" TO "anon";
GRANT ALL ON TABLE "public"."models" TO "authenticated";
GRANT ALL ON TABLE "public"."models" TO "service_role";



GRANT ALL ON TABLE "public"."preset_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."preset_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."preset_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."presets" TO "anon";
GRANT ALL ON TABLE "public"."presets" TO "authenticated";
GRANT ALL ON TABLE "public"."presets" TO "service_role";



GRANT ALL ON TABLE "public"."profiles" TO "anon";
GRANT ALL ON TABLE "public"."profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."profiles" TO "service_role";



GRANT ALL ON TABLE "public"."prompt_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."prompt_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."prompt_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."prompts" TO "anon";
GRANT ALL ON TABLE "public"."prompts" TO "authenticated";
GRANT ALL ON TABLE "public"."prompts" TO "service_role";



GRANT ALL ON TABLE "public"."tool_workspaces" TO "anon";
GRANT ALL ON TABLE "public"."tool_workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."tool_workspaces" TO "service_role";



GRANT ALL ON TABLE "public"."tools" TO "anon";
GRANT ALL ON TABLE "public"."tools" TO "authenticated";
GRANT ALL ON TABLE "public"."tools" TO "service_role";



GRANT ALL ON TABLE "public"."workspaces" TO "anon";
GRANT ALL ON TABLE "public"."workspaces" TO "authenticated";
GRANT ALL ON TABLE "public"."workspaces" TO "service_role";



ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";







RESET ALL;
