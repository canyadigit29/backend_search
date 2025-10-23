import os
import base64
import json
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from fastapi import HTTPException

from app.core.supabase_client import supabase
from app.core.config import settings
from app.api.file_ops.upload import upload_file_to_supabase

# Define the scopes for Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive']

def get_google_drive_service():
    """
    Authenticates with Google Drive API using service account credentials
    and returns a service object.
    """
    if not settings.GOOGLE_CREDENTIALS_BASE64:
        raise HTTPException(status_code=500, detail="Google credentials are not configured.")
    
    try:
        # Decode the base64 credentials
        creds_json_str = base64.b64decode(settings.GOOGLE_CREDENTIALS_BASE64).decode('utf-8')
        creds_info = json.loads(creds_json_str)

        # Create credentials with the required scopes and subject (for domain-wide delegation)
        creds = service_account.Credentials.from_service_account_info(
            creds_info,
            scopes=SCOPES,
            subject=settings.GOOGLE_ADMIN_EMAIL
        )
        
        # Build the Google Drive service
        service = build('drive', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"Error creating Google Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Google Drive service: {e}")

async def run_google_drive_sync():
    """
    Main function to sync files from a Google Drive folder to Supabase storage.
    """
    print("Starting Google Drive sync...")
    try:
        drive_service = get_google_drive_service()

        # 1. Get list of files from Supabase
        response = supabase.storage.from_("files").list()
        supabase_files = {file['name'] for file in response}
        print(f"Found {len(supabase_files)} files in Supabase storage.")

        # 2. Get list of files from Google Drive folder
        query = f"'{settings.GOOGLE_DRIVE_FOLDER_ID}' in parents and trashed = false"
        results = drive_service.files().list(
            q=query,
            pageSize=100, # Adjust as needed
            fields="nextPageToken, files(id, name)"
        ).execute()
        drive_files = results.get('files', [])
        print(f"Found {len(drive_files)} files in Google Drive folder.")

        new_files_to_upload = []
        for file in drive_files:
            if file['name'] not in supabase_files:
                new_files_to_upload.append(file)
        
        print(f"Found {len(new_files_to_upload)} new files to upload.")

        # 3. Download new files and upload to Supabase
        for file_to_upload in new_files_to_upload:
            file_id = file_to_upload['id']
            file_name = file_to_upload['name']
            print(f"Processing new file: {file_name} (ID: {file_id})")

            request = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
                print(f"Downloading {file_name}: {int(status.progress() * 100)}%.")

            fh.seek(0)
            
            # We pass the content as bytes and the filename
            # The upload function will handle the rest
            await upload_file_to_supabase(file_content=fh.getvalue(), file_name=file_name)
            print(f"Successfully uploaded {file_name} to Supabase and triggered ingestion.")

        print("Google Drive sync completed successfully.")
        return {"status": "success", "new_files_processed": len(new_files_to_upload)}

    except Exception as e:
        print(f"An error occurred during Google Drive sync: {e}")
        # In a real app, you might want to send a notification here
        return {"status": "error", "detail": str(e)}
