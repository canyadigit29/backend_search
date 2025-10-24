import os
from datetime import datetime
from supabase import Client, create_client

# This file was attempting to write to a 'daily_chat_log' table which does not exist.
# To prevent crashes, the implementation has been commented out.
# A proper logging solution should be designed, but for now, this will ensure stability.


def log_message(
    session_id: str,
    role: str,
    content: str,
    topic_name: str = None,
    user_id: str = None,
):
    """
    This function is currently disabled to prevent crashes due to a missing 'daily_chat_log' table.
    It will print the log message to standard output instead.
    """
    print(
        f"Logging disabled: Table 'daily_chat_log' does not exist. Message: {role} | {content}"
    )
    return None
    # try:
    #     # Railway passes env variables directly — use them here
    #     supabase_url = os.environ["SUPABASE_URL"]
    #     supabase_key = os.environ["SUPABASE_SERVICE_ROLE"]
    #     supabase: Client = create_client(supabase_url, supabase_key)
    #
    #     log_entry = {
    #         "session_id": session_id,
    #         "role": role,
    #         "content": content,
    #         "topic_name": topic_name,
    #         "timestamp": datetime.utcnow().isoformat(),
    #     }
    #
    #     if user_id:
    #         log_entry["user_id"] = user_id  # ✅ Add user ownership
    #
    #     result = supabase.table("daily_chat_log").insert(log_entry).execute()
    #
    #     print(
    #         f"✅ Logged message: {role} | {session_id} | {topic_name} | user: {user_id}"
    #     )
    #     return result
    # except Exception as e:
    #     print(f"❌ Log failed: {str(e)}")
    #     return None
