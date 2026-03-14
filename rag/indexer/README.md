# Indexer

Reads extracted JSON files from `extracted/`, chunks the content, generates embeddings, and loads everything into the PostgreSQL knowledge store.

## What it does

1. Reads all JSON files from `extracted/services/`, `extracted/schema.json`, `extracted/confluence/`, `extracted/ixm-spec/`, and `extracted/log-patterns.json`
2. Chunks each document using content-type-aware chunking strategies
3. Generates embeddings in batches of 32 using the local sentence-transformers model
4. Inserts chunks + embeddings into `kep.kep_chunks`
5. Builds the knowledge graph from service docs (`kep_service_edges`, `kep_service_tables`, etc.)
6. Builds the HNSW vector index after bulk insert (for efficiency)

## Running

```bash
# Full index
python -m rag.indexer --config ../../config/config.yaml

# Incremental (skip unchanged documents)
python -m rag.indexer --config ../../config/config.yaml --changed-only

# Verify only (check DB connection, model load, config — no writes)
python -m rag.indexer --config ../../config/config.yaml --verify-only

# Index a single source type
python -m rag.indexer --config ../../config/config.yaml --source-type go_service
```

## Expected Output

```
[rag.indexer] Starting full index run
[rag.indexer] Loading embedding model from models/all-MiniLM-L6-v2... OK (384-dim, CPU)
[rag.indexer] Connected to PostgreSQL at localhost:5432/appdb (schema: kep)
[rag.indexer] Indexing 124 service documents...
[rag.indexer]   Go services: 120/120 processed, 0 errors
[rag.indexer]   Java services: 4/4 processed, 0 errors
[rag.indexer] Indexing schema (48 tables)...
[rag.indexer] Indexing 312 Confluence pages...
[rag.indexer] Indexing 28 IXM message types...
[rag.indexer] Indexing log patterns...
[rag.indexer] Generated 47,823 chunks, 47,823 embeddings
[rag.indexer] Building HNSW vector index...
[rag.indexer] Building service graph (124 nodes, 512 edges)...
[rag.indexer] Index complete. Total time: 28m 14s
[rag.indexer] Verification: test query returned 5 results in 43ms ✓
```

## Performance

Full indexing of 120 services takes approximately 25–35 minutes on CPU (dominated by embedding generation). Incremental indexing of changed files typically takes 1–5 minutes.

The HNSW index build (after bulk insert) takes approximately 3–5 minutes for 50K vectors.

## Module Structure

```
rag/indexer/
├── indexer.py          # Main Indexer class and CLI entry point
├── source_reader.py    # Reads extracted JSON files, yields typed documents
└── progress.py         # Progress reporting utilities
```
