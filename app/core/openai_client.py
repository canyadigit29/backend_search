from openai import OpenAI
import time

client = OpenAI()


def chat_completion(messages: list, model: str = "gpt-5") -> str:
    """Synchronous chat completion that returns the full message content or an error string."""
    try:
        response = client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def stream_chat_completion(messages: list, model: str = "gpt-5", max_seconds: float = 50.0) -> tuple[str, bool]:
    """
    Stream chat completion and return accumulated text within a time budget.
    Returns (content, was_partial) where was_partial=True if we stopped due to timeout or error.
    """
    start = time.time()
    content_parts: list[str] = []
    try:
        stream = client.chat.completions.create(model=model, messages=messages, stream=True)
        # Iterate streamed chunks and accumulate text until time budget is exceeded
        for chunk in stream:
            try:
                choice = chunk.choices[0]
                # New SDK: delta may be None-safe
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    piece = getattr(delta, "content", None)
                    if piece:
                        content_parts.append(piece)
                else:
                    # Some SDK variants expose content directly
                    piece = getattr(choice, "message", None)
                    if piece and getattr(piece, "content", None):
                        content_parts.append(piece.content)
            except Exception:
                # Ignore malformed chunks but keep streaming
                pass

            if time.time() - start >= max_seconds:
                # Attempt to close stream gracefully if supported
                try:
                    if hasattr(stream, "close"):
                        stream.close()
                except Exception:
                    pass
                return ("".join(content_parts).strip(), True)

        # Completed naturally within time budget
        return ("".join(content_parts).strip(), False)
    except Exception:
        # If we have partial content, return it as partial; else return error text marked partial
        text = "".join(content_parts).strip()
        if text:
            return (text, True)
        return ("", True)


def embed_text(text: str) -> list:
    try:
        response = client.embeddings.create(
            input=[text], model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Embedding failed: {str(e)}")
