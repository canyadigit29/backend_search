import logging
import json
from typing import Dict, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# A simple JSON schema to guide the model's output
PROFILES_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise, 2-4 sentence summary of the document's main topics and purpose.",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "A list of 5-10 important keywords or phrases.",
        },
        "entities": {
            "type": "object",
            "properties": {
                "people": {"type": "array", "items": {"type": "string"}},
                "organizations": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
                "dates": {"type": "array", "items": {"type": "string"}},
            },
            "description": "Key named entities mentioned in the document.",
        },
    },
    "required": ["summary", "keywords", "entities"],
}

PROMPT_TEMPLATE = """
You are an expert document analyst.

Task:
- Read the provided document text.
- Produce a JSON object that strictly follows the JSON schema named "document_profile" provided out-of-band by the client.
- Include: a concise summary (2-4 sentences), 5-10 keywords, and named entities (people, organizations, locations, dates arrays).

Important:
- Output ONLY the JSON object (no prose around it).
- If unsure about specific entities, omit them rather than hallucinating.

DOCUMENT TEXT:
---
{document_text}
---
"""


async def generate_profile_from_text(
    text_content: str, client: Optional[AsyncOpenAI] = None
) -> Optional[Dict]:
    """
    Generates a structured document profile (summary, keywords, entities) from raw text content
    using an OpenAI model with JSON mode.

    Args:
        text_content: The text content of the document to profile.
        client: An optional OpenAI client instance.

    Returns:
        A dictionary containing the structured profile, or None if generation fails.
    """
    if not text_content or not text_content.strip():
        logger.warning("[document_profiler] Text content is empty, skipping profile generation.")
        return None

    # Truncate content to fit within a reasonable context window for this task
    # This limit can be tuned based on the model used. 15k chars is a safe starting point.
    max_chars = 15000
    truncated_content = text_content[:max_chars]

    # Skip profiling for very short content to avoid noisy/low-value calls
    min_chars = 400
    if len(truncated_content) < min_chars:
        logger.info(
            f"[document_profiler] Skipping profiling: content too short ({len(truncated_content)} < {min_chars})."
        )
        return None

    if client is None:
        client = AsyncOpenAI()

    try:
        logger.info(
            f"[document_profiler] Generating profile for document content (truncated to {len(truncated_content)} chars)."
        )

        # Prefer plain Responses API (no response_format, no temperature) for widest compatibility
        model = "gpt-5"
        try:
            response = await client.responses.create(
                model=model,
                input=PROMPT_TEMPLATE.format(document_text=truncated_content),
            )
            output_text = getattr(response, "output_text", None)
            if not output_text:
                # Try to assemble from output blocks if output_text missing
                try:
                    blocks = getattr(response, "output", []) or []
                    texts = []
                    for b in blocks:
                        for c in getattr(b, "content", []) or []:
                            if getattr(c, "type", "") == "output_text":
                                texts.append(getattr(c, "text", ""))
                    output_text = "\n".join([t for t in texts if t]).strip()
                except Exception:
                    output_text = None
            if not output_text:
                raise RuntimeError("Responses API returned no output_text.")
            profile_data = json.loads(output_text)
        except Exception as resp_err:
            # Compatibility fallback: chat.completions with simple json_object mode (no schema enforcement)
            logger.warning(
                f"[document_profiler] Responses API failed ({resp_err}); falling back to chat.completions JSON mode."
            )
            chat = await client.chat.completions.create(
                model="gpt-4o-mini",
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "Return ONLY a JSON object with fields: summary (string), keywords (array of strings), entities (object with arrays: people, organizations, locations, dates).",
                    },
                    {"role": "user", "content": PROMPT_TEMPLATE.format(document_text=truncated_content)},
                ],
                temperature=0.2,
            )
            message_content = chat.choices[0].message.content if chat.choices else None
            if not message_content:
                logger.error("[document_profiler] Chat Completions fallback returned empty content.")
                return None
            profile_data = json.loads(message_content)
        
        # Basic validation against the schema
        if "summary" not in profile_data or "keywords" not in profile_data:
            logger.error(f"[document_profiler] OpenAI response is missing required fields. Got: {profile_data}")
            return None
            
        logger.info(f"[document_profiler] Successfully generated document profile.")
        return profile_data

    except Exception as e:
        logger.error(f"[document_profiler] Failed to generate document profile from text: {e}", exc_info=True)
        return None
