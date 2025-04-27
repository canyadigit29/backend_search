import openai
import psycopg2
from common.config import get_env

openai.api_key = get_env("OPENAI_API_KEY")

def embed_text(text: str) -> list:
    response = openai.Embedding.create(
        model="text-embedding-ada-002",
        input=text
    )
    return response['data'][0]['embedding']

def semantic_search(query: str):
    embedding = embed_text(query)
    conn = psycopg2.connect(get_env("SUPABASE_DB_CONNECTION_STRING"))
    cur = conn.cursor()
    cur.execute("""
        SELECT content, 1 - (embedding <=> cube(array[%s])) AS similarity
        FROM documents
        WHERE 1 - (embedding <=> cube(array[%s])) > 0.8
        ORDER BY similarity DESC;
    """, (embedding, embedding))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"content": row[0], "similarity": row[1]} for row in rows]
