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

4. **Start background workers** (now consolidated under `local_dev/`):
```bash
# OCR + ingestion/profile loop (hourly)
python local_dev/run_worker.py

# Legacy Drive sync loop (non-Responses)
python local_dev/run_gdrive_sync.py

# Unified Responses Drive sync + OCR + Vector Store ingest
python local_dev/run_gdrive_sync_responses.py
```

> Helper scripts were moved from the repo root into `local_dev/` to keep the top level clean.

## 🔧 Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in the required values. The most important variables are shown below; see `.env.example` for a complete list and recommended defaults.

```env
# Required – Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_STORAGE_BUCKET=files

# Required – OpenAI
OPENAI_API_KEY=sk-...

# CORS / API
API_PREFIX=/api
ALLOWED_ORIGINS=http://127.0.0.1:3100,http://localhost:3100

# Optional – Google Workspace / Drive (Drive-only ingestion)
GOOGLE_CREDENTIALS_BASE64=<base64-encoded service account json>
GOOGLE_ADMIN_EMAIL=
GOOGLE_DRIVE_FOLDER_ID=
GDRIVE_WORKSPACE_ID=
GDRIVE_VECTOR_STORE_ID=

# Optional – Responses ingestion tuning
VS_UPLOAD_DELAY_MS=1000
VS_UPLOAD_BATCH_LIMIT=25
ENABLE_RESPONSES_GDRIVE_SYNC=true

# Optional – Reliability controls (retries/timeouts)
RETRY_RETRIES=3
RETRY_MIN_MS=300
RETRY_MAX_MS=2500
RETRY_JITTER=true
RESPONSES_STREAM_TIMEOUT_SEC=600

# Optional – Web search enrichment (Researcher)
BRAVE_SEARCH_API_KEY=your_brave_api_key

# Logging
LOG_LEVEL=INFO
LOG_JSON=false
```

Notes:
- Use `SUPABASE_SERVICE_ROLE_KEY` (not `SUPABASE_SERVICE_ROLE`) to match the code in `app/core/config.py`.
- Keep secrets out of version control. Store them in `.env` locally and in your platform’s env settings in production.
- Brave Search is only used when the research web enrichment step is enabled; leave blank to disable web queries.

## 📡 API Endpoints

This service focuses on ingestion and Vector Store maintenance for the frontend’s File Search flow. Retrieval happens in the frontend via the OpenAI Responses API.

Key routes (prefix defaults to `/api`):

- Responses + Drive ingestion
  - `POST /responses/vector-store/ingest/upload` – Upload files for ingestion (multipart)
  - `POST /responses/vector-store/ingest` – Trigger a background ingestion run
  - `POST /responses/gdrive/sync` – Trigger a Google Drive sync (when enabled)
- Vector Store maintenance
  - `GET /responses/list` – List files in the workspace Vector Store
  - `DELETE /responses/file/{file_id}` – Detach and delete an OpenAI File
  - `POST /responses/vector-store/purge` – Detach all files for a workspace
- v2 endpoints (for parity with frontend while migrating)
  - `POST /api/v2/chat/respond` – Responses API + File Search (stream/non-stream)
  - `POST /api/v2/research` – Generate a research report (and persist)
  - `GET /api/v2/research?stream=true&workspace_id=...&question=...` – SSE stream of phases

## 🔄 Background Processing

The system includes automated background workers that run on an interval:

- **Drive Sync (Responses)**: Downloads new files from Google Drive, uploads to Supabase Storage, marks rows for ingest
- **Vector Store Ingest**: Prefers OCR text when available, uploads to OpenAI, attaches to the workspace Vector Store, enriches metadata
- **Document Profiling**: Generates summary/keywords/entities and writes them to `file_workspaces` columns

## 🗃️ Database Schema

Key tables/columns (see the frontend repo’s schema snapshot for full reference):
- `files`: Document metadata and OCR hints (`ocr_needed`, `ocr_scanned`, `ocr_text_path`, `file_path`)
- `file_workspaces`: Join table + ingestion/profiling state (`ingested`, `openai_file_id`, `vs_file_id`, `has_ocr`, `file_ext`, `doc_type`, `meeting_year`, `meeting_month`, `profile_*`, `doc_profile_processed`)
- `workspace_vector_stores`: Per-workspace mapping to an OpenAI Vector Store id

## 🧪 Testing

```bash
python -m pytest tests/
```

## 🩺 Health checks

- Detailed: `GET /responses/vector-store/health` (per-workspace diagnostics)
- Summary: `GET /responses/vector-store/health/summary?workspace_id=...`

Nightly script (returns non‑zero exit code if dangling counts are high):

```powershell
python scripts/vs-health-check.py http://127.0.0.1:8000 <workspace_id>
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