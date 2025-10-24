from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class AssistantSearchRequest(BaseModel):
    query: str
    relevance_threshold: float = Field(default=0.4, description="Minimum similarity score for a result to be considered relevant (0.0 to 1.0).")
    max_results: int = Field(default=100, description="The maximum number of chunks to process for the summary.")
    search_weights: Optional[Dict[str, float]] = Field(default=None, description="Weights to blend semantic and keyword search scores.")
    or_terms: Optional[List[str]] = Field(default=None, description="Optional alternate phrasings or synonyms.")
    resume_chunk_ids: Optional[List[str]] = Field(default=None, description="Resume mode: specify chunk IDs to continue summarization.")
    user_prompt: Optional[str] = None # Adding fallback field
