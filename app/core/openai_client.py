from openai import OpenAI
import os
import time

client = OpenAI()


def chat_completion(messages: list, model: str = "gpt-5", max_tokens: int | None = None) -> str:
    """Synchronous chat completion that returns the full message content or an error string."""
    try:
        kwargs = {"model": model, "messages": messages}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {str(e)}"


def stream_chat_completion(messages: list, model: str = "gpt-5", max_seconds: float = 50.0, max_tokens: int | None = None) -> tuple[str, bool]:
    """
    Stream chat completion and return accumulated text within a time budget.
    Returns (content, was_partial) where was_partial=True if we stopped due to timeout or error.
    """
    start = time.time()
    content_parts: list[str] = []
    last_finish_reason = None
    try:
        # include_usage is supported by newer SDK versions; ignore if not available
        kwargs = {"model": model, "messages": messages, "stream": True}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            kwargs["stream_options"] = {"include_usage": True}
        except Exception:
            pass
        stream = client.chat.completions.create(**kwargs)
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
                # Capture finish_reason when present on streamed chunks
                fr = getattr(choice, "finish_reason", None)
                if fr:
                    last_finish_reason = fr
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
                # Timeout considered partial
                return ("".join(content_parts).strip(), True)

        # Completed naturally within time or without max_seconds guard
        text = "".join(content_parts).strip()
        # Consider partial if model stopped due to length or content filter
        was_partial = last_finish_reason in ("length", "content_filter")
        return (text, bool(was_partial))
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
