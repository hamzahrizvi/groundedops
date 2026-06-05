# RAG Backend (Hybrid Retrieval + Grounded QA)

A FastAPI-based retrieval-augmented generation (RAG) system with hybrid search, reranking, structured extraction, and grounded answer validation.

---

## Overview

This project implements a modular RAG pipeline designed to:

- Retrieve relevant document chunks using hybrid search
- Improve relevance with reranking
- Extract structured lists when applicable
- Generate answers using local and API-based LLMs
- Validate responses against source context (grounding)

---

## Architecture

Pipeline flow:

```text
Upload → Parse → Chunk → Embed → Store
Query → Retrieve → Rerank → Route → Extract / Generate → Ground → Log
```

---

## Features

### Hybrid Retrieval

- BM25 keyword scoring
- Dense embeddings using SentenceTransformers
- Reciprocal Rank Fusion (RRF)

### Reranking

- Cross-encoder: `ms-marco-MiniLM-L-6-v2`
- Improves top-k relevance

### Structured Extraction

- Detects checklist or step-like content
- Returns directly without LLM when possible

### LLM Layer

- Local models via Ollama:
  - `phi`
  - `mistral`
- Role-based routing:
  - Fast
  - Extract
  - Accurate
  - Reasoning
- Fallback chain across models and providers

### Grounding (Validation)

- Sentence-level NLI validation
- Flags unsupported or hallucinated answers

### Memory (Basic)

- Stores recent interactions
- Injected into short queries

### Observability

- Response timing breakdown
- Retrieval scores
- Grounding scores
- Fallback tracking

---

## Tech Stack

- FastAPI
- SentenceTransformers
- CrossEncoder (reranking + grounding)
- Ollama (local LLMs)
- DeepSeek API (fallback)
- NumPy

---

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start Ollama

```bash
ollama run mistral
```

Optional:

```bash
ollama run phi
```

### 3. Set Environment Variable

```bash
export DEEPSEEK_API_KEY=your_key_here
```

### 4. Run the Server

```bash
uvicorn main:app --reload
```

---

## API

### Upload Documents

**Endpoint**

```http
POST /upload
```

**Description**

Uploads and processes documents into the vector store.

**Pipeline**

```text
Document
  ↓
Parse
  ↓
Chunk
  ↓
Embed
  ↓
Store
```

---

### Query

**Endpoint**

```http
POST /query?q=...
```

**Description**

Runs the full RAG pipeline:

```text
Retrieve
  ↓
Rerank
  ↓
Route
  ↓
Extract or Generate
  ↓
Ground
  ↓
Return
```

**Response Includes**

```json
{
  "answer": "...",
  "provider": "ollama",
  "model": "mistral",
  "grounding_score": 0.92,
  "timings": {
    "retrieval_ms": 45,
    "rerank_ms": 18,
    "generation_ms": 732
  }
}
```

---

## Retrieval Strategy

### Step 1: Dense Retrieval

Uses SentenceTransformers embeddings to retrieve semantically similar chunks.

Advantages:

- Handles paraphrasing
- Captures semantic meaning
- Robust to wording differences

---

### Step 2: Sparse Retrieval

Uses BM25 keyword matching.

Advantages:

- Exact term matching
- Strong performance on factual queries
- Handles rare keywords

---

### Step 3: Reciprocal Rank Fusion (RRF)

Combines dense and sparse rankings into a single ranked result set.

Benefits:

- More stable retrieval quality
- Better recall
- Less dependence on a single retrieval method

---

## Reranking Layer

Retrieved chunks are passed through a cross-encoder:

```text
ms-marco-MiniLM-L-6-v2
```

The reranker:

- Scores query-document relevance jointly
- Reorders retrieved chunks
- Improves answer quality before generation

---

## Structured Extraction Mode

When retrieved content resembles:

- Checklists
- Procedures
- Step-by-step instructions
- Enumerated actions

The system bypasses the LLM and returns structured output directly.

Example:

```text
1. Install dependencies
2. Configure API keys
3. Start Ollama
4. Run FastAPI server
```

Benefits:

- Faster responses
- Reduced hallucinations
- Deterministic output

---

## Generation Layer

### Local Models

Served through Ollama:

```text
phi
mistral
```

### Routing Strategy

Queries are routed based on complexity.

| Query Type | Route |
|------------|---------|
| Simple factual | Fast |
| Extraction | Extract |
| Detailed explanation | Accurate |
| Multi-step reasoning | Reasoning |

---

### Fallback Strategy

If a model fails:

```text
Primary Model
      ↓
Secondary Model
      ↓
DeepSeek API
```

Benefits:

- Higher reliability
- Reduced downtime
- Better response consistency

---

## Grounding Validation

Generated responses are validated against retrieved context.

Process:

```text
Answer
   ↓
Sentence Split
   ↓
NLI Validation
   ↓
Grounding Score
```

Each sentence is checked for support within source documents.

Possible outcomes:

- Supported
- Partially Supported
- Unsupported

---

## Memory Layer

Basic conversational memory stores recent interactions.

Capabilities:

- Tracks recent queries
- Tracks recent responses
- Injects context into short follow-up questions

Example:

```text
User: What is BM25?
User: How does it compare to dense retrieval?
```

The second query can leverage context from the first.

---

## Observability & Metrics

Collected metrics include:

### Performance

- Retrieval latency
- Reranking latency
- Generation latency
- Total request latency

### Retrieval

- Dense retrieval scores
- BM25 scores
- RRF rankings

### Generation

- Model selected
- Provider selected
- Fallback usage

### Grounding

- Grounding score
- Supported sentence count
- Unsupported sentence count

---

## Current Limitations

### Extraction

- Relies on heuristics
- May miss poorly formatted lists
- Not fully semantic

### Grounding

- Uses semantic entailment
- Not strict fact verification
- Can produce false positives

### Performance

- Local inference may be slow on CPU
- Larger models increase latency

### Retrieval

- Thresholds require tuning per dataset
- Chunking strategy impacts quality

---

## Next Steps

### Retrieval Improvements

- Improve list-aware chunking
- Dynamic chunk sizing
- Better metadata filtering

### Grounding Improvements

- Tune grounding thresholds
- Add confidence calibration
- Support citation generation

### Performance Improvements

- Embedding cache
- Response cache
- Async model execution

### Intelligence Improvements

- Better query classification
- Intent detection
- Adaptive routing policies

---

## Status

### Currently Working

✅ Hybrid retrieval

✅ BM25 + dense search

✅ Reciprocal Rank Fusion

✅ Cross-encoder reranking

✅ Structured extraction

✅ Multi-model generation

✅ Ollama integration

✅ DeepSeek fallback

✅ Grounding validation

✅ Memory injection

✅ Observability metrics

---

## End-to-End Flow

```text
User Query
     ↓
Hybrid Retrieval
(BM25 + Dense)
     ↓
RRF Fusion
     ↓
Cross-Encoder Reranking
     ↓
Query Routing
     ↓
Extraction OR Generation
     ↓
Grounding Validation
     ↓
Response + Metrics
```
