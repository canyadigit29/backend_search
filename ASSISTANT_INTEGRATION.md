# OpenAI Search Assistant Integration

This integration allows your backend search system to work with the OpenAI Assistant `asst_JmzUgai6rV2Hc6HTSCJFZQsD`.

## Setup

### 1. Environment Variable
Add this environment variable to your `.env` file or deployment configuration:

```env
SEARCH_ASSISTANT_ID=asst_JmzUgai6rV2Hc6HTSCJFZQsD
```

### 2. Database Schema (Optional)
If you want to track assistant conversations, create this table in your Supabase database:

```sql
CREATE TABLE assistant_conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_reply TEXT NOT NULL,
    function_calls JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add indexes for better performance
CREATE INDEX idx_assistant_conversations_user_id ON assistant_conversations(user_id);
CREATE INDEX idx_assistant_conversations_thread_id ON assistant_conversations(thread_id);
CREATE INDEX idx_assistant_conversations_created_at ON assistant_conversations(created_at);
```

## API Endpoints

### 1. Chat with Assistant
**POST** `/api/assistant/chat`

Start a conversation or continue an existing one with the search assistant.

**Request Body:**
```json
{
    "user_id": "your-user-id",
    "message": "Search for information about ARPA funding",
    "thread_id": "optional-existing-thread-id"
}
```

**Response:**
```json
{
    "reply": "I found several documents about ARPA funding...",
    "thread_id": "thread_abc123",
    "function_calls": [
        {
            "function": "search_documents",
            "arguments": {"query": "ARPA funding", "user_id": "your-user-id"},
            "result": {"results": [...], "total_found": 5}
        }
    ]
}
```

### 2. Get Thread Messages
**GET** `/api/assistant/threads/{thread_id}/messages?limit=20`

Get conversation history for a specific thread.

### 3. Delete Thread
**DELETE** `/api/assistant/threads/{thread_id}`

Delete a conversation thread.

## Assistant Functions

The assistant has access to these functions:

### `search_documents`
- Search through municipal documents using semantic similarity and keywords
- Supports filtering by year, month, document type
- Returns relevant document chunks with metadata

### `get_file_list`
- Get a list of available files in the system
- Filter by user, file type, etc.

### `get_document_summary`
- Get summary information about a specific document
- Returns file metadata and preview content

## Usage Examples

### Frontend Integration
```javascript
// Start a new conversation
const response = await fetch('/api/assistant/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        user_id: 'user123',
        message: 'What did the council discuss about road repairs in 2024?'
    })
});

const result = await response.json();
console.log(result.reply); // Assistant's response
console.log(result.thread_id); // Save this for continued conversation

// Continue the conversation
const followUp = await fetch('/api/assistant/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        user_id: 'user123',
        message: 'Can you find the specific budget amounts?',
        thread_id: result.thread_id // Use existing thread
    })
});
```

### Python Client Example
```python
import requests

def chat_with_assistant(message, user_id, thread_id=None):
    url = "http://your-backend.com/api/assistant/chat"
    payload = {
        "user_id": user_id,
        "message": message
    }
    if thread_id:
        payload["thread_id"] = thread_id
    
    response = requests.post(url, json=payload)
    return response.json()

# Usage
result = chat_with_assistant(
    "Search for meeting minutes about budget discussions", 
    "user123"
)
print(result["reply"])
```

## Testing

Run the test script to verify the integration:

```bash
python test_assistant.py
```

Make sure your backend is running and the environment variables are set.

## Assistant Configuration

Your OpenAI Assistant (`asst_JmzUgai6rV2Hc6HTSCJFZQsD`) should be configured with:

1. **Instructions**: Tell it what kind of documents it has access to and how to help users search municipal records
2. **Model**: GPT-4 or GPT-4 Turbo recommended for best results
3. **Tools**: The function definitions are automatically provided by the API

Example assistant instructions:
```
You are a helpful assistant that helps users search through municipal documents including meeting minutes, agendas, ordinances, and other government records. 

When users ask questions, use the search_documents function to find relevant information. You can search by keywords, filter by date ranges, document types, and more.

Always provide specific, helpful responses based on the actual document content you find. If you can't find relevant information, suggest alternative search terms or approaches.

For complex queries, you may need to make multiple searches with different keywords or filters to gather comprehensive information.
```

## Error Handling

The API handles various error conditions:
- Invalid assistant ID
- OpenAI API failures  
- Database connection issues
- Function call errors

All errors return appropriate HTTP status codes and descriptive error messages.

## Security Considerations

- User ID is used for access control - ensure proper authentication
- Thread IDs should be treated as sensitive user data
- Consider rate limiting for production use
- Function calls are logged for audit purposes