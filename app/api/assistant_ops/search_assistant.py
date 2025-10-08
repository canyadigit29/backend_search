import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel

from app.core.supabase_client import supabase
from app.api.file_ops.search_docs import perform_search
from app.api.file_ops.embed import embed_text

router = APIRouter()
logger = logging.getLogger("search_assistant")
logger.setLevel(logging.DEBUG)

client = OpenAI()
SEARCH_ASSISTANT_ID = os.getenv("SEARCH_ASSISTANT_ID", "asst_JmzUgai6rV2Hc6HTSCJFZQsD")

class AssistantMessageRequest(BaseModel):
    user_id: str
    message: str
    thread_id: Optional[str] = None  # If provided, continue existing conversation

class AssistantResponse(BaseModel):
    reply: str
    thread_id: str
    function_calls: Optional[List[Dict[str, Any]]] = None

# Function definitions that the assistant can call
ASSISTANT_FUNCTIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search through the document database using semantic similarity and keyword matching. Use this to find relevant information from meeting minutes, agendas, ordinances, and other municipal documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query - can be a question, keywords, or specific terms to search for"
                    },
                    "user_id": {
                        "type": "string", 
                        "description": "User ID to filter results for"
                    },
                    "match_count": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default 20)",
                        "default": 20
                    },
                    "meeting_year": {
                        "type": "integer",
                        "description": "Filter by specific meeting year (e.g., 2023, 2024)"
                    },
                    "meeting_month": {
                        "type": "integer", 
                        "description": "Filter by specific meeting month (1-12)"
                    },
                    "document_type": {
                        "type": "string",
                        "description": "Filter by document type (e.g., 'minutes', 'agenda', 'ordinance')"
                    },
                    "match_threshold": {
                        "type": "number",
                        "description": "Minimum similarity threshold for results (0.0-1.0, default 0.5)",
                        "default": 0.5
                    }
                },
                "required": ["query", "user_id"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "get_file_list",
            "description": "Get a list of available files in the system, optionally filtered by user or file type",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": "User ID to filter files for"
                    },
                    "file_type": {
                        "type": "string", 
                        "description": "Filter by file type (e.g., 'pdf', 'docx')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of files to return (default 50)",
                        "default": 50
                    }
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_document_summary",
            "description": "Get a summary or overview of a specific document by file ID",
            "parameters": {
                "type": "object", 
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "The ID of the file to get summary for"
                    },
                    "user_id": {
                        "type": "string",
                        "description": "User ID for access control"
                    }
                },
                "required": ["file_id", "user_id"]
            }
        }
    }
]

def handle_search_documents(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle the search_documents function call from the assistant"""
    try:
        query = arguments.get("query")
        user_id = arguments.get("user_id") 
        match_count = arguments.get("match_count", 20)
        match_threshold = arguments.get("match_threshold", 0.5)
        
        # Build search parameters. perform_search expects user_id_filter and may read search_query/user_prompt
        search_args = {
            "query": query,
            "user_id_filter": user_id,
            "match_count": match_count,
            "match_threshold": match_threshold,
            "user_prompt": query,
            "search_query": query
        }
        
        # Add optional filters
        if arguments.get("meeting_year"):
            search_args["meeting_year"] = arguments["meeting_year"]
        if arguments.get("meeting_month"):
            search_args["meeting_month"] = arguments["meeting_month"] 
        if arguments.get("document_type"):
            search_args["document_type"] = arguments["document_type"]
        
        # Generate embedding for semantic search
        try:
            embedding = embed_text(query)
            search_args["embedding"] = embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return {"error": f"Failed to generate embedding: {str(e)}"}
        
        # Perform the search
        result = perform_search(search_args)
        
        if "error" in result:
            return result
            
        chunks = result.get("retrieved_chunks", [])
        
        # Format results for the assistant
        formatted_results = []
        for chunk in chunks[:match_count]:
            formatted_chunk = {
                "content": chunk.get("content", ""),
                "file_name": chunk.get("file_name", ""),
                "score": round(chunk.get("final_score", 0), 3),
                "document_type": chunk.get("document_type", ""),
                "meeting_date": chunk.get("meeting_date", ""),
                "chunk_index": chunk.get("chunk_index", 0)
            }
            formatted_results.append(formatted_chunk)
        
        return {
            "results": formatted_results,
            "total_found": len(formatted_results),
            "query": query
        }
        
    except Exception as e:
        logger.exception("Error in handle_search_documents")
        return {"error": f"Search failed: {str(e)}"}

def handle_get_file_list(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle the get_file_list function call from the assistant"""
    try:
        user_id = arguments.get("user_id")
        file_type = arguments.get("file_type")
        limit = arguments.get("limit", 50)
        
        # Query files table
        query = supabase.table("files").select("id, name, description, file_type, created_at, status")
        
        if user_id:
            query = query.eq("user_id", user_id)
        if file_type:
            query = query.eq("file_type", file_type)
            
        query = query.limit(limit).order("created_at", desc=True)
        
        response = query.execute()
        
        if response.data:
            return {
                "files": response.data,
                "count": len(response.data)
            }
        else:
            return {"files": [], "count": 0}
            
    except Exception as e:
        logger.exception("Error in handle_get_file_list")
        return {"error": f"Failed to get file list: {str(e)}"}

def handle_get_document_summary(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle the get_document_summary function call from the assistant"""
    try:
        file_id = arguments.get("file_id")
        user_id = arguments.get("user_id")
        
        # Get file info
        file_response = supabase.table("files").select("*").eq("id", file_id).eq("user_id", user_id).execute()
        
        if not file_response.data:
            return {"error": "File not found or access denied"}
        
        file_info = file_response.data[0]
        
        # Get first few chunks for summary
        chunks_response = supabase.table("document_chunks").select("content, chunk_index").eq("file_id", file_id).order("chunk_index").limit(5).execute()
        
        summary_content = ""
        if chunks_response.data:
            summary_content = "\n\n".join([chunk["content"] for chunk in chunks_response.data])
        
        return {
            "file_info": {
                "name": file_info.get("name"),
                "description": file_info.get("description"),
                "file_type": file_info.get("file_type"),
                "created_at": file_info.get("created_at"),
                "status": file_info.get("status")
            },
            "preview_content": summary_content[:2000],  # First 2000 chars
            "total_chunks": len(chunks_response.data) if chunks_response.data else 0
        }
        
    except Exception as e:
        logger.exception("Error in handle_get_document_summary")
        return {"error": f"Failed to get document summary: {str(e)}"}

# Function handler mapping
FUNCTION_HANDLERS = {
    "search_documents": handle_search_documents,
    "get_file_list": handle_get_file_list,
    "get_document_summary": handle_get_document_summary
}

@router.post("/assistant/chat", response_model=AssistantResponse)
async def chat_with_search_assistant(request: AssistantMessageRequest):
    """
    Chat with the search assistant. The assistant can search documents and access file information.
    """
    try:
        user_id = request.user_id
        user_message = request.message
        thread_id = request.thread_id
        
        # Create or retrieve thread
        if thread_id:
            # Use existing thread
            thread = client.beta.threads.retrieve(thread_id)
        else:
            # Create new thread
            thread = client.beta.threads.create()
            thread_id = thread.id
        
        # Add user message to thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user", 
            content=user_message
        )
        
        # Create run with function tools
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=SEARCH_ASSISTANT_ID,
            tools=ASSISTANT_FUNCTIONS
        )
        
        function_calls = []
        
        # Wait for run completion and handle function calls
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
            
            if run_status.status == "completed":
                break
            elif run_status.status == "requires_action":
                # Handle function calls
                tool_calls = run_status.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []
                
                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_args = json.loads(tool_call.function.arguments)
                    
                    logger.info(f"Assistant calling function: {function_name} with args: {function_args}")
                    
                    # Call the appropriate handler
                    if function_name in FUNCTION_HANDLERS:
                        result = FUNCTION_HANDLERS[function_name](function_args)
                        function_calls.append({
                            "function": function_name,
                            "arguments": function_args,
                            "result": result
                        })
                    else:
                        result = {"error": f"Unknown function: {function_name}"}
                    
                    tool_outputs.append({
                        "tool_call_id": tool_call.id,
                        "output": json.dumps(result)
                    })
                
                # Submit tool outputs
                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )
                
            elif run_status.status in ["failed", "cancelled", "expired"]:
                raise Exception(f"Run failed with status: {run_status.status}")
            else:
                # Still processing
                time.sleep(1)
        
        # Get assistant's response
        messages = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=1)
        latest_message = messages.data[0] if messages.data else None
        
        if latest_message and latest_message.role == "assistant":
            reply = latest_message.content[0].text.value
        else:
            reply = "I apologize, but I couldn't generate a response. Please try again."
        
        # Log the conversation
        try:
            supabase.table("assistant_conversations").insert({
                "user_id": user_id,
                "thread_id": thread_id,
                "user_message": user_message,
                "assistant_reply": reply,
                "function_calls": function_calls,
                "created_at": datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to log conversation: {e}")
        
        return AssistantResponse(
            reply=reply,
            thread_id=thread_id,
            function_calls=function_calls if function_calls else None
        )
        
    except Exception as e:
        logger.exception("Error in chat_with_search_assistant")
        raise HTTPException(status_code=500, detail=f"Assistant chat failed: {str(e)}")

@router.get("/assistant/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str, limit: int = 20):
    """
    Get messages from a specific thread for conversation history
    """
    try:
        messages = client.beta.threads.messages.list(
            thread_id=thread_id,
            order="asc", 
            limit=limit
        )
        
        formatted_messages = []
        for msg in messages.data:
            formatted_messages.append({
                "role": msg.role,
                "content": msg.content[0].text.value if msg.content else "",
                "created_at": msg.created_at
            })
        
        return {"messages": formatted_messages}
        
    except Exception as e:
        logger.exception("Error getting thread messages")
        raise HTTPException(status_code=500, detail=f"Failed to get messages: {str(e)}")

@router.delete("/assistant/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """
    Delete a conversation thread
    """
    try:
        client.beta.threads.delete(thread_id)
        return {"message": "Thread deleted successfully"}
    except Exception as e:
        logger.exception("Error deleting thread")
        raise HTTPException(status_code=500, detail=f"Failed to delete thread: {str(e)}")