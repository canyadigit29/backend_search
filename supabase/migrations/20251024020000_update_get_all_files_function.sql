DROP FUNCTION IF EXISTS public.get_all_file_names();

CREATE OR REPLACE FUNCTION get_all_file_names()
RETURNS TABLE(id UUID, name TEXT, file_path TEXT) AS $$
BEGIN
  RETURN QUERY SELECT f.id, f.file_name AS name, f.file_path FROM public.files f;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;