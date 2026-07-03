## Updates (since last push)

### Retrieval & Ranking
- Introduced hybrid retrieval (lexical + embedding-ready structure)
- Added structured block prioritization (`checklist`, `steps`, `kv`)
- Improved ranking logic to favor exact procedural matches over generic text

### Query Handling
- Implemented extract-first strategy:
  - Returns exact blocks when strong structured match is found
  - Falls back to LLM only when extraction is insufficient
- Added basic query logging and response tracking

### Provider Routing
- Added multi-provider support:
  - Local (Ollama: phi3, mistral, mistral-nemo)
  - Optional OpenAI / Anthropic integration
- Introduced compare mode for evaluating multiple model outputs

### Ingestion Pipeline
- Background ingestion worker using Redis + RQ
- Parsing improvements:
  - Block segmentation (checklist / steps / kv / paragraph)
  - Metadata association per block
- Object storage via MinIO for uploaded documents

### API & Backend
- Expanded FastAPI endpoints:
  - `/upload`
  - `/query`
  - `/feedback`
  - `/documents`
  - `/stats`
- Improved query orchestration flow and response formatting

### Dashboard
- Streamlit dashboard enhancements:
  - Multi-document upload
  - Provider comparison view
  - Result inspection with retrieved context
  - Feedback submission UI

### Configuration & Setup
- Added `.env.example` with provider configuration support
- Standardized Docker setup for:
  - API
  - Worker
  - Postgres
  - Redis
  - MinIO
- Ollama kept external for simpler local setup

### Known Limitations
- Embeddings stored and ranked in-app (no vector DB yet)
- PDF parsing quality dependent on source formatting
- No formal evaluation pipeline
- Compare mode is basic
- No reviewer/correction loop yet

### Next Planned
- Cross-encoder reranking
- Grounding / hallucination checks
- Vector DB integration (FAISS/Chroma)
- Reviewer queue + correction memory
- Improved context truncation and scoring