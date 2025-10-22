from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from enum import Enum

class DocType(str, Enum):
    agenda = "agenda"
    minutes = "minutes"
    report = "report"
    ordinance = "ordinance"
    resolution = "resolution"
    memo = "memo"
    other = "other"

class User(BaseModel):
    id: UUID

class SearchWeights(BaseModel):
    semantic: float = Field(default=0.5, ge=0, le=1)
    keyword: float = Field(default=0.5, ge=0, le=1)

class SearchRequest(BaseModel):
    query: str
    user: User
    relevance_threshold: Optional[float] = 0.5
    search_weights: Optional[SearchWeights] = SearchWeights()
    or_terms: Optional[List[str]] = []
    doc_type: Optional[DocType] = None
    resume_chunk_ids: Optional[List[str]] = []
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    metadata_filter: Optional[Dict[str, Any]] = None

class Source(BaseModel):
    id: str
    file_name: str
    url: str
    page_number: int
    score: float
    excerpt: str
    doc_type: Optional[DocType] = None
    doc_id: Optional[str] = None

class SearchResponse(BaseModel):
    summary: Optional[str] = None
    summary_was_partial: bool = False
    sources: List[Source] = []
    can_resume: bool = False
    pending_chunk_ids: List[str] = []
    included_chunk_ids: List[str] = []
    error: Optional[str] = None

class UploadMetadataRequest(BaseModel):
    file_id: str
    metadata_json: str
    user: User

class UploadMetadataResponse(BaseModel):
    message: str
    file_id: str

