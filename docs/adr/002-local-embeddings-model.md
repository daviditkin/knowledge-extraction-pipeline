# ADR-002: Use sentence-transformers/all-MiniLM-L6-v2 for local embeddings

**Status**: Accepted
**Date**: 2024-03
**Deciders**: Pipeline architecture team

---

## Context

The RAG pipeline needs to convert text chunks (code, schema definitions, documentation, spec fragments) into dense vector representations for semantic similarity search. Options:

1. **API-based embedding** (OpenAI `text-embedding-ada-002`, `text-embedding-3-small`)
2. **Locally-hosted large model** (e.g., `nomic-embed-text`, `bge-large-en-v1.5`, `all-mpnet-base-v2`)
3. **Locally-hosted small model** (e.g., `all-MiniLM-L6-v2`, `paraphrase-MiniLM-L3-v2`)
4. **BM25 / TF-IDF only** (no embedding model, keyword search only)

## Decision

Use **sentence-transformers/all-MiniLM-L6-v2** loaded from a local directory.

Output dimensions: **384**. Model size: **~80MB**.

## Rationale

### API-based embedding is impossible

The restricted network has no external internet access. OpenAI's embedding API, Cohere's embedding API, and any other hosted embedding service are all unreachable. This eliminates option 1 entirely.

### Model size vs. quality tradeoff

The embedding model runs on the same server as the pipeline. This server likely does not have a GPU (typical of application servers on a private network). CPU inference speed must be acceptable for both batch indexing and interactive query embedding.

| Model | Dimensions | Size | CPU inference (single text) | MTEB Average |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 384 | 80MB | ~5ms | 56.26 |
| all-MiniLM-L12-v2 | 384 | 120MB | ~10ms | 59.76 |
| all-mpnet-base-v2 | 768 | 420MB | ~25ms | 63.30 |
| bge-large-en-v1.5 | 1024 | 1.3GB | ~80ms | 64.23 |
| nomic-embed-text | 768 | 270MB | ~20ms | 62.39 |

For a 50,000-chunk batch index at 32 chunks per batch:
- all-MiniLM-L6-v2: ~25 minutes
- all-mpnet-base-v2: ~78 minutes
- bge-large-en-v1.5: ~250 minutes (4+ hours — unacceptable for a nightly job)

For interactive query embedding (single text):
- all-MiniLM-L6-v2: ~5ms (imperceptible)
- bge-large-en-v1.5: ~80ms (still fast, but adds to query latency)

The quality difference (MTEB average: 56.26 vs. 64.23 for the largest model) is real but not decisive. For retrieval tasks on a specific technical domain (code, SQL, XML), the domain-specific context in our chunks compensates for the model's lower general capability. The system prompt and context section headers provide strong lexical anchoring that reduces dependence on embedding quality.

### Bundle size constraint

The deployment bundle must be transferred to the restricted network physically (USB drive or internal file share). The all-MiniLM-L6-v2 model adds 80MB. The full bundle (model + Python wheels including PyTorch) is approximately 1.5GB.

Using bge-large-en-v1.5 would increase the bundle to approximately 2.7GB (1.3GB model). This is a significant transfer overhead. More importantly, PyTorch for CPU (required to run any of these models) is ~700MB regardless of model choice — the model size difference is marginal relative to the total bundle.

However, all-MiniLM-L6-v2 at 80MB is more likely to be transportable within the file size limits of whatever mechanism is used (USB drive capacity, internal file share limits).

### Offline operation

sentence-transformers loads from a local directory path. When the path exists on disk, no network requests are made. The model directory contains all necessary files: config, tokenizer, weights, and metadata.

Verification during development:
```python
import sentence_transformers
import os

# Disable HuggingFace Hub API (extra safety measure)
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

model = SentenceTransformer("models/all-MiniLM-L6-v2")
# Loads from disk, no network calls even attempted
```

### Vector dimensions and pgvector

all-MiniLM-L6-v2 produces 384-dimensional vectors. The pgvector column is `VECTOR(384)`. The HNSW index is built on 384-dimensional vectors. If the model were changed to one with 768 or 1024 dimensions, the entire knowledge store would need to be re-indexed with a schema migration. The 384-dim choice is a concrete commitment in the schema.

If higher quality is needed in the future, the migration path is:
1. Update `requirements.txt` to the new model
2. Add it to `scripts/download_models.py`
3. Alter the embedding column: `ALTER TABLE kep.kep_chunks ALTER COLUMN embedding TYPE VECTOR(768)`
4. Re-run full indexing

## Consequences

### Positive

- Model fits in 80MB (fast download, included in bundle)
- CPU inference is fast: ~5ms per query, ~25 minutes for full batch index
- No GPU required
- Fully offline: loads from local path, no network calls
- Well-documented, stable model from the sentence-transformers library
- Consistent output: same text always produces the same vector

### Negative

- 384 dimensions may not capture all nuance in complex queries
- MTEB average of 56.26 is lower than larger models; very long-range semantic relationships may be missed
- Code-specific embedding models (e.g., CodeBERT, GraphCodeBERT) might perform better on code chunks; all-MiniLM-L6-v2 is a general-purpose model

### Risk Mitigation

The hybrid search approach (vector + keyword + graph expansion) reduces dependence on embedding quality. Even if the vector search misses a relevant chunk, the keyword search or graph expansion may find it. The RAG quality is more sensitive to good chunking strategy and good prompt templates than to the specific embedding model.

## Upgrade Path

If embedding quality proves inadequate:
1. Replace model in `config.yaml`: `embedding.model_path: models/all-mpnet-base-v2`
2. Update `knowledge-store/schema/V001__kep_base_schema.sql` to use `VECTOR(768)`
3. Run `python scripts/migrate_embeddings.py` (to be written if needed)
4. Re-index

The upgrade does not require changing any extractor, chunker, or prompt builder code.
