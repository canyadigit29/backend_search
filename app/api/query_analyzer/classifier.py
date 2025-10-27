import os
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing import Literal, Dict

# Initialize the OpenAI client
client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# Pydantic models for structured response
class QueryClassification(BaseModel):
    query_type: Literal[
        "semantic", 
        "keyword", 
        "mixed", 
        "sparse", 
        "overbroad", 
        "long_tail"
    ] = Field(..., description="The classified type of the user's query.")
    reasoning: str = Field(..., description="A brief explanation of why the query was classified this way.")

async def classify_query(user_query: str) -> QueryClassification:
    """
    Analyzes the user's query to determine its type for search strategy.
    """
    system_prompt = """
    You are an expert query analyst. Your task is to classify a user's query into one of the following categories based on its content and structure:

    - **semantic**: The query asks for explanations, descriptions, or concepts (e.g., "Explain the new zoning laws").
    - **keyword**: The query contains specific entities, acronyms, or codes (e.g., "Find mentions of Ordinance 1045").
    - **mixed**: The query combines a specific entity with a conceptual search (e.g., "blight issues at the Fink Building").
    - **sparse**: The query uses rare terms or specific, uncommon names that are unlikely to have many hits.
    - **overbroad**: The query uses vague terms that could match too many documents (e.g., "general government," "technology").
    - **long_tail**: The query is for a large, recurring topic that may have many related documents (e.g., "all documents related to parks projects").

    Analyze the user's query and respond with the corresponding classification and your reasoning.
    """

    response = await client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Classify this query: '{user_query}'"}
        ],
        tools=[{"type": "function", "function": {"name": "classify_query", "parameters": QueryClassification.model_json_schema()}}],
        tool_choice={"type": "function", "function": {"name": "classify_query"}},
        temperature=0,
    )
    
    tool_call = response.choices[0].message.tool_calls[0]
    result = QueryClassification.model_validate_json(tool_call.function.arguments)
    return result

def get_search_parameters(query_type: str) -> Dict:
    """
    Returns the search weights and relevance threshold based on the query type.
    """
    params = {
        "semantic": {"search_weights": {"semantic": 0.75, "keyword": 0.25}, "relevance_threshold": 0.40},
        "keyword": {"search_weights": {"semantic": 0.3, "keyword": 0.7}, "relevance_threshold": 0.60},
        "mixed": {"search_weights": {"semantic": 0.55, "keyword": 0.45}, "relevance_threshold": 0.40},
        "sparse": {"search_weights": {"semantic": 0.5, "keyword": 0.5}, "relevance_threshold": 0.30},
        "overbroad": {"search_weights": {"semantic": 0.8, "keyword": 0.2}, "relevance_threshold": 0.65},
        "long_tail": {"search_weights": {"semantic": 0.7, "keyword": 0.3}, "relevance_threshold": 0.40},
    }
    return params.get(query_type, params["semantic"]) # Default to semantic
