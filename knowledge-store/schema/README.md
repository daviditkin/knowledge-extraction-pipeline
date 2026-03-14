# Knowledge Store Schema

SQL DDL files for the `kep` schema in PostgreSQL. Run these files in order to set up the knowledge store tables and indexes.

## Files

| File | Description |
|---|---|
| `V001__kep_base_schema.sql` | All table definitions: documents, chunks, service graph tables |
| `V002__kep_indexes.sql` | HNSW vector index, GIN full-text index, B-tree indexes on FK columns |

## Setup Order

```bash
# 1. Create the pgvector extension and schema (requires superuser)
psql -f ../../scripts/setup_pgvector.sql

# 2. Create tables
psql -f V001__kep_base_schema.sql

# 3. Create indexes (run AFTER bulk data load for performance)
psql -f V002__kep_indexes.sql
```

**Important**: create the HNSW index (in V002) **after** bulk inserting all chunks, not before. Building HNSW on an empty table and inserting later is less efficient than building it after bulk load.

## Key Design Points

### kep_chunks — the main search table

```sql
CREATE TABLE kep.kep_chunks (
    id          UUID PRIMARY KEY,
    document_id UUID REFERENCES kep.kep_documents(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,      -- 'go_service', 'java_service', 'schema', 'confluence', 'ixm_spec', 'log_patterns'
    service_name TEXT,              -- NULL for schema/spec chunks
    content     TEXT NOT NULL,      -- the actual text of the chunk
    content_tsv TSVECTOR,          -- auto-populated by trigger for full-text search
    embedding   VECTOR(384),        -- sentence-transformers/all-MiniLM-L6-v2 output
    token_count INTEGER NOT NULL,
    metadata    JSONB DEFAULT '{}'
);
```

The `content_tsv` column is populated automatically by a `BEFORE INSERT OR UPDATE` trigger that calls `to_tsvector('english', content)`. Do not populate it manually.

### Vector search query pattern

```sql
-- Semantic search: find the 20 most similar chunks to a query embedding
SELECT id, content, service_name, source_type,
       1 - (embedding <=> $1::vector) AS similarity
FROM kep.kep_chunks
ORDER BY embedding <=> $1::vector
LIMIT 20;
```

### Full-text search query pattern

```sql
-- Keyword search
SELECT id, content, service_name, source_type,
       ts_rank_cd(content_tsv, query) AS rank
FROM kep.kep_chunks, plainto_tsquery('english', 'enrollment biometric') AS query
WHERE content_tsv @@ query
ORDER BY rank DESC
LIMIT 20;
```

## pgvector Version Requirement

The HNSW index requires **pgvector 0.5.0 or later**. Check your version:

```sql
SELECT installed_version FROM pg_available_extensions WHERE name = 'vector';
```

If the version is 0.4.x, only IVFFlat indexes are available (slightly lower recall, no online updates). The schema will fall back to IVFFlat if HNSW is not available. See the comments in `V002__kep_indexes.sql` for the IVFFlat alternative.
