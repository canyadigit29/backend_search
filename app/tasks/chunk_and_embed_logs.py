import os
import re
import io
import logging
from pypdf import PdfReader
from app.core.openai_client import embed_text
from app.core.supabase_client import supabase

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clean_text(text):
    """Removes common PDF artifacts like page numbers and repeated headers/footers."""
    # Remove page numbers (e.g., "Page X of Y" or just "X")
    text = re.sub(r'Page\s+\d+\s+of\s+\d+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
    # Add more specific cleaning rules here as you identify them
    return text.strip()

def get_chunks(text, file_name):
    """
    Chunks text based on municipal document structures.
    - Primary strategy: Split by structural markers (Section, Article, Agenda items).
    - Fallback: Split by paragraphs.
    - Enriches chunks with metadata.
    """
    chunks = []
    
    # Try to identify document type from filename
    doc_type = "unknown"
    if "ordinance" in file_name.lower():
        doc_type = "ordinance"
    elif "agenda" in file_name.lower():
        doc_type = "agenda"
    elif "minute" in file_name.lower():
        doc_type = "minutes"

    # Regex for structural markers
    # This pattern looks for "Section X", "Article Y", "1.", "A.", etc. at the start of a line
    structural_markers = re.compile(r'(^\s*(Section|Article|§)\s+[\w\d\.]+|^\s*\d+\.\s+|^\s*[A-Z]\.\s+)', re.MULTILINE)
    
    # Split by structural markers if found, otherwise split by double newlines (paragraphs)
    potential_splits = structural_markers.split(text)
    if len(potential_splits) > 1:
        # Re-combine the marker with its content
        splits = []
        i = 1
        while i < len(potential_splits):
            marker = potential_splits[i]
            content = potential_splits[i+2] if i+2 < len(potential_splits) else ""
            splits.append(marker + content)
            i += 3
    else:
        splits = text.split('\n\n')

    # Process each split into a chunk
    for split_text in splits:
        if not split_text.strip():
            continue
        
        # Prepend metadata to the chunk content
        enriched_content = f"Source: {file_name} (Type: {doc_type})\n\n{split_text}"
        
        # Simple overlap: for now, we are not adding sentence overlap to keep it clean,
        # but this is where it would be implemented if needed.
        chunks.append(enriched_content)
        
    return chunks

async def process_unindexed_files():
    """
    Main function for the ingestion worker.
    - Fetches un-ingested files from the 'files' table.
    - Extracts text, cleans it, and chunks it based on document structure.
    - Embeds the chunks and saves them to 'document_chunks'.
    - Marks the original file as ingested.
    """
    logger.info("Checking for un-ingested files...")
    
    try:
        unindexed_files_response = supabase.table("files").select("*").eq("ingested", False).execute()
        if not unindexed_files_response.data:
            logger.info("No new files to ingest.")
            return

        files_to_process = unindexed_files_response.data
        logger.info(f"Found {len(files_to_process)} files to ingest.")

        for file_data in files_to_process:
            file_id = file_data['id']
            file_path = file_data['file_path']
            file_name = file_data['file_name']
            logger.info(f"Processing file: {file_name} (ID: {file_id})")

            try:
                # 1. Download file from storage
                file_content_bytes = supabase.storage.from_("files").download(file_path)
                file_stream = io.BytesIO(file_content_bytes)

                # 2. Extract text using pypdf
                reader = PdfReader(file_stream)
                full_text = ""
                for page in reader.pages:
                    full_text += page.extract_text() + "\n"
                
                # 3. Clean and chunk the text
                cleaned_text = clean_text(full_text)
                chunks = get_chunks(cleaned_text, file_name)
                logger.info(f"  - Extracted and split file into {len(chunks)} chunks.")

                # 4. Embed and insert each chunk
                for i, chunk_text in enumerate(chunks):
                    embedding = embed_text(chunk_text)
                    supabase.table("document_chunks").insert({
                        "file_id": file_id,
                        "chunk_index": i,
                        "content": chunk_text,
                        "embedding": embedding,
                    }).execute()
                logger.info(f"  - Successfully embedded and saved {len(chunks)} chunks.")

                # 5. Mark the file as ingested
                supabase.table("files").update({"ingested": True}).eq("id", file_id).execute()
                logger.info(f"✅ Successfully marked file as ingested: {file_name}")

            except Exception as e:
                logger.error(f"❌ Failed to process file {file_name}: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"An error occurred in the main processing loop: {e}", exc_info=True)

    logger.info("Ingestion cycle complete.")

