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

    # ⚙️ Env
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").strip()
    DEBUG: bool = os.getenv("DEBUG", "True").strip() == "True"


settings = Settings()
