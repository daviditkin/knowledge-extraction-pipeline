# Knowledge Store Design

The knowledge store is the central repository for all extracted and indexed knowledge. It lives in PostgreSQL (the team's existing database) using the `kep` schema, with the pgvector extension providing vector similarity search.

## Architecture Decision: One PostgreSQL Instance for Everything

The knowledge store uses two PostgreSQL capabilities in combination:
1. **pgvector**: stores 384-dimensional float vectors and supports approximate nearest-neighbor search via HNSW indexes
2. **Standard PostgreSQL full-text search**: `tsvector`/`tsquery` for keyword matching
3. **Standard relational tables**: knowledge graph stored as adjacency lists

This avoids running Elasticsearch, Qdrant, Weaviate, or any other dedicated search infrastructure. The team already operates PostgreSQL; adding the pgvector extension is a one-line change (`CREATE EXTENSION vector`).

The knowledge store can be in the same PostgreSQL database as the application (different schema: `kep`) or in a separate database on the same instance. Both work identically.

---

## Schema Design

### Core Tables

#### `kep.kep_documents` — Source document registry

Tracks every source document that has been indexed. One row per file/page, not per chunk.

```sql
CREATE TABLE kep.kep_documents (
    id              UUID PRIMARY KEY,
    source_type     TEXT NOT NULL,          -- 'go_service', 'java_service', 'schema', 'confluence', 'ixm_spec', 'log_patterns'
    source_path     TEXT NOT NULL,          -- absolute path to source file, or page URL
    source_name     TEXT NOT NULL,          -- human-readable name (service name, page title, etc.)
    content_hash    TEXT NOT NULL,          -- SHA-256 of source content (for change detection)
    extracted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    indexed_at      TIMESTAMPTZ,            -- NULL until indexed
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_type, source_path)
);
```

#### `kep.kep_chunks` — Content chunks with vector embeddings

The primary search table. Each row is one chunk of content with its vector embedding and full-text search vector.

```sql
CREATE TABLE kep.kep_chunks (
    id              UUID PRIMARY KEY,
    document_id     UUID NOT NULL REFERENCES kep.kep_documents(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL,          -- denormalized from kep_documents for query performance
    service_name    TEXT,                   -- NULL for non-service chunks (e.g., schema, spec)
    chunk_index     INTEGER NOT NULL,       -- position within the parent document
    content         TEXT NOT NULL,          -- the full text of the chunk
    content_tsv     TSVECTOR,              -- for full-text search (updated by trigger)
    embedding       VECTOR(384),            -- sentence-transformers/all-MiniLM-L6-v2 output
    token_count     INTEGER NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Trigger to keep content_tsv in sync
CREATE OR REPLACE FUNCTION kep.update_chunk_tsv()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', NEW.content);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER kep_chunks_tsv_trigger
BEFORE INSERT OR UPDATE ON kep.kep_chunks
FOR EACH ROW EXECUTE FUNCTION kep.update_chunk_tsv();
```

The `metadata` JSONB column stores type-specific attributes:
- For go/java chunks: `{"handler_name": "EnrollHandler", "http_method": "POST", "http_path": "/api/v1/enroll"}`
- For schema chunks: `{"table_name": "biometric_records", "column_count": 8}`
- For spec chunks: `{"message_type": "EnrollRequest", "direction": "inbound", "field_count": 5}`
- For confluence chunks: `{"page_id": "12345", "page_title": "Enrollment Service", "section": "API Reference"}`
- For log pattern chunks: `{"service": "enrollment-svc", "pattern_count": 15}`

### Knowledge Graph Tables

#### `kep.kep_service_nodes` — Service registry

```sql
CREATE TABLE kep.kep_service_nodes (
    id              UUID PRIMARY KEY,
    service_name    TEXT NOT NULL UNIQUE,
    language        TEXT NOT NULL,          -- 'go' or 'java'
    service_path    TEXT NOT NULL,          -- directory path
    module_path     TEXT,                   -- Go module path or Maven groupId:artifactId
    handler_count   INTEGER NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `kep.kep_service_edges` — Service-to-service call relationships

```sql
CREATE TABLE kep.kep_service_edges (
    id              UUID PRIMARY KEY,
    from_service    TEXT NOT NULL REFERENCES kep.kep_service_nodes(service_name) ON DELETE CASCADE,
    to_service      TEXT NOT NULL REFERENCES kep.kep_service_nodes(service_name) ON DELETE CASCADE,
    call_type       TEXT NOT NULL,          -- 'http_client', 'grpc_client', 'shared_lib'
    evidence_count  INTEGER NOT NULL DEFAULT 1, -- number of import references found
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (from_service, to_service, call_type)
);
```

#### `kep.kep_service_tables` — Service-to-database-table relationships

```sql
CREATE TABLE kep.kep_service_tables (
    id              UUID PRIMARY KEY,
    service_name    TEXT NOT NULL REFERENCES kep.kep_service_nodes(service_name) ON DELETE CASCADE,
    schema_table    TEXT NOT NULL,          -- table name (without schema prefix)
    operation       TEXT NOT NULL,          -- 'read', 'write', 'both'
    evidence        TEXT[],                 -- SQL query snippets that led to this detection
    UNIQUE (service_name, schema_table)
);
```

#### `kep.kep_service_log_events` — Service log event catalog

```sql
CREATE TABLE kep.kep_service_log_events (
    id              UUID PRIMARY KEY,
    service_name    TEXT NOT NULL REFERENCES kep.kep_service_nodes(service_name) ON DELETE CASCADE,
    level           TEXT NOT NULL,          -- 'DEBUG', 'INFO', 'WARN', 'ERROR'
    message_template TEXT NOT NULL,
    fields          TEXT[] NOT NULL,        -- array of field names
    source_file     TEXT NOT NULL,
    source_line     INTEGER NOT NULL,
    UNIQUE (service_name, message_template, source_file, source_line)
);
```

#### `kep.kep_ixm_message_refs` — Service-to-IXM-message-type relationships

```sql
CREATE TABLE kep.kep_ixm_message_refs (
    id              UUID PRIMARY KEY,
    service_name    TEXT NOT NULL REFERENCES kep.kep_service_nodes(service_name) ON DELETE CASCADE,
    message_type    TEXT NOT NULL,          -- e.g., 'EnrollRequest'
    direction       TEXT NOT NULL,          -- 'sends', 'receives', 'both'
    UNIQUE (service_name, message_type)
);
```

### Indexes

```sql
-- Vector similarity search (HNSW - built after bulk insert)
CREATE INDEX kep_chunks_embedding_hnsw_idx
ON kep.kep_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Full-text search
CREATE INDEX kep_chunks_content_tsv_gin_idx
ON kep.kep_chunks USING gin (content_tsv);

-- Common filter columns
CREATE INDEX kep_chunks_source_type_idx ON kep.kep_chunks (source_type);
CREATE INDEX kep_chunks_service_name_idx ON kep.kep_chunks (service_name);
CREATE INDEX kep_chunks_document_id_idx ON kep.kep_chunks (document_id);

-- Knowledge graph lookups
CREATE INDEX kep_service_edges_from_idx ON kep.kep_service_edges (from_service);
CREATE INDEX kep_service_edges_to_idx ON kep.kep_service_edges (to_service);
CREATE INDEX kep_service_tables_service_idx ON kep.kep_service_tables (service_name);
CREATE INDEX kep_service_tables_table_idx ON kep.kep_service_tables (schema_table);
CREATE INDEX kep_service_log_events_service_idx ON kep.kep_service_log_events (service_name);
CREATE INDEX kep_ixm_message_refs_message_type_idx ON kep.kep_ixm_message_refs (message_type);
```

---

## Chunking Strategy

Chunking is critical: chunks that are too large lose semantic precision; chunks that are too small lose context. The chunking strategy is tuned per content type.

### Go/Java Handler Chunks

**Unit**: one function (handler) per chunk.

**Content format**:
```
Service: enrollment-svc
Language: Go
File: cmd/server/handler.go
Handler: EnrollHandler
HTTP: POST /api/v1/enroll

func EnrollHandler(w http.ResponseWriter, r *http.Request) {
    // ... function body ...
}
```

**Rationale**: handlers are the primary unit of behavior in a microservice. Chunking at function boundaries preserves the complete logical unit. Including the service name, HTTP method, and path in the chunk header means the chunk is self-contained — a similarity search for "enrollment endpoint" will surface this chunk even without knowing the function name.

**Size management**: if a function exceeds 300 lines, include the first 200 lines of the body plus a truncation marker. The first 200 lines typically contain all the significant logic (struct field access, service calls, DB queries).

### Schema Chunks

**Unit**: one table per chunk.

**Content format**:
```
Table: biometric_records
Schema: public
Created in migration: V003__add_biometric_records.sql

Columns:
  id              UUID            NOT NULL  PRIMARY Key
  subject_id      UUID            NOT NULL  FK → subjects(id)
  modality        TEXT            NOT NULL  CHECK (modality IN ('FINGERPRINT', 'IRIS', 'FACE'))
  template_data   BYTEA           NOT NULL
  quality_score   NUMERIC(5,2)    NULL
  enrolled_at     TIMESTAMPTZ     NOT NULL  DEFAULT NOW()
  deleted_at      TIMESTAMPTZ     NULL

Indexes:
  idx_biometric_records_subject_id ON (subject_id)
  idx_biometric_records_modality ON (modality)
  idx_biometric_records_enrolled_at ON (enrolled_at)

Foreign Keys:
  subject_id → subjects(id) ON DELETE CASCADE
```

**Rationale**: a table definition is the natural unit of database schema knowledge. Including FK relationships in the chunk means a search for "subject biometric data" will surface both the subjects table chunk and the biometric_records chunk (which references subjects).

### Confluence Page Chunks

**Unit**: one section (H2 or H3 heading + content until the next heading) per chunk.

**Content format**:
```
Page: Enrollment Service
Section: API Reference

## API Reference

### POST /api/v1/enroll

Accepts a biometric enrollment request. The request body must conform to the
EnrollRequest IXM message format...

Parameters:
- BiometricID (required): UUID of the subject
- Template (required): base64-encoded biometric template
...
```

**Size limits**:
- Minimum chunk: 100 tokens (sections shorter than this are merged with the next section)
- Maximum chunk: 600 tokens (sections longer than this are split at paragraph boundaries)
- Target chunk: ~400 tokens

**Rationale**: splitting at heading boundaries respects the document's semantic structure. Each section is about one topic; keeping it intact maximizes the signal-to-noise ratio for semantic search.

### IXM Spec Chunks

**Unit**: one message type per chunk.

**Content format**:
```
IXM Message Type: EnrollRequest
Direction: inbound (front door receives this from external systems)
Description: Request to enroll a new biometric subject into the identity system

Fields:
  BiometricID    string   required  cardinality:one   pattern:[0-9a-f-]{36}
  Template       bytes    required  cardinality:one
  Modality       enum     required  cardinality:one   values:[FINGERPRINT, IRIS, FACE]
  SubjectName    string   optional  cardinality:one   maxLength:100
  CaptureDate    date     optional  cardinality:one
```

**Rationale**: message types are the unit of IXM knowledge. A search for "what fields does the enrollment request have" should surface exactly this chunk.

### Log Pattern Chunks

**Unit**: all log events for one service, as a single chunk.

**Content format**:
```
Service: enrollment-svc
Log Events:

[INFO]  "enrollment started"
        fields: biometric_id, service, correlation_id
        file: cmd/server/handler.go:55

[INFO]  "enrollment completed"
        fields: biometric_id, duration_ms, template_quality
        file: cmd/server/handler.go:78

[ERROR] "enrollment failed: template quality too low"
        fields: biometric_id, quality_score, threshold, error
        file: cmd/server/handler.go:92

[WARN]  "duplicate enrollment attempt"
        fields: biometric_id, existing_record_id
        file: cmd/server/handler.go:108
```

**Rationale**: grouping all log events for a service in one chunk makes the chunk useful for both debugging queries ("what should I see in the logs when enrollment fails?") and for understanding the service's observable behavior.

---

## Embedding Model

### Model: sentence-transformers/all-MiniLM-L6-v2

- **Architecture**: 6-layer MiniLM (distilled from BERT)
- **Output dimensions**: 384
- **Max input tokens**: 512 (chunks are sized to stay under this)
- **Model size**: ~80MB (tokenizer + weights)
- **CPU inference time**: ~5ms per chunk on a modern CPU; ~50ms/chunk in a batch of 32

### Why This Model

- **Offline**: fully self-contained after download; no network calls at inference time
- **Size**: 80MB fits comfortably in the deployment bundle
- **Performance**: strong performance on semantic similarity benchmarks (SBERT leaderboard consistently in top tier for small models)
- **Technical text**: performs well on code and technical documentation, not just natural language prose
- **No GPU required**: the team's restricted network servers likely do not have CUDA GPUs; this model runs fast enough on CPU

### Batch Processing

Embedding generation is the bottleneck in the indexing pipeline. The embedder processes chunks in batches of 32 (configurable). For 50,000 chunks at 50ms/batch, the full indexing takes approximately 25 minutes on CPU. This is acceptable for an offline batch process run once or nightly.

For the interactive query path (embedding a single user query), inference is ~5ms and imperceptible to the user.

### Loading for Offline Use

```python
from sentence_transformers import SentenceTransformer

# Load from local directory — no internet required
model = SentenceTransformer("models/all-MiniLM-L6-v2")

# Embed a batch of texts
embeddings = model.encode(texts, batch_size=32, show_progress_bar=True)
# Returns numpy array of shape (N, 384)
```

The `models/all-MiniLM-L6-v2/` directory must contain:
- `config.json`
- `tokenizer.json` and `tokenizer_config.json`
- `vocab.txt`
- `pytorch_model.bin` or `model.safetensors` (safetensors preferred for faster loading)
- `sentence_bert_config.json`
- `special_tokens_map.json`

---

## Knowledge Graph Design

### Why Relational Adjacency Tables (Not Neo4j)

The knowledge graph has ~120 service nodes, ~500 service edges, ~300 service-table relationships, and ~200 IXM message type relationships. At this scale, a relational adjacency list in PostgreSQL is:
- Faster for point lookups (indexed B-tree on `from_service` and `to_service`)
- Adequate for multi-hop traversal (recursive CTEs handle 5-hop paths in <10ms)
- Operationally free (same PostgreSQL instance, no new service to manage)

A graph database (Neo4j, ArangoDB) would add operational overhead and another service to manage on the restricted network, with no performance benefit at this scale.

### Graph Queries

**Find all services a given service calls (depth 1)**:
```sql
SELECT to_service, call_type, details
FROM kep.kep_service_edges
WHERE from_service = $1;
```

**Find the full call graph for a service (all reachable services)**:
```sql
WITH RECURSIVE reachable AS (
    -- Base case: direct neighbors
    SELECT to_service AS service, 1 AS depth, ARRAY[$1, to_service] AS path
    FROM kep.kep_service_edges
    WHERE from_service = $1
    UNION ALL
    -- Recursive case: neighbors of neighbors
    SELECT e.to_service, r.depth + 1, r.path || e.to_service
    FROM kep.kep_service_edges e
    JOIN reachable r ON r.service = e.from_service
    WHERE e.to_service != ALL(r.path)  -- cycle prevention
    AND r.depth < 5                     -- max depth
)
SELECT DISTINCT service, depth, path FROM reachable ORDER BY depth, service;
```

**Find all services that read or write a given table**:
```sql
SELECT service_name, operation
FROM kep.kep_service_tables
WHERE schema_table = $1;
```

**Find all tables shared between two services** (potential coupling indicator):
```sql
SELECT a.schema_table
FROM kep.kep_service_tables a
JOIN kep.kep_service_tables b ON a.schema_table = b.schema_table
WHERE a.service_name = $1 AND b.service_name = $2;
```

**Find services that handle a given IXM message type**:
```sql
SELECT service_name, direction
FROM kep.kep_ixm_message_refs
WHERE message_type = $1;
```

---

## Incremental Re-indexing

When source files change (after a code commit), only the affected chunks need to be re-indexed.

**Strategy**:
1. Re-run the extractor for changed files only (`--changed-only` flag)
2. For each re-extracted document, look up its `kep_documents.id`
3. Delete all `kep_chunks` rows with that `document_id` (cascade handles this)
4. Re-insert the new chunks with fresh embeddings
5. Update `kep_documents.content_hash` and `indexed_at`

**HNSW index behavior**: the HNSW index is updated incrementally on insert/delete (unlike IVF indexes which require a full rebuild). This means incremental re-indexing does not require dropping and rebuilding the vector index.

**Graph re-building**: the knowledge graph is also rebuilt incrementally. When a service doc is re-indexed, its `kep_service_edges`, `kep_service_tables`, and `kep_service_log_events` rows are deleted and re-inserted.

---

## Storage Estimates

For 120 Go services + a few Java services:

| Table | Estimated rows | Estimated size |
|---|---|---|
| kep_documents | ~500 (services + pages + spec + schema) | ~1 MB |
| kep_chunks | ~50,000 | ~200 MB (content) + ~75 MB (384-dim vectors at float32) |
| kep_service_nodes | ~125 | negligible |
| kep_service_edges | ~500 | negligible |
| kep_service_tables | ~300 | negligible |
| kep_service_log_events | ~5,000 | ~5 MB |
| kep_ixm_message_refs | ~200 | negligible |
| HNSW index | — | ~100 MB |
| GIN full-text index | — | ~50 MB |
| **Total** | — | **~430 MB** |

This is well within PostgreSQL's comfortable operating range and represents a small fraction of the storage used by the application's own data.
