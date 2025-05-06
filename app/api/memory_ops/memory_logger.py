
import os
import uuid
from datetime import datetime
from app.core.supabase_client import supabase
from app.core.openai_client import embed_text

def store_memory(user_id: str, content: str, message_type: str = "user"):
    try:
        # Embed the message
        embedding = embed_text(content)
        embedding_array = embedding if isinstance(embedding, list) else embedding.tolist()

        # Prepare row
        record = {{
            "message_id": str(uuid.uuid4()),
            "user_id": user_id,
            "content": content,
            "embedding": embedding_array,
            "message_type": message_type,
            "created_at": datetime.utcnow().isoformat()
        }}

        # Insert into Supabase
        supabase.table("memory").insert(record).execute()
        return True
    except Exception as e:
        print(f"🧠 Memory store failed: {{str(e)}}")
        return False
