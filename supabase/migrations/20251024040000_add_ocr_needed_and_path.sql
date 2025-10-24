ALTER TABLE files
ADD COLUMN ocr_needed BOOLEAN DEFAULT FALSE,
ADD COLUMN ocr_text_path TEXT;

COMMENT ON COLUMN files.ocr_needed IS 'Indicates if the file is a candidate for OCR processing.';
COMMENT ON COLUMN files.ocr_text_path IS 'Path to the stored text file after OCR.';
