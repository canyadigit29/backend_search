from fastapi import FastAPI
from pgvector_search import semantic_search

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Semantic service is running."}

@app.post("/search/")
async def search_endpoint(query: str):
    results = semantic_search(query)
    return {"results": results}
