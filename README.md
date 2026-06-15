## README Changes (v4.0)

### Core Additions
- Introduced **Hybrid Retrieval (BM25 + Dense + RRF)** for improved relevance
- Added **Chroma Vector DB** for persistent storage (replaces in-memory chunks)
- Implemented **LLM fallback chain** (phi → mistral → deepseek)
- Added **Grounding validation layer (NLI-based)** to reduce hallucinations
- Introduced **query routing system** (extract / fast / accurate / reasoning)

### Pipeline Improvements
- Added **retrieval confidence gate** (rejects out-of-domain queries early)
- Integrated **reranking (CrossEncoder)** after retrieval
- Added **structured extraction path** for checklists/steps (bypass LLM)
- Improved **prompt design** (strict context-bound answering)

### Performance & Stability
- Added **model warmup** to reduce first-call latency
- Implemented **request locking for Ollama** to prevent overload
- Added **fallback retry logic** for failed LLM calls
- Improved **timeout handling and error recovery**

### Data Handling
- Added **background ingestion + caching**
- Prevented **duplicate document ingestion**
- Introduced **source tracking for chunks**

### UX / System Behavior
- Added **memory context for short follow-up queries**
- Improved **response structure (timing, grounding score, flags)**
- Enabled **clean rejection for unsupported queries**

### Dev / Infra
- Cleaned repo (ignored DB + logs)
- Standardized **branch/tag versioning (vX.0)**
- Updated README to reflect actual architecture (not legacy FAISS/LangChain claims)
