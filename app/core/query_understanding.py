from app.core.openai_client import chat_completion
import json
import sys

def extract_search_filters(user_prompt: str) -> dict:
    system_prompt = (
        "You are an expert search assistant. Given a user query, extract as many of the following search filters as possible, based on what is specified or implied in the query: document_type, meeting_year, meeting_month, meeting_month_name, meeting_day, ordinance_title, file_extension, section_header, page_number, description, file_name. "
        "Return a JSON object with only the fields that are present or implied. If a field is not present, omit it. Example: {\"document_type\": \"Agenda\", \"meeting_year\": 2024, \"meeting_month\": 1, \"ordinance_title\": \"Noise Ordinance\"}. "
        "If the user query does not specify any filters, return an empty JSON object."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    result = chat_completion(messages)
    try:
        return json.loads(result)
    except Exception:
        return {}
