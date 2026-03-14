# Architecture

## System Overview

The system is split across two environments. The **build environment** (public internet, Claude Code, your development machine) is where the tooling is constructed, models are downloaded, and Python wheels are vendored. The **runtime environment** (restricted private network) is where the fully bundled system runs against the actual codebase.

```
BUILD ENVIRONMENT (public internet)
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  Claude Code / Developer                                             │
│       │                                                              │
│       ▼                                                              │
│  knowledge-extraction-pipeline/                                      │
│  ├── Download embedding model (80MB)                                 │
│  ├── Vendor Python wheels (no-index install)                         │
│  ├── Write extractors, RAG engine, prompt builder                    │
│  └── Bundle → dist/pipeline-bundle.tar.gz                           │
│                                                                      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ physical transfer (USB / file share)
                               ▼
RESTRICTED NETWORK ENVIRONMENT
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  Source Code (120 Go + Java services)                                │
│  Flyway Migrations  IXM XML Spec  Confluence Export                  │
│       │                                                              │
│       ▼                                                              │
│  ┌─────────────────────────────┐                                     │
│  │     EXTRACTION LAYER        │                                     │
│  │  go-service extractor       │                                     │
│  │  java-service extractor     │                                     │
│  │  flyway-schema extractor    │                                     │
│  │  confluence extractor       │                                     │
│  │  ixm-spec extractor         │                                     │
│  │  log-patterns extractor     │                                     │
│  └──────────────┬──────────────┘                                     │
│                 │ Extracted knowledge (JSON documents)               │
│                 ▼                                                    │
│  ┌─────────────────────────────┐                                     │
│  │     KNOWLEDGE STORE         │                                     │
│  │                             │                                     │
│  │  PostgreSQL (existing)      │                                     │
│  │  ┌────────────────────┐     │                                     │
│  │  │ pgvector extension │     │                                     │
│  │  │  documents table   │     │                                     │
│  │  │  embeddings (1536d)│     │                                     │
│  │  │  chunks table      │     │                                     │
│  │  └────────────────────┘     │                                     │
│  │  ┌────────────────────┐     │                                     │
│  │  │ Knowledge Graph    │     │                                     │
│  │  │  service_nodes     │     │                                     │
│  │  │  service_edges     │     │                                     │
│  │  │  service_tables    │     │                                     │
│  │  │  service_log_events│     │                                     │
│  │  └────────────────────┘     │                                     │
│  └──────────────┬──────────────┘                                     │
│                 │                                                    │
│                 ▼                                                    │
│  ┌─────────────────────────────┐                                     │
│  │       RAG ENGINE            │                                     │
│  │  hybrid search              │                                     │
│  │  (keyword + semantic)       │                                     │
│  │  context assembly           │                                     │
│  │  result ranking             │                                     │
│  └──────────────┬──────────────┘                                     │
│                 │ Context package (ranked snippets + metadata)       │
│                 ▼                                                    │
│  ┌─────────────────────────────┐                                     │
│  │     PROMPT BUILDER          │                                     │
│  │  template selection         │                                     │
│  │  context injection          │                                     │
│  │  length management          │                                     │
│  │  output: copyable prompt    │                                     │
│  └──────────────┬──────────────┘                                     │
│                 │                                                    │
│                 ▼                                                    │
│  ┌─────────────────────────────┐                                     │
│  │     WEB UI / CLI            │                                     │
│  │  query input                │                                     │
│  │  prompt display             │                                     │
│  │  copy-to-clipboard          │                                     │
│  └──────────────┬──────────────┘                                     │
│                 │ User copies prompt                                 │
│                 ▼                                                    │
│  ┌─────────────────────────────┐                                     │
│  │     ChatGPT (web UI)        │                                     │
│  │  User pastes enriched       │                                     │
│  │  prompt into browser        │                                     │
│  └─────────────────────────────┘                                     │
│                                                                      │
│  (Optional: Ollama local LLM replaces ChatGPT step)                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## Data Flow: End to End

### Phase A: Extraction (run once, then incrementally)

```
Source files on disk
      │
      ├──► go-service extractor
      │         Reads .go files via tree-sitter or go/ast subprocess
      │         Emits: ServiceDoc { name, handlers[], deps[], db_queries[], log_events[] }
      │
      ├──► java-service extractor
      │         Reads .java files via javalang
      │         Emits: ServiceDoc { name, endpoints[], beans[], log_events[] }
      │
      ├──► flyway-schema extractor
      │         Reads V*.sql migration files in version order
      │         Replays ALTER/CREATE statements to compute current schema
      │         Emits: SchemaDoc { tables[], columns[], indexes[], fks[] }
      │
      ├──► confluence extractor
      │         Reads XML export or REST API JSON dump
      │         Emits: DocPage { title, service_ref, content_markdown, last_updated }
      │
      ├──► ixm-spec extractor
      │         Reads IXM XML spec files
      │         Emits: SpecDoc { message_type, fields[], validation_rules[], xml_element_path }
      │
      └──► log-patterns extractor
                Scans all .go/.java files for OTEL/Splunk log call patterns
                Emits: LogPattern { service, level, message_template, fields[], file, line }

