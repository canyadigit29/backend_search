import os
from datetime import datetime, timedelta
from app.core.supabase_client import create_client
from app.core.openai_client import embed_text
import uuid

# ⏱ Set up Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]  # ✅ updated key name
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

# 🧠 Pull today's unembedded chat messages
start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
daily_log = supabase.table("daily_chat_log").select("*").gte("timestamp", start_of_day).order("timestamp").execute()

if not daily_log.data:
    print("📭 No daily logs found for embedding.")
    exit(0)

messages = daily_log.data

# 📦 Group messages by session_id
grouped = {}
for msg in messages:
    session = msg["session_id"]
    grouped.setdefault(session, []).append(msg)

# 💬 Chunk and embed grouped conversations
for session_id, msgs in grouped.items():
    content_block = "\n".join([f"{m['role']}: {m['content']}" for m in msgs])
    embedding = embed_text(content_block)

    chunk_id = str(uuid.uuid4())
    try:
        supabase.table("memory").insert({
            "id": chunk_id,
            "session_id": session_id,
            "message_index": 0,
            "speaker_role": "grouped",  # ✅ renamed from "role"
            "content": content_block,
            "timestamp": datetime.utcnow().isoformat(),
            "embedding": embedding,
            "topic_id": msgs[0].get("topic_id") or str(uuid.uuid4()),
            "topic_name": msgs[0].get("topic_name", "General")
        }).execute()
        print(f"✅ Embedded chunk for session {session_id}")
    except Exception as e:
        print(f"❌ Failed embedding chunk for session {session_id}: {e}")

# 🧹 Wipe today's log after embedding
try:
    supabase.table("daily_chat_log").delete().gte("timestamp", start_of_day).execute()
    print("🧽 Daily log cleared.")
except Exception as e:
    print(f"⚠️ Failed to clear daily log: {e}")
