"""
Test script for the Search Assistant integration
Run this after setting up the environment variable SEARCH_ASSISTANT_ID
"""
import os
import requests
import json

# Configure your backend URL
BACKEND_URL = "http://localhost:8000"  # Update this to your actual backend URL
API_PREFIX = "/api"

def test_assistant_chat():
    """Test the assistant chat endpoint"""
    url = f"{BACKEND_URL}{API_PREFIX}/assistant/chat"
    
    # Test payload
    payload = {
        "message": "Search for information about ARPA funding and how the money was used"
    }
    
    try:
        response = requests.post(url, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Assistant chat test successful!")
            print(f"Reply: {result['reply']}")
            print(f"Thread ID: {result['thread_id']}")
            if result.get('function_calls'):
                print(f"Function calls made: {len(result['function_calls'])}")
                for call in result['function_calls']:
                    print(f"  - {call['function']}: {call['arguments']}")
        else:
            print(f"❌ Test failed with status {response.status_code}")
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")

def test_search_function():
    """Test just the search functionality"""
    url = f"{BACKEND_URL}{API_PREFIX}/file_ops/search_docs"
    
    payload = {
        "query": "ARPA funding",
        
    }
    
    try:
        response = requests.post(url, json=payload)
        
        if response.status_code == 200:
            result = response.json()
            print("✅ Search test successful!")
            print(f"Found {len(result.get('retrieved_chunks', []))} chunks")
        else:
            print(f"❌ Search test failed with status {response.status_code}")
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"❌ Search test failed with exception: {e}")

if __name__ == "__main__":
    print("Testing Search Assistant Integration")
    print("=" * 50)
    
    # Check environment variable
    assistant_id = os.getenv("SEARCH_ASSISTANT_ID")
    if not assistant_id:
        print("⚠️  SEARCH_ASSISTANT_ID environment variable not set!")
        print("   Please set it to: asst_JmzUgai6rV2Hc6HTSCJFZQsD")
    else:
        print(f"✅ Assistant ID configured: {assistant_id}")
    
    print("\n1. Testing search functionality...")
    test_search_function()
    
    print("\n2. Testing assistant chat...")
    test_assistant_chat()