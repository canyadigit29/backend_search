import openai
from app.core.config import settings

openai.api_key = settings.OPENAI_API_KEY

def embed_text(text: str) -> list:
    response = openai.Embedding.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text
    )
    return response['data'][0]['embedding']
