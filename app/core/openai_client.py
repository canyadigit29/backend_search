
from openai import OpenAI

client = OpenAI()

def embed_text(text: str) -> list:
    try:
        response = client.embeddings.create(
            input=[text],
            model="text-embedding-ada-002"
        )
        return response.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Embedding failed: {str(e)}")
