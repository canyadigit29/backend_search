import os
from dotenv import load_dotenv

load_dotenv()

def get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Environment variable {name} not found.")
    return value
