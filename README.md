# Backend Search - Document Search & RAG System.


A production-ready FastAPI backend for document processing, semantic search, and GPT-powered question answering. Built for integration with Custom GPTs and real-world document management workflows.

## 🚀 Features

### Core Functionality
- **Document Upload & Processing**: Automated OCR, chunking, and embedding pipeline
- **Semantic Search**: Hybrid search combining vector similarity and keyword matching
- **GPT Integration**: Direct API endpoints for Custom GPT assistants
- **Google Drive Sync**: Automated file synchronization and processing
- **Background Workers**: Efficient processing of documents and OCR tasks

### Search Capabilities
- Vector similarity search using OpenAI embeddings
- Full-text search with PostgreSQL
- Hybrid scoring with configurable weights
- Intelligent query understanding and filtering
- Batch processing for large result sets

## 🏗️ Architecture

```
app/
├── api/
│   ├── assistant/          # Custom GPT integration endpoints
│   ├── file_ops/          # Document processing and search
│   └── gdrive_ops/        # Google Drive synchronization
├── core/                  # Core services and utilities
├── services/             # Business logic services
└── workers/              # Background task processing
```

## 🛠️ Technology Stack

- **Framework**: FastAPI with async support
- **Database**: PostgreSQL with pgvector for embeddings
- **Storage**: Supabase for file storage and database
- **AI/ML**: OpenAI GPT-5 and embeddings
- **Document Processing**: OCR with Tesseract, PDF processing
- **Deployment**: Railway (production) with Docker support

## 📋 Prerequisites

- Python 3.11+
- PostgreSQL with pgvector extension
- Supabase account and project
- OpenAI API key
- Google Drive API credentials (optional)

## 🚀 Quick Start

1. **Clone and setup environment**:
```bash
git clone https://github.com/canyadigit29/backend_search.git
cd backend_search
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. **Configure environment variables**:
```bash
cp .env.example .env
# Edit .env with your credentials
```

3. **Run the application**:
```bash
uvicorn app.main:app --reload
```

4. **Start background workers**:
```bash
# OCR and document processing
python run_worker.py

# Google Drive sync (optional)
python run_gdrive_sync.py
```

## 🔧 Configuration

### Environment Variables
```env
# Database
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_ROLE=your_service_role_key

# OpenAI
OPENAI_API_KEY=your_openai_key

# Google Drive (optional)
GOOGLE_DRIVE_FOLDER_ID=your_folder_id
GOOGLE_SERVICE_ACCOUNT_KEY=path_to_service_account.json

# API Configuration
API_PREFIX=/api
ALLOWED_ORIGINS=https://your-frontend.com
```

## 📡 API Endpoints

### Search Documents
```http
POST /api/assistant/search_docs
Content-Type: application/json

{
  "query": "your search query",
  "relevance_threshold": 0.4,
  "max_results": 100,
  "search_weights": {
    "semantic": 0.6,
    "keyword": 0.4
  }
}
```

### Google Drive Sync
```http
POST /api/gdrive/sync
```

### Manual Worker Trigger
```http
POST /api/run-worker
```

## 🔄 Background Processing

The system includes automated background workers that run every hour:

- **OCR Worker**: Processes uploaded PDFs for text extraction
- **Ingestion Worker**: Chunks and embeds documents for search
- **Google Drive Sync**: Downloads and processes new files from Google Drive

## 🗃️ Database Schema

Key tables:
- `files`: Document metadata and processing status
- `document_chunks`: Text chunks with embeddings for search
- `embeddings`: Vector storage for semantic search

## 🧪 Testing

```bash
python -m pytest tests/
```

## 📝 Development

### Code Quality
- Type hints throughout
- Async/await for I/O operations
- Comprehensive error handling
- Structured logging

### Adding New Features
1. Core logic goes in `app/core/`
2. API endpoints in appropriate `app/api/` subdirectory
3. Background tasks in `app/workers/`
4. Update OpenAPI schema if needed

## 🚀 Deployment

### Railway (Recommended)
```bash
# Connect your GitHub repo to Railway
# Set environment variables in Railway dashboard
# Deploy automatically on push
```

### Docker
```bash
docker build -t backend-search .
docker run -p 8000:8000 backend-search
```

## 📊 Performance

- Optimized vector search with pgvector
- Efficient chunking and embedding strategies
- Configurable batch processing
- Background task processing to avoid API timeouts

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License.

## 🆘 Support

For issues and questions:
1. Check the [Issues](https://github.com/canyadigit29/backend_search/issues) page
2. Review the API documentation at `/docs` when running locally
3. Check the logs for detailed error information

---

Built with ❤️ for efficient document search and AI integration.