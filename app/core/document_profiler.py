import logging
import json
from typing import Dict, Optional

from openai import OpenAI

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
Based on the following document text, generate a structured profile. The profile must include a concise summary (2-4 sentences), a list of 5-10 keywords, and key named entities (people, organizations, locations, dates).

Respond with a JSON object that conforms to the provided schema.

DOCUMENT TEXT:
---
{document_text}
---
"""


def generate_profile_from_text(
    text_content: str, client: Optional[OpenAI] = None
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

    if client is None:
        client = OpenAI()

    try:
        logger.info(f"[document_profiler] Generating profile for document content (truncated to {len(truncated_content)} chars).")
        
        # Use a model that supports JSON mode, like gpt-4-turbo-preview or newer
        model = "gpt-4-turbo-preview"

        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object", "schema": PROFILES_SCHEMA},
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert document analyst. Your task is to create a structured JSON profile of a document based on its text content, following the provided schema precisely.",
                },
                {"role": "user", "content": PROMPT_TEMPLATE.format(document_text=truncated_content)},
            ],
            temperature=0.2,
        )

        message_content = response.choices[0].message.content
        if not message_content:
            logger.error("[document_profiler] OpenAI response was empty.")
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
