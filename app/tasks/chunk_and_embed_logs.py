
import os
from datetime import datetime, timedelta
from supabase import create_client, Client
from openai import OpenAI

# Use Railway env variables
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
openai = OpenAI(api_key=OPENAI_API_KEY)
embedding_model = "text-embedding-3-small"

# Step 1: Get all log messages from today
start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
logs = supabase.table("daily_chat_log").select("*").gte("timestamp", start_of_day).order("timestamp", desc=False).execute().data

# Step 2: Group by session + topic
from collections import defaultdict
grouped = defaultdict(list)
for log in logs:
    key = (log["session_id"], log.get("topic_name", "General"))
    grouped[key].append(log)

# Step 3: Chunk each group into 3–5 message blocks
def chunk_messages(messages, chunk_size=5):
    for i in range(0, len(messages), chunk_size):
        yield messages[i:i+chunk_size]

# Step 4: Create memory rows and embed each chunk
for (session_id, topic_name), messages in grouped.items():
    chunks = list(chunk_messages(messages))
    for i, chunk in enumerate(chunks):
        combined_text = "\n".join([msg["role"] + ": " + msg["content"] for msg in chunk])
        try:
            embedding = openai.embeddings.create(
                model=embedding_model,
                input=combined_text
            ).data[0].embedding

            supabase.table("memory").insert({
                "session_id": session_id,
                "message_index": i,
                "role": "grouped",  # denotes this is a multi-turn chunk
                "content": combined_text,
                "timestamp": datetime.utcnow().isoformat(),
                "embedding": embedding,
                "topic_id": None,
                "topic_name": topic_name
            }).execute()
            print(f"✅ Embedded chunk {i+1}/{len(chunks)} for session {session_id}")
        except Exception as e:
            print(f"❌ Failed embedding chunk {i+1} for session {session_id}: {str(e)}")

# Step 5: Wipe the daily log if everything succeeded
try:
    supabase.table("daily_chat_log").delete().gte("timestamp", start_of_day).execute()
    print("🧹 Daily log cleared.")
except Exception as e:
    print(f"⚠️ Failed to clear log: {str(e)}")
