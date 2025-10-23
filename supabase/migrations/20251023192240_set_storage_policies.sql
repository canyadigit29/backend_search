CREATE POLICY "Allow public read access"
ON storage.objects FOR SELECT
USING ( bucket_id = 'files' );

CREATE POLICY "Allow authenticated uploads"
ON storage.objects FOR INSERT
WITH CHECK ( bucket_id = 'files' AND auth.role() = 'authenticated' );

CREATE POLICY "Allow authenticated deletes"
ON storage.objects FOR DELETE
USING ( bucket_id = 'files' AND auth.uid() = owner );