All extractors write JSON to: extracted/
  extracted/services/          ← one JSON per service
  extracted/schema.json        ← full DB schema
  extracted/confluence/        ← one JSON per page
  extracted/ixm-spec/          ← one JSON per message type
  extracted/log-patterns.json  ← all log patterns
```

### Phase B: Indexing (run after extraction)

```
extracted/ JSON files
      │
      ▼
Chunker (per content type)
      │  Go handler → chunk at function boundary
      │  Schema → chunk at table boundary
      │  Confluence page → chunk at H2/H3 boundary, ~500 tokens
      │  IXM spec → chunk at message type boundary
      │  Log patterns → group by service, chunk by 20 patterns
      │
      ▼
Embedding model (sentence-transformers/all-MiniLM-L6-v2, local)
      │  Each chunk → 384-dimensional float vector
      │
      ▼
PostgreSQL (pgvector)
      │  INSERT INTO kep_chunks (id, source_type, service_name, content, embedding, metadata)
      │
      ▼
Knowledge graph builder
      │  Parse ServiceDoc.deps → INSERT INTO kep_service_edges (from_service, to_service, call_type)
      │  Parse ServiceDoc.db_queries → INSERT INTO kep_service_tables (service, table, operation)
      │  Parse ServiceDoc.log_events → INSERT INTO kep_service_log_events (service, event_name, fields)
```

### Phase C: Query and Prompt Building (interactive use)

```
User types query: "How does the biometric enrollment flow work?"
      │
      ▼
Query Analyzer
      │  Extract keywords: [biometric, enrollment, flow]
      │  Detect intent: "trace request" or "explain flow"
      │  Identify entity mentions: service names, table names, IXM message types
      │
      ├──► Semantic search (pgvector cosine similarity)
      │         embed(query) → top-K similar chunks
      │
      ├──► Keyword search (PostgreSQL full-text search)
      │         tsvector search on content → matching chunks
      │
      ├──► Graph traversal
      │         If service name mentioned: fetch all edges, tables, log events for that service
      │         If table name mentioned: fetch all services that read/write it
      │
      ▼
Context Assembler
      │  Merge results from all three search paths
      │  Deduplicate (same chunk from multiple paths → keep once)
      │  Score and rank: semantic score × keyword boost × graph relevance
      │  Truncate to token budget (default: 6000 tokens, leaving 2000 for ChatGPT response)
      │
      ▼
Prompt Builder
      │  Select template based on detected intent
      │  Inject context sections: [SYSTEM CONTEXT], [CODE SNIPPETS], [SCHEMA], [LOG PATTERNS]
      │  Add user question at end
      │  Output: formatted prompt string
      │
      ▼
Web UI / CLI
      │  Display prompt with syntax highlighting
      │  "Copy to clipboard" button
      │  User pastes into ChatGPT web UI
