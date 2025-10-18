from openai import OpenAI

client = OpenAI()


def chat_completion(messages: list, model: str = "gpt-5") -> str:
    try:
        response = client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def embed_text(text: str) -> list:
    try:
        response = client.embeddings.create(
            input=[text], model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Embedding failed: {str(e)}")
