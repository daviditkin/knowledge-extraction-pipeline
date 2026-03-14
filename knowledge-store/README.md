# Knowledge Store

The knowledge store is the indexed, searchable repository of all extracted knowledge. It lives in PostgreSQL (schema: `kep`) and uses the pgvector extension for semantic vector search.

## What's in here

```
knowledge-store/
├── schema/       # SQL DDL files for all kep.* tables
├── embeddings/   # Embedding model loader and chunker
└── graph/        # Knowledge graph builder and query utilities
```

## Quick Reference

### Setting up (one time)

```bash
# Install pgvector extension and create kep schema
psql -h <host> -U <superuser> -d <database> -f ../scripts/setup_pgvector.sql

# Create all tables and indexes
psql -h <host> -U <pipeline_user> -d <database> \
  -f schema/V001__kep_base_schema.sql \
  -f schema/V002__kep_indexes.sql
```

### Running the indexer

```bash
# Index all extracted data (run after extractors)
python -m rag.indexer --config ../config/config.yaml

# Index only changed files
python -m rag.indexer --config ../config/config.yaml --changed-only

# Verify the installation without modifying the database
python -m rag.indexer --config ../config/config.yaml --verify-only
```

### Running a quick SQL check

```sql
-- How many chunks are indexed?
SELECT source_type, COUNT(*) FROM kep.kep_chunks GROUP BY source_type;

-- Which services are in the graph?
SELECT service_name, language, handler_count FROM kep.kep_service_nodes ORDER BY service_name;

-- Quick semantic search (example)
SELECT content, service_name, 1 - (embedding <=> '[0.1, 0.2, ...]'::vector) AS score
FROM kep.kep_chunks
ORDER BY embedding <=> '[0.1, 0.2, ...]'::vector
LIMIT 5;
```

## Schema Overview

All tables are in the `kep` schema with the `kep_` prefix to avoid collisions:

| Table | Purpose |
|---|---|
| `kep_documents` | Source document registry (one row per file/page) |
| `kep_chunks` | Content chunks with vector embeddings and full-text search |
| `kep_service_nodes` | Service registry (one row per service) |
| `kep_service_edges` | Service-to-service call relationships |
| `kep_service_tables` | Service-to-database-table relationships |
| `kep_service_log_events` | Log event catalog per service |
| `kep_ixm_message_refs` | Service-to-IXM-message-type relationships |

Full schema design is in [`docs/design/knowledge-store.md`](../docs/design/knowledge-store.md).

## Embedding Model

The semantic search uses `sentence-transformers/all-MiniLM-L6-v2`, a 80MB model that runs on CPU. It must be downloaded before deployment to the restricted network:

```bash
python ../scripts/download_models.py
# Downloads to ../models/all-MiniLM-L6-v2/
```

See [ADR-002](../docs/adr/002-local-embeddings-model.md) for the rationale behind this model choice.

## Storage Requirements

Approximately 430MB for 120 services:
- ~275MB for chunk content + vector embeddings
- ~100MB for HNSW vector index
- ~50MB for full-text GIN index
- ~5MB for knowledge graph tables