```

## Technology Choices With Rationale

### pgvector (via existing PostgreSQL)

The codebase already uses PostgreSQL managed with Flyway. Adding the pgvector extension to an existing instance costs nothing in operational overhead. A dedicated vector database (Pinecone, Weaviate, Qdrant) would require new infrastructure, container deployments, and operational knowledge the team may not have. pgvector with HNSW indexes delivers sub-100ms query times at the scale of 120 services (~50K chunks), which is adequate for interactive use.

### sentence-transformers/all-MiniLM-L6-v2

This model is 80MB, runs on CPU in ~50ms per chunk, requires no GPU, and achieves competitive retrieval performance on technical text. It was designed specifically for semantic similarity tasks. The full model and tokenizer can be downloaded once, serialized to disk, and loaded offline. Larger models (e.g., all-mpnet-base-v2) are not necessary at this scale.

### Python for the pipeline

Go would be the natural choice for parsing Go code, but Python has a substantially richer ecosystem for NLP (sentence-transformers, spaCy, NLTK), SQL parsing (sqlglot), XML processing (lxml), and data pipeline work (pandas, pydantic). The Go service extractor uses a subprocess call to `go/ast` via a small Go helper binary for accuracy, but the pipeline orchestration is Python. Java parsing is done with `javalang` (pure Python), avoiding a JVM dependency.

### FastAPI + HTMX for the UI

A React/Next.js frontend would require Node.js, npm, a build step, and a JavaScript bundle — all of which add deployment complexity. FastAPI with HTMX delivers a responsive web UI from a single Python process with Jinja2 templates. The prompt copy-to-clipboard interaction works with a single JS line. This is deployable as a single `python -m ui.server` command.

### No Neo4j

The knowledge graph (service→service calls, service→DB tables, service→log events) is represented as adjacency tables in PostgreSQL. For a system with 120 services and perhaps 500 edges, this is entirely adequate. PostgreSQL recursive CTEs handle multi-hop traversal. Neo4j would be unnecessary complexity and an additional service to manage.

### Tree-sitter for parsing (optional)

tree-sitter has Python bindings and can parse Go, Java, and SQL without requiring the language's own toolchain. It is more robust than regex-based parsing and faster than running `go vet` or `javac` for analysis. Where tree-sitter is unavailable or difficult to bundle, regex-based fallbacks cover the most important patterns (HTTP handler registration, gRPC service definitions, log call sites).

## Key Design Decisions

### Decision 1: Extract-then-Index vs. Live Parsing

All extraction is done as a batch step that writes JSON files. The indexer reads these files independently. This means:
- You can re-index without re-extracting (fast iteration on chunking/embedding strategy)
- You can inspect extracted data to verify correctness before indexing
- Extraction can run on a developer machine with access to the full codebase; only the index needs to reach the PostgreSQL instance

### Decision 2: Hybrid Search (Not Pure Semantic)

Pure vector search misses exact matches for technical identifiers (service names, table names, IXM message type codes). Pure keyword search misses conceptual queries. Hybrid search — parallel vector + full-text, with score fusion — handles both. The PostgreSQL full-text search (tsvector/tsquery) is already built into the database, requiring no additional tooling.

### Decision 3: Prompt Builder Output Is Plain Text

The prompt builder does not call the ChatGPT API (none is available). It writes the enriched prompt to stdout or the UI, and the user copies it. This is not a limitation — it is the explicit design target. Future versions may add Ollama integration for in-pipeline completions.

### Decision 4: Knowledge Graph Stored Relationally

The service dependency graph is sparse (most services call 3–10 other services). A relational adjacency list in PostgreSQL is as fast as a graph database for the query patterns here: "what does service X call?", "what services write to table Y?", "what is the call path from service A to service B?" (answered with a recursive CTE, 5–10 hops, completes in milliseconds).

### Decision 5: Incremental Re-extraction

Each extractor tracks file content hashes. On re-run, it skips files that have not changed. This allows the pipeline to be run nightly or on-commit without full re-processing. New or modified files are re-extracted and their old chunks are deleted and replaced in the knowledge store.

### Decision 6: Schema Prefix (kep_)

All pipeline tables in PostgreSQL use the `kep_` prefix (Knowledge Extraction Pipeline) to avoid collisions with the application's own schema. The pipeline can share the application's PostgreSQL instance by targeting a separate schema (`kep`) or by using a dedicated database.
