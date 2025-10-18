# Minimal LlamaIndex FastAPI server for RAG search and chat
# Save as llama_server.py

from fastapi import FastAPI, Request
from pydantic import BaseModel
from llama_index.core import VectorStoreIndex, ServiceContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import BM25Retriever, VectorIndexRetriever, HybridRetriever
from llama_index.core.llms import OpenAI
from llama_index.vector_stores.pgvector import PGVectorStore
import os
import logging

# --- Config ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_API_KEY = os.environ.get("SUPABASE_SERVICE_ROLE")
PGVECTOR_CONN_STR = os.environ.get("PGVECTOR_CONN_STR")  # e.g. postgresql://user:pass@host:port/db
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# --- LlamaIndex Setup ---
llm = OpenAI(api_key=OPENAI_API_KEY, model="gpt-5")
service_context = ServiceContext.from_defaults(llm=llm)

vector_store = PGVectorStore.from_params(
    database_url=PGVECTOR_CONN_STR,
    table_name="document_chunks",  # match your table
    embedding_dim=1536  # or 3072 for OpenAI/gpt-5
)

index = VectorStoreIndex.from_vector_store(vector_store, service_context=service_context)

# Use a sentence splitter to match your chunking logic
node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)

# --- FastAPI App ---
app = FastAPI()

class SearchRequest(BaseModel):
    query: str
    user_id: str = None
    filters: dict = None
    top_k: int = 10

@app.post("/search")
async def search(req: SearchRequest):
    # Hybrid retriever: vector + BM25
    vector_retriever = VectorIndexRetriever(index=index, similarity_top_k=req.top_k)
    bm25_retriever = BM25Retriever.from_defaults(index=index, similarity_top_k=req.top_k)
    retriever = HybridRetriever(vector_retriever=vector_retriever, bm25_retriever=bm25_retriever, alpha=0.6)
    query_engine = RetrieverQueryEngine(retriever=retriever, service_context=service_context)
    # Metadata filtering (if any)
    filters = req.filters or {}
    response = query_engine.query(req.query, filters=filters)
    return {"results": [str(node) for node in response.source_nodes], "answer": str(response)}

class ChatRequest(BaseModel):
    query: str
    user_id: str = None
    history: list = None

@app.post("/chat")
async def chat(req: ChatRequest):
    # For demo: just call search for now
    search_req = SearchRequest(query=req.query, user_id=req.user_id)
    return await search(search_req)

# --- Debug logging ---
logging.basicConfig(level=logging.INFO)
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logging.info(f"Incoming request: {request.method} {request.url}")
    response = await call_next(request)
    return response
