# GroundedOps v4.1

## Overview
v4.1 focuses on stabilising the RAG pipeline, fixing routing errors, enforcing grounding, and reducing incorrect outputs while maintaining acceptable latency.

---

## Key Changes

### 1. Routing Fix
- Correct classification into:
  - `extract` → checklist/steps only
  - `fast` → short factual queries
  - `reasoning` → why/how/comparisons
  - `accurate` → default
- Prevented misrouting of generic queries to extraction

---

### 2. Extraction Guard
- Structured extraction only runs if query contains:
  - `checklist`, `steps`, `procedure`, `verify`, `instructions`
- Prevents extraction hijacking normal answers

---

### 3. LLM Execution Stabilised
- Ensured LLM is always called unless valid extraction exists
- Fixed fallback chain:
  - `phi → mistral → deepseek`
- Removed repeated/looping retries

---

### 4. Grounding Enforcement
- Added retrieval confidence gate before LLM
- Rejects:
  - out-of-domain queries
  - weak matches
- Uses reranker score threshold

---

### 5. True Hybrid Retrieval
- BM25 and dense retrieval run independently on full corpus
- Combined using RRF (Reciprocal Rank Fusion)
- Fixes missed keyword-heavy results

---

### 6. Latency Improvements
- Reduced context size (≤300 chars per chunk)
- Limited chunks to top 3
- Eliminated duplicate LLM calls
- Reduced unnecessary processing

---

### 7. Refusal Handling
- Added post-processing:
  - `truncate_after_refusal()`
- Removes hallucinated continuation after refusal

---

### 8. Logging Improvements
- Added logs for:
  - routing decision
  - retrieval score
  - fallback usage
  - grounding score

---

## Behaviour Improvements

- Extraction only triggers when appropriate
- Fewer hallucinations
- Better rejection of irrelevant queries
- Stable fallback handling
- More predictable outputs

---

