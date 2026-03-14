# Implementation Plan

This plan is organized into five phases. Each phase has clear goals, concrete deliverables, dependencies, and estimated effort. Phases are designed so that each delivers standalone value — you can stop after Phase 2 and still have a working knowledge store you can query with SQL.

---

## Phase 1: Extractors

**Goal**: Parse every knowledge source in the system and emit clean, structured JSON. After this phase you have a `extracted/` directory with machine-readable knowledge about every service, the database schema, the IXM spec, all Confluence docs, and every log call site in the codebase.

**Duration estimate**: 2–3 weeks

### Deliverables

| Deliverable | Description |
|---|---|
| `extractors/shared/file_walker.py` | Recursive directory walker with glob filtering and content-hash tracking |
| `extractors/shared/output_writer.py` | Write extracted JSON with consistent schema, atomic file writes |
| `extractors/shared/models.py` | Pydantic models for all extractor output types (ServiceDoc, SchemaDoc, etc.) |
| `extractors/go-service/extractor.py` | Parse Go source files, emit ServiceDoc per service |
| `extractors/go-service/ast_helper/main.go` | Thin Go binary: reads a .go file, emits JSON AST summary |
| `extractors/java-service/extractor.py` | Parse Java Spring Boot source, emit ServiceDoc per service |
| `extractors/flyway-schema/extractor.py` | Replay Flyway migrations, emit SchemaDoc |
| `extractors/confluence/extractor.py` | Parse Confluence XML export or REST dump, emit DocPage per page |
| `extractors/ixm-spec/extractor.py` | Parse IXM XML spec, emit SpecDoc per message type |
| `extractors/log-patterns/extractor.py` | Scan all source files for OTEL log calls, emit LogPattern list |
| `scripts/run_extractors.py` | Orchestrate all extractors, write to `extracted/` |

### Key Design Points

**Go service extractor**: Uses a small Go helper (`ast_helper/main.go`) compiled once and invoked as a subprocess. The helper uses `go/ast` and `go/types` to produce a reliable JSON summary of each file: package name, function declarations with their signatures, `http.HandleFunc` / `mux.Handle` / gRPC `RegisterXxxServer` calls, struct types, and all log call sites. The Python extractor aggregates per-file outputs into a per-service `ServiceDoc`.

**Java service extractor**: Uses `javalang` (pure Python Java parser) to find `@RestController`, `@RequestMapping`, `@GetMapping`, `@PostMapping` etc., extract method signatures and path patterns, identify `@Autowired` / `@Bean` dependencies, and find Logback/SLF4J log call sites.

**Flyway schema extractor**: Sorts migration files by version (V1__, V2__, etc.), parses each with `sqlglot`, and maintains an in-memory representation of the current schema state. Handles CREATE TABLE, ALTER TABLE ADD COLUMN, ALTER TABLE DROP COLUMN, CREATE INDEX, ADD FOREIGN KEY, DROP TABLE. Emits a `SchemaDoc` representing the schema as of the latest migration.

