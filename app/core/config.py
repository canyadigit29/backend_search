import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # 🔧 Project metadata
    PROJECT_NAME: str = "MaxGPT Backend"
    API_PREFIX: str = "/api"

    # 🔐 Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip()
    SUPABASE_SERVICE_ROLE: str = os.getenv("SUPABASE_SERVICE_ROLE", "").strip()
    SUPABASE_STORAGE_BUCKET: str = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()

    # 🤖 OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-ada-002"
    HUB_ASSISTANT_ID: str = os.getenv("HUB_ASSISTANT_ID", "").strip()
    SEARCH_ASSISTANT_ID: str = os.getenv("SEARCH_ASSISTANT_ID", "").strip()

    # 📄 Google Drive
    GOOGLE_CREDENTIALS_BASE64: str = os.getenv("GOOGLE_CREDENTIALS_BASE64", "").strip()
    GOOGLE_ADMIN_EMAIL: str = os.getenv("GOOGLE_ADMIN_EMAIL", "").strip()
    GOOGLE_DRIVE_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    GDRIVE_SYNC_INTERVAL_MINUTES: int = int(os.getenv("GDRIVE_SYNC_INTERVAL_MINUTES", "60").strip() or 60)
    # Workspace/Vector Store targeting for Responses GDrive sync
    GDRIVE_WORKSPACE_ID: str = os.getenv("GDRIVE_WORKSPACE_ID", "").strip()
    GDRIVE_VECTOR_STORE_ID: str = os.getenv("GDRIVE_VECTOR_STORE_ID", "").strip()
    # Vector Store upload worker tuning
    VS_UPLOAD_DELAY_MS: int = int(os.getenv("VS_UPLOAD_DELAY_MS", "1000").strip() or 1000)
    VS_UPLOAD_BATCH_LIMIT: int = int(os.getenv("VS_UPLOAD_BATCH_LIMIT", "25").strip() or 25)

    # ⚙️ Env
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").strip()
    DEBUG: bool = os.getenv("DEBUG", "True").strip() == "True"

    # 🪵 Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip()
    LOG_JSON: bool = os.getenv("LOG_JSON", "True").strip().lower() in ("1", "true", "yes")
    # Avoid logging large texts by default (e.g., hydrated chunks or summaries)
    DEBUG_VERBOSE_LOG_TEXTS: bool = os.getenv("DEBUG_VERBOSE_LOG_TEXTS", "False").strip().lower() in ("1", "true", "yes")

    # 📝 Summary capture controls
    # If True, log summary text (truncated) to logs under event 'rag.summary.text'
    LOG_SUMMARY_TEXT: bool = os.getenv("LOG_SUMMARY_TEXT", "False").strip().lower() in ("1", "true", "yes")
    # Max characters of summary to log when LOG_SUMMARY_TEXT is enabled
    SUMMARY_TEXT_MAX_CHARS: int = int(os.getenv("SUMMARY_TEXT_MAX_CHARS", "1200").strip() or 1200)
    # If True, attempt to persist full summary and metadata to Supabase table 'rag_summary_results'
    CAPTURE_SUMMARY_TO_DB: bool = os.getenv("CAPTURE_SUMMARY_TO_DB", "False").strip().lower() in ("1", "true", "yes")


settings = Settings()
