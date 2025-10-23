import asyncio
from app.api.gdrive_ops.sync import run_google_drive_sync

async def main():
    print("Starting scheduled Google Drive sync task...")
    result = await run_google_drive_sync()
    print(f"Google Drive sync task finished with result: {result}")

if __name__ == "__main__":
    asyncio.run(main())