**Confluence extractor**: Supports two input modes: (a) Confluence XML space export (the `.xml` format from Confluence's built-in export), and (b) a JSON dump from the Confluence REST API (`/rest/api/content`). Converts page body HTML to clean Markdown. Associates pages with service names based on page title and label matching.

**IXM spec extractor**: Parses the XML spec to extract message type definitions, field names with data types and cardinality, validation rules (regex patterns, allowed values, required/optional), and XML element paths. Emits one `SpecDoc` per top-level message type.

**Log patterns extractor**: Uses regex to match OTEL-style log calls in Go (`slog.Info(...)`, `logger.With(...).Info(...)`, `otel.Logger.Emit(...)`) and Java (SLF4J `log.info(...)`, `log.error(...)`). Extracts the message template string and all key-value pairs passed as structured fields.

### Dependencies

- Python 3.10+, pydantic v2, lxml, javalang, sqlglot, tree-sitter (optional)
- Go toolchain available to compile `ast_helper/main.go`
- Confluence XML export or REST API access (run once before transfer to restricted network)
- IXM XML spec file(s)
- Flyway migration directory

### Acceptance Criteria

- Running `python scripts/run_extractors.py --config config/config.yaml` completes without errors on the full codebase
- `extracted/services/` contains one JSON file per Go/Java service, each validating against the `ServiceDoc` schema
- `extracted/schema.json` accurately represents the current DB schema (validated manually against known tables)
- `extracted/confluence/` contains one JSON file per page, with readable Markdown content
- `extracted/ixm-spec/` contains one JSON file per message type with all fields present
- `extracted/log-patterns.json` lists at least 80% of the known log call sites (spot-checked against source)

---

## Phase 2: Knowledge Store

**Goal**: Take the extracted JSON files and load them into a PostgreSQL instance with pgvector. After this phase you have a queryable knowledge base you can query with SQL (`SELECT ... ORDER BY embedding <=> $1 LIMIT 10`) and a knowledge graph you can traverse.

**Duration estimate**: 1–2 weeks

### Deliverables

| Deliverable | Description |
|---|---|
| `knowledge-store/schema/V001__kep_base_schema.sql` | Base DDL: documents, chunks, embeddings, graph tables |
| `knowledge-store/schema/V002__kep_indexes.sql` | HNSW vector indexes, GIN full-text indexes |
| `knowledge-store/embeddings/model_loader.py` | Load sentence-transformers model from local path |
| `knowledge-store/embeddings/embedder.py` | Batch embedding generation with progress tracking |
| `knowledge-store/embeddings/chunker.py` | Content-type-aware chunking (code, schema, docs, spec, logs) |
| `knowledge-store/graph/builder.py` | Build knowledge graph from ServiceDoc relationships |
| `knowledge-store/graph/queries.py` | Graph traversal queries (neighbors, reachable, shared tables) |
| `rag/indexer/indexer.py` | Orchestrate chunking → embedding → upsert to PostgreSQL |
| `scripts/setup_pgvector.sql` | One-time setup: CREATE EXTENSION vector, CREATE SCHEMA kep |

### Key Design Points

**Chunking strategy by content type**:
- Go/Java handler code: chunk at function boundary. One chunk = one handler function (typically 20–150 lines). Include the function signature, doc comment, and body.
- Schema: chunk at table boundary. One chunk = one table definition (CREATE TABLE + all ALTERs applied). Include column names, types, constraints, and foreign keys.
- Confluence pages: split at H2/H3 headings. Each section is a chunk of ~500 tokens. If a section exceeds 600 tokens, split at paragraph boundaries.
- IXM spec: chunk at message type boundary. One chunk = one message type with all its fields and validation rules.
- Log patterns: group by service. One chunk = all log patterns for one service (service name + list of {level, message, fields}).

**Embedding model**: `sentence-transformers/all-MiniLM-L6-v2`. Downloaded with `python scripts/download_models.py` before transfer to restricted network. Stored in `models/all-MiniLM-L6-v2/`. The embedder loads this from disk, no internet required.

**pgvector HNSW index**: The `kep_chunks.embedding` column uses an HNSW index with `m=16, ef_construction=64`. This allows approximate nearest neighbor search in ~5ms for a 50K chunk store. The index is built after bulk insert, not incrementally.

**Knowledge graph schema**:
```sql
kep_service_nodes      -- one row per service (name, language, path, metadata jsonb)
kep_service_edges      -- (from_service, to_service, call_type, details jsonb)
kep_service_tables     -- (service, schema_table, operation: read/write/both)
kep_service_log_events -- (service, event_name, level, fields jsonb)
kep_ixm_message_refs   -- (service, message_type, direction: inbound/outbound)
```

### Dependencies

- Phase 1 completed (extracted/ directory populated)
- PostgreSQL 14+ with pgvector extension available
- Python: psycopg2 or asyncpg, sentence-transformers, torch (CPU only)

### Acceptance Criteria

- `python -m rag.indexer --config config/config.yaml` completes without errors
- `SELECT COUNT(*) FROM kep.kep_chunks` returns a non-zero count
- A cosine similarity query returns relevant results for "biometric enrollment handler"
- `SELECT * FROM kep.kep_service_edges LIMIT 20` shows real service-to-service call relationships
- Full-text search on `kep_chunks.content_tsv` returns results for known service names

---

## Phase 3: RAG Query Engine

**Goal**: Given a natural language query, return a ranked list of relevant context snippets from the knowledge store. After this phase you have a Python API you can call: `query_engine.search("how does enrollment work")` → list of `ContextChunk` objects.

**Duration estimate**: 1 week

### Deliverables

| Deliverable | Description |
|---|---|
| `rag/query/analyzer.py` | Detect query intent, extract entity mentions, build search plan |
| `rag/query/vector_search.py` | Embed query, run pgvector cosine similarity search |
| `rag/query/keyword_search.py` | PostgreSQL full-text search (tsvector/tsquery) |
| `rag/query/graph_search.py` | Expand results via knowledge graph traversal |
| `rag/query/assembler.py` | Merge, deduplicate, rank, and truncate to token budget |
| `rag/query/engine.py` | Unified query interface combining all search strategies |

### Key Design Points

**Query analyzer**: Uses spaCy (if available) or a lightweight keyword extractor to identify:
- Service names: matched against `kep_service_nodes.name` using fuzzy matching (fuzz ratio > 80)
- Table names: matched against extracted schema table names
- IXM message types: matched against `kep_ixm_message_refs.message_type`
- Intent: classified into one of {explain_flow, trace_request, debug_error, document_endpoint, find_related, general_question} based on question words and trigger phrases

**Hybrid search**: Three parallel queries:
1. Vector search: `SELECT id, content, source_type, service_name, 1 - (embedding <=> $1) AS score FROM kep.kep_chunks ORDER BY score DESC LIMIT 20`
2. Full-text: `SELECT id, content, source_type, service_name, ts_rank(content_tsv, query) AS score FROM kep.kep_chunks, to_tsquery($1) query WHERE content_tsv @@ query`
3. Graph expansion: if entities detected, fetch all chunks for the related services/tables

**Score fusion (Reciprocal Rank Fusion)**: Each search path produces a ranked list. RRF score = Σ (1 / (60 + rank_in_list)). This handles the different score scales from vector vs. BM25.

**Token budget management**: The assembler counts tokens using a simple word-count heuristic (multiply by 1.3 for token estimate) or a tiktoken-based count if available. Chunks are added to the context in ranked order until the budget is exhausted.

### Dependencies

- Phase 2 completed (knowledge store populated)
- Python: numpy, scikit-learn (for RRF), optional spaCy

### Acceptance Criteria

- `engine.search("enrollment service HTTP endpoints")` returns chunks containing Go handler functions from the enrollment service
- `engine.search("what tables does the biometric service write to")` returns graph-expanded results showing `kep_service_tables` entries
- `engine.search("IXM enrollment request message fields")` returns IXM spec chunks for the enrollment message type
- Results for a known query match results from a direct SQL query on the knowledge store

---

## Phase 4: Prompt Builder and ChatGPT Integration Helper

**Goal**: Turn a query + context package into a well-formatted, copy-pasteable prompt for ChatGPT. After this phase, a user can type a question, get an enriched prompt, and paste it into the ChatGPT web UI and receive a high-quality answer.

**Duration estimate**: 1 week

### Deliverables

| Deliverable | Description |
|---|---|
| `prompt-builder/builder.py` | Assemble query + context into a formatted prompt |
| `prompt-builder/length_manager.py` | Trim context sections to fit within token budget |
| `prompt-builder/templates/explain_flow.txt` | Template: explain how a flow or service works |
| `prompt-builder/templates/trace_request.txt` | Template: trace a request through the system |
| `prompt-builder/templates/debug_error.txt` | Template: debug an error given log context |
| `prompt-builder/templates/document_endpoint.txt` | Template: write documentation for an endpoint |
| `prompt-builder/templates/find_related.txt` | Template: find services related to X |
| `prompt-builder/templates/general_question.txt` | Template: general question with context |

### Key Design Points

**Template structure**: Each template has named slots filled by the context assembler:
```
[SYSTEM CONTEXT]
You are analyzing a biometric identity management system with 120 Go microservices.
The system uses an XML-based IXM spec for external communication, PostgreSQL for storage,
and OTEL for logging. Respond based only on the context provided below.

[RELEVANT CODE]
{code_snippets}

[DATABASE SCHEMA]
{schema_context}

[IXM SPEC CONTEXT]
{spec_context}

[LOG PATTERNS]
{log_context}

[DOCUMENTATION]
{docs_context}

[QUESTION]
{user_question}
```

**Length management strategy**: If total prompt exceeds 8000 tokens (leaving headroom for ChatGPT's context window), sections are trimmed in this priority order (lowest priority trimmed first):
1. Log patterns (most repetitive)
2. Documentation (least precise)
3. IXM spec context (keep if question is spec-related)
4. Schema context (keep if question is data-related)
5. Code snippets (highest value, trim last)

**Saved prompts**: The builder can write prompts to `prompts/saved/YYYYMMDD_HHMMSS_<slug>.txt` for later retrieval. Users can build up a library of prompts for recurring questions.

### Dependencies

- Phase 3 completed (RAG query engine working)
- Optional: tiktoken for accurate token counting

### Acceptance Criteria

- Running `python -m prompt_builder.cli "how does enrollment work"` prints a complete, well-formatted prompt
- The prompt contains code snippets, schema info, and docs relevant to enrollment
- The prompt fits within 8000 tokens
- Template selection matches the query intent (trace_request template for "how does X flow work")

---

## Phase 5: Web UI and CLI Interface

**Goal**: A usable interface for team members on the restricted network. Either a web browser UI or a command-line tool they can run from their terminal.

**Duration estimate**: 1 week

### Deliverables

| Deliverable | Description |
|---|---|
| `ui/server.py` | FastAPI application, serves HTML UI |
| `ui/templates/index.html` | Main query interface with HTMX for dynamic updates |
| `ui/templates/result.html` | Rendered prompt with copy button |
| `ui/static/app.js` | Minimal JS: copy-to-clipboard, nothing else |
| `ui/cli.py` | Typer-based CLI: query, show-chunks, show-graph, build-prompt |
| `ui/cli_commands/` | One file per CLI command |

### Key Design Points

**Web UI flow**:
1. User opens browser to `http://localhost:8080`
2. Types question in text area, selects optional intent hint (dropdown)
3. HTMX POST to `/query` → returns rendered prompt in `<div id="result">`
4. "Copy Prompt" button copies the prompt text (navigator.clipboard.writeText)
5. "Show Sources" toggle reveals the source chunks that were included

**CLI interface**:
```bash
# Build a prompt for a question
python -m ui.cli query "how does biometric enrollment work"

# Show what's in the knowledge store for a service
python -m ui.cli show-service enrollment-service

# Show the service dependency graph
python -m ui.cli show-graph --from-service enrollment-service --depth 2

# Re-run extraction and indexing
python -m ui.cli reindex --changed-only
```

**Offline-first**: The web UI serves all static assets from local files. No CDN dependencies. HTMX is bundled as a single JS file in `ui/static/`. No Node.js, no npm.

### Dependencies

- Phase 4 completed (prompt builder working)
- Python: fastapi, uvicorn, jinja2, typer, rich (for CLI output)

### Acceptance Criteria

- Web UI starts with `python -m ui.server` and is accessible at `http://localhost:8080`
- Submitting a query returns a formatted prompt in under 3 seconds
- "Copy Prompt" button works in Chrome and Firefox
- CLI `query` command prints the prompt to stdout and optionally saves to file
- CLI `show-service` displays a service's handlers, dependencies, and log patterns in readable format

---

## Cross-Phase Concerns

### Testing Strategy

Each phase should have unit tests runnable with `pytest`. Integration tests require a PostgreSQL instance with pgvector and are tagged `@pytest.mark.integration`. A fixture provides a test database populated with a small synthetic dataset (2 fake services, 5 tables, 10 Confluence pages, 3 IXM message types).

### Configuration

All file paths, database credentials, chunk sizes, and model paths are read from `config/config.yaml`. No hardcoded paths anywhere. The config schema is validated with pydantic at startup.

### Logging

The pipeline itself uses Python's `logging` module with structured JSON output (via `python-json-logger`). Log levels: DEBUG for chunk-level operations, INFO for extractor progress, WARNING for parse failures, ERROR for database errors.

### Incremental Updates

The file walker computes SHA-256 of file content. The extractor stores hashes in `extracted/.hashes.json`. On re-run, only changed files are re-extracted. The indexer stores `(file_hash, chunk_id)` in `kep.kep_chunk_sources`. On re-index, it deletes old chunks for changed files and inserts new ones.
