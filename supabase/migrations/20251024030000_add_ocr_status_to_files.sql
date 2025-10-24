ALTER TABLE files
ADD COLUMN ocr_scanned BOOLEAN DEFAULT FALSE,
ADD COLUMN ocr_started_at TIMESTAMPTZ,
ADD COLUMN ocr_completed_at TIMESTAMPTZ;

COMMENT ON COLUMN files.ocr_scanned IS 'Indicates whether the file has been processed by OCR.';
COMMENT ON COLUMN files.ocr_started_at IS 'Timestamp for when OCR processing began.';
COMMENT ON COLUMN files.ocr_completed_at IS 'Timestamp for when OCR processing completed.';
