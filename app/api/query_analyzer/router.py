from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from .classifier import classify_query, get_search_parameters, QueryClassification

router = APIRouter()

class QueryAnalysisRequest(BaseModel):
    query: str

class QueryAnalysisResponse(BaseModel):
    classification: QueryClassification
    search_params: dict

@router.post("/analyze-query", response_model=QueryAnalysisResponse)
async def analyze_query_endpoint(request: QueryAnalysisRequest):
    """
    Analyzes the user's query, classifies it, and returns the optimal
    search parameters based on the classification.
    """
    if not request.query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    try:
        classification_result = await classify_query(request.query)
        search_params = get_search_parameters(classification_result.query_type)
        
        return QueryAnalysisResponse(
            classification=classification_result,
            search_params=search_params
        )
    except Exception as e:
        # Log the exception details for debugging
        print(f"Error during query analysis: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze query.")
