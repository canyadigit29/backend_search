ALTER TABLE public.file_workspaces
ADD COLUMN ingest_retries smallint NOT NULL DEFAULT 0,
ADD COLUMN ingest_failed boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN public.file_workspaces.ingest_retries IS 'Number of times ingestion has been attempted for this file.';
COMMENT ON COLUMN public.file_workspaces.ingest_failed IS 'Set to true if ingestion has failed more than the max retry count.';
