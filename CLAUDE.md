# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication Language

**Always respond to the user in Russian.** All explanations, questions, and comments should be written in Russian regardless of the language used in code or configuration files.

## Application Launch Policy

**Never start the application automatically.** Only the user should launch the server (uvicorn, run.py, docker). Claude may provide commands but must not execute them without explicit user request.

## Code in Responses Policy

**Do not write code blocks in conversation text.** When discussing solutions or answering questions:
- Describe the architecture and approach in natural language
- Explain what needs to be changed and where
- Reference files and line numbers for context
- Write actual code only when editing files directly via tools

This keeps responses focused on understanding and decision-making rather than copy-paste snippets.

## Development Commands

```bash
# Run development server (with hot reload)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run production server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

# Alternative entry point
python run.py

# Install dependencies
pip install -r requirements.txt
```

No test or lint commands are configured in this project.

## Architecture Overview

FastAPI backend for technical document analysis using Google Gemini LLM.

### Layer Structure

```
Routers (HTTP) → Services (Business Logic) → DB/Storage (Data)
```

**Routers** (`app/routers/`): auth, user, chats, files, projects, prompts
**Services** (`app/services/`): agent_service (orchestrator), llm_service, search_service, queue_service, evidence_service, deletion_service
**DB Clients** (`app/db/`): supabase_client, supabase_projects_client, s3_client

### Two Database Architecture

- **Main Supabase** (`supabase_client.py`): Users, chats, messages, prompts, settings - read/write
- **Projects Supabase** (`supabase_projects_client.py`): Project tree, documents, search blocks - **read-only**
- **S3/R2** (`s3_client.py`): File uploads, evidence images, rendered content

### Request Processing Pipeline

The `agent_service.py` orchestrates multi-phase document analysis:
1. Intent analysis (search/compare/direct mode)
2. Document search and context building
3. LLM processing with streaming
4. Tool execution (zoom, request_images, request_materials)
5. Response persistence

### Key Patterns

**Queue-based concurrency** (`queue_service.py`): Limits concurrent LLM requests via semaphore (default: 2 concurrent, 50 max queue). Provides position tracking and estimated wait time.

**SSE streaming** (`GET /chats/{chat_id}/stream`): Server-Sent Events for real-time LLM token streaming with events: `queue_position`, `processing_started`, `llm_token`, `tool_call`, `completed`.

**Background deletion** (`deletion_service.py`): Async cascade deletion of chats → messages → files (S3 + DB) via asyncio.Queue.

**Dependency injection** (`core/dependencies.py`): FastAPI Depends() for user auth, database clients, and settings.

### Model Profiles

- **simple**: Flash model only (faster)
- **complex**: Flash + Pro model cascade (more powerful)

Selected per-user via `settings.model_profile`.

### Configuration

Environment-based via Pydantic Settings (`app/config.py`). Key categories:
- JWT auth (secret, algorithm, expiry)
- Two Supabase connections (main + projects)
- S3/R2 storage credentials
- Gemini API (default key, models, parameters)
- Queue limits and cache settings

Copy `env.example` to `.env` for local development.

## API Documentation

After starting the server:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
