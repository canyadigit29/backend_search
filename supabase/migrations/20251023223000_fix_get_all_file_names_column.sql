CREATE OR REPLACE FUNCTION get_all_file_names()
RETURNS TABLE(name TEXT) AS $$
BEGIN
  RETURN QUERY SELECT f.file_name AS name FROM files f;
END;
$$ LANGUAGE plpgsql;

-- Re-apply the security definer setting to be safe
ALTER FUNCTION public.get_all_file_names() SECURITY DEFINER;
