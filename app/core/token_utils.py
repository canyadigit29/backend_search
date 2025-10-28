import os
from typing import List, Optional

import tiktoken


def get_encoding(model: Optional[str] = None):
    """Return a reasonable encoding for chat models; prefer o200k_base for large-context models."""
    # Heuristic mapping for newer large-context models that tiktoken may not yet recognize by name.
    try:
        if model:
            lowered = str(model).lower()
            # Prefer o200k_base for models like gpt-4o and gpt-5 family which commonly use that tokenizer
            if ("gpt-4o" in lowered) or ("gpt-5" in lowered) or ("o4" in lowered):
                return tiktoken.get_encoding("o200k_base")
            return tiktoken.encoding_for_model(model)
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Fallback if the specific model tokenizer isn't available
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")


def trim_texts_to_token_limit(texts: List[str], max_tokens: int, model: Optional[str] = None, separator: str = "\n\n") -> str:
    """
    Concatenate texts with a separator up to a token budget; trims the last text if needed.
    Returns the concatenated string.
    """
    enc = get_encoding(model)
    sep_tokens = enc.encode(separator)
    sep_len = len(sep_tokens)

    out_tokens: list[int] = []
    first = True
    for txt in texts:
        tks = enc.encode(txt or "")
        # Add separator if not first
        add_sep = (not first)
        needed = (sep_len if add_sep else 0) + len(tks)
        if (len(out_tokens) + needed) <= max_tokens:
            if add_sep:
                out_tokens.extend(sep_tokens)
            out_tokens.extend(tks)
            first = False
            continue
        # Not enough space for full text; try partial fit
        space = max_tokens - len(out_tokens) - (sep_len if add_sep else 0)
        if space > 0:
            if add_sep:
                out_tokens.extend(sep_tokens)
            out_tokens.extend(tks[:space])
        break

    return enc.decode(out_tokens)
