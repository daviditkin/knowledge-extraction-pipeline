# Task List

Each task has: a clear description, acceptance criteria, files to create or modify, and dependencies. Tasks are ordered within phases. An AI agent should complete tasks in order unless otherwise noted.

Use this status notation in PR descriptions or commit messages:
- `[TODO]` — not started
- `[IN PROGRESS]` — being worked on
- `[DONE]` — completed and tested
- `[BLOCKED]` — waiting on a dependency

---

## Phase 1: Extractors

### TASK-001: Create shared pydantic models for extractor output

**Status**: TODO
**Priority**: P0 (everything else depends on this)
**File**: `extractors/shared/models.py`

Create pydantic v2 models representing all extractor output types. Every field must have a description. Use `model_config = ConfigDict(extra='forbid')` to catch schema drift early.

Models required:
- `HandlerInfo`: `name: str`, `http_method: Optional[str]`, `http_path: Optional[str]`, `grpc_service: Optional[str]`, `grpc_method: Optional[str]`, `request_type: Optional[str]`, `response_type: Optional[str]`, `calls_services: list[str]`, `db_queries: list[str]`, `file: str`, `line_start: int`, `line_end: int`
- `LogEvent`: `service: str`, `level: str`, `message_template: str`, `fields: dict[str, str]`, `file: str`, `line: int`
- `ServiceDoc`: `name: str`, `language: str` (go/java), `directory: str`, `handlers: list[HandlerInfo]`, `external_deps: list[str]`, `db_tables_referenced: list[str]`, `log_events: list[LogEvent]`, `file_hash_map: dict[str, str]`
- `ColumnInfo`: `name: str`, `data_type: str`, `nullable: bool`, `default: Optional[str]`, `is_primary_key: bool`
- `TableInfo`: `name: str`, `columns: list[ColumnInfo]`, `indexes: list[str]`, `foreign_keys: list[str]`, `migration_version_created: str`
- `SchemaDoc`: `tables: list[TableInfo]`, `views: list[str]`, `as_of_migration: str`
- `SpecField`: `name: str`, `xml_element: str`, `data_type: str`, `required: bool`, `cardinality: str`, `validation_pattern: Optional[str]`, `allowed_values: list[str]`, `description: str`
- `SpecDoc`: `message_type: str`, `xml_root_element: str`, `direction: str` (inbound/outbound/both), `fields: list[SpecField]`, `description: str`
- `DocPage`: `page_id: str`, `title: str`, `service_refs: list[str]`, `space_key: str`, `content_markdown: str`, `last_updated: str`, `author: str`
- `LogPattern`: `service: str`, `level: str`, `message_template: str`, `fields: list[str]`, `file: str`, `line: int`

**Acceptance criteria**:
- `from extractors.shared.models import ServiceDoc, SchemaDoc, SpecDoc, DocPage, LogPattern` works
- `ServiceDoc.model_validate({...})` raises `ValidationError` on an invalid input
- All models have JSON schema exportable via `model.model_json_schema()`

---

### TASK-002: Create shared file walker and output writer

**Status**: TODO
**Priority**: P0
**Files**: `extractors/shared/file_walker.py`, `extractors/shared/output_writer.py`

**file_walker.py**: Implement `FileWalker` class.
- `__init__(self, root_dir: str, include_patterns: list[str], exclude_patterns: list[str])`
- `walk() -> Generator[Path, None, None]`: yield matching files
- `compute_hash(path: Path) -> str`: SHA-256 hex digest of file content
- `load_hash_cache(cache_path: Path) -> dict[str, str]`: load previous hashes from JSON
- `save_hash_cache(cache_path: Path, hashes: dict[str, str])`: atomically write hash cache
- `changed_files(cache_path: Path) -> list[Path]`: return files whose hash has changed since last run

**output_writer.py**: Implement `OutputWriter` class.
- `__init__(self, output_dir: Path)`
- `write_service_doc(doc: ServiceDoc)`: write to `output_dir/services/<name>.json`
- `write_schema_doc(doc: SchemaDoc)`: write to `output_dir/schema.json`
- `write_spec_doc(doc: SpecDoc)`: write to `output_dir/ixm-spec/<message_type>.json`
- `write_doc_page(page: DocPage)`: write to `output_dir/confluence/<page_id>.json`
- `write_log_patterns(patterns: list[LogPattern])`: write to `output_dir/log-patterns.json`
- All writes must be atomic (write to `.tmp` file, then rename)

**Acceptance criteria**:
- `FileWalker("/some/dir", ["*.go"], ["vendor/**"]).walk()` yields `.go` files excluding vendor
- After calling `write_service_doc`, the JSON file is valid and deserializes back to `ServiceDoc`
- `changed_files()` returns empty list on second run with no file changes
- `changed_files()` returns the modified file on second run after a file change

---

### TASK-003: Build Go AST helper binary

**Status**: TODO
**Priority**: P0
**Files**: `extractors/go-service/ast_helper/main.go`, `extractors/go-service/ast_helper/go.mod`

Write a Go program that:
1. Accepts a single argument: path to a `.go` file
2. Parses the file using `go/ast` and `go/parser`
3. Emits a JSON object to stdout containing:
   - `package`: package name
   - `imports`: list of import paths
   - `functions`: list of `{name, params, returns, start_line, end_line, body_text}`
   - `http_handlers`: list of `{pattern, handler_func, registration_line}` — found by detecting calls to `http.HandleFunc`, `mux.Handle`, `r.Get/Post/Put/Delete/Patch` (gorilla/mux, chi patterns), `router.GET/POST` (gin)
   - `grpc_registrations`: list of `{service_name, handler_func, registration_line}` — found by detecting `pb.RegisterXxxServer(...)` calls
   - `struct_types`: list of `{name, fields: [{name, type, json_tag}]}`
   - `log_calls`: list of `{func_name, args, line}` — found by detecting calls where the function name contains `log`, `Log`, `Info`, `Warn`, `Error`, `Debug`, `With`, `Emit`
4. Exits 0 on success, 1 on parse error (with error message to stderr)

Use only stdlib packages (`go/ast`, `go/parser`, `go/token`, `encoding/json`, `os`, `fmt`).

**Acceptance criteria**:
- `go build -o ast_helper ./ast_helper` compiles without errors
- Running on a sample Go file with a `http.HandleFunc` call produces JSON with that handler in `http_handlers`
- Running on a sample Go file with `slog.Info("msg", "key", value)` produces a log_call entry
- Running on a malformed Go file exits 1 and prints the parse error

---

### TASK-004: Build Go service extractor

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-001, TASK-002, TASK-003
**File**: `extractors/go-service/extractor.py`

Implement `GoServiceExtractor` class:
- `__init__(self, service_root: Path, ast_helper_binary: Path, output_writer: OutputWriter)`
- `extract_service(service_dir: Path) -> ServiceDoc`: detect service name (from directory name or module path in `go.mod`), walk all `.go` files (excluding `_test.go` and `vendor/`), invoke ast_helper for each, aggregate results into `ServiceDoc`
- `extract_all_services(services_root: Path)`: walk `services_root`, detect service directories (has `go.mod` or `main.go`), call `extract_service` for each
- Handler detection: map ast_helper `http_handlers` → `HandlerInfo` with method/path parsed from the registration string
- Dependency detection: parse `imports` for known internal service client packages (configurable prefix, e.g., `company.com/services/`)
- DB query detection: find `db.Query`, `db.Exec`, `tx.Query`, `tx.Exec`, `sqlx.Get`, `sqlx.Select` calls in function bodies; extract the SQL string literal if present
- Log event detection: map ast_helper `log_calls` → `LogEvent` with message template and key-value fields parsed from the argument list

**Acceptance criteria**:
- Running on the actual codebase produces one `ServiceDoc` per Go service directory
- At least 90% of HTTP handler registrations are detected (spot-checked against known services)
- Service dependencies (internal imports) are correctly identified
- Log events list is non-empty for services that have log calls

---

### TASK-005: Build Java Spring Boot service extractor

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-001, TASK-002
**File**: `extractors/java-service/extractor.py`

Implement `JavaServiceExtractor` class:
- Use `javalang` library to parse `.java` files
- Detect service name from `spring.application.name` in `application.properties` or `application.yml`, or fall back to the Maven `artifactId` in `pom.xml`
- Find `@RestController` and `@Controller` classes
- Extract methods annotated with `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`, `@RequestMapping`; record HTTP method, path, request/response types
- Find `@FeignClient` or `RestTemplate` usage for service-to-service calls
- Find `@Autowired` `JdbcTemplate` / `NamedParameterJdbcTemplate` / Spring Data repository usages for DB access
- Find SLF4J log calls: `log.info(...)`, `log.warn(...)`, `log.error(...)`, `log.debug(...)`; extract message template and parameters
- Emit one `ServiceDoc` per service (one `pom.xml` = one service)

**Acceptance criteria**:
- Produces one `ServiceDoc` per Java service in the codebase
- All `@GetMapping`/`@PostMapping` handlers are detected
- `@FeignClient` targets are listed in `external_deps`
- SLF4J log calls appear in `log_events`

---

### TASK-006: Build Flyway schema extractor

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-001, TASK-002
**File**: `extractors/flyway-schema/extractor.py`

Implement `FlywaySchemaExtractor`:
- `__init__(self, migrations_dir: Path)`
- `extract() -> SchemaDoc`: parse all `V*.sql` files in version order, apply each migration to an in-memory schema state, return final state
- Migration file name parsing: support both `V1__description.sql` and `V1_1__description.sql` (Flyway versioning)
- SQL parsing: use `sqlglot` to parse DDL statements
- Schema state tracking:
  - `CREATE TABLE foo (...)`: add table with columns
  - `ALTER TABLE foo ADD COLUMN bar TYPE`: add column
  - `ALTER TABLE foo DROP COLUMN bar`: remove column
  - `ALTER TABLE foo ALTER COLUMN bar TYPE new_type`: update column type
  - `ALTER TABLE foo ADD CONSTRAINT fk_... FOREIGN KEY (col) REFERENCES other_table(col)`: add FK
  - `CREATE INDEX / CREATE UNIQUE INDEX`: add index to table
  - `DROP TABLE`: remove table
  - `CREATE VIEW`: record view name (content not parsed in detail)
- Repeatable migrations (`R__*.sql`): parse but tag separately; apply after versioned
- Skip `B__*.sql` (baseline) and `U__*.sql` (undo) scripts — record their presence but don't invert

**Acceptance criteria**:
- Running on the actual Flyway migrations directory produces a `SchemaDoc` with all expected tables
- Column types match what's in the database (spot-checked via `\d tablename` in psql)
- Foreign key relationships are captured
- Running twice on unchanged migrations produces identical output

---

### TASK-007: Build Confluence extractor

**Status**: TODO
**Priority**: P2
**Depends on**: TASK-001, TASK-002
**File**: `extractors/confluence/extractor.py`

Implement `ConfluenceExtractor` with two modes:

**Mode 1: XML space export** (preferred for restricted networks)
- Parse `entities.xml` (Confluence space XML export format)
- Find `<object class="Page">` elements
- Extract: page ID, title, parent page, body HTML, labels, last-modified date, author
- Convert body HTML to Markdown using `markdownify` or a custom converter
- Clean up Confluence-specific macros: `ac:structured-macro`, `ac:parameter` → try to convert info/note/warning panels to Markdown blockquotes; strip code macros but preserve content

**Mode 2: REST API JSON dump** (for use during build phase with internet access)
- Input: directory of JSON files, one per page (from `GET /rest/api/content/{id}?expand=body.storage`)
- Parse the Confluence storage format (XML-like) from `body.storage.value`
- Same HTML → Markdown conversion

**Service association**: associate each page with services by:
1. Exact title match: if page title is exactly a service name, associate it
2. Label match: if page has a Confluence label matching a service name
3. Content scan: if the page body mentions a service name 3+ times in the first 500 words

**Acceptance criteria**:
- Produces one `DocPage` per Confluence page in the export
- Markdown content is readable (no escaped HTML entities, no raw `<div>` tags)
- `service_refs` contains correct service associations for at least the pages with exact title matches

---

### TASK-008: Build IXM spec extractor

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-001, TASK-002
**File**: `extractors/ixm-spec/extractor.py`

Implement `IxmSpecExtractor`:
- `__init__(self, spec_dir: Path)`: the IXM spec may be one file or a directory of XML files
- `extract() -> list[SpecDoc]`: parse all spec XML files, emit one SpecDoc per top-level message type

XML parsing strategy:
- Use `lxml.etree` for parsing
- The IXM spec is an XML Schema (XSD) or a custom XML format — handle both:
  - If XSD: walk `xs:element` and `xs:complexType` definitions; extract `xs:restriction` for validation patterns and enumeration values; note `minOccurs`/`maxOccurs` for cardinality
  - If custom format: look for patterns like `<MessageType name="EnrollRequest">`, `<Field name="..." type="..." required="true"/>`, `<AllowedValues>`, `<ValidationRule pattern="..."/>`
- For each message type: extract all field definitions recursively (handle nested complex types)
- Infer direction (inbound/outbound/both) from message type name conventions (e.g., "...Request" vs "...Response") or explicit annotations if present

**Acceptance criteria**:
- Produces one `SpecDoc` per top-level message type in the IXM spec
- Field names, types, and cardinalities are correct (spot-checked against spec document)
- Validation patterns (regex) are preserved verbatim
- All allowed values for enumeration fields are captured

---

### TASK-009: Build log patterns extractor

**Status**: TODO
**Priority**: P2
**Depends on**: TASK-001, TASK-002
**File**: `extractors/log-patterns/extractor.py`

Implement `LogPatternExtractor`:
- Scan all `.go` and `.java` files in the codebase
- Go patterns to detect (regex-based):
  - `slog.Info("message", "key1", val1, "key2", val2)`
  - `logger.Info("message", slog.String("key1", val1))`
  - `span.AddEvent("event_name", trace.WithAttributes(attribute.String("key", val)))`
  - `logger.With("key", val).Info("message")`
  - `otel`-style: look for `.Emit(` calls on any variable named `logger` or `log`
- Java patterns to detect:
  - `log.info("message {}", arg)` — SLF4J parameterized logging
  - `log.error("message", exception)`
  - MDC usage: `MDC.put("key", value)` — extract key as a field name
- For each detected log call, extract:
  - Service name (from directory context)
  - Log level
  - Message template (the first string argument)
  - Field names (the string literal keys in key-value pairs, or `{}` placeholder names from SLF4J)
  - File and line number

**Acceptance criteria**:
- Detects `slog.Info(...)`, `slog.Error(...)`, `slog.Warn(...)`, `slog.Debug(...)` patterns
- Detects SLF4J `log.info(...)` etc. with parameterized messages
- Does not produce false positives on string operations that happen to contain "log"
- Output `log-patterns.json` has at least as many entries as there are log call sites in the codebase (within 10% margin, spot-checked)

---

### TASK-010: Create extraction orchestrator script

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-004, TASK-005, TASK-006, TASK-007, TASK-008, TASK-009
**File**: `scripts/run_extractors.py`

Implement a script that:
1. Loads `config/config.yaml`
2. Instantiates and runs each extractor in order
3. Reports progress: `[1/6] Go service extractor... 45/120 services processed`
4. Reports errors without stopping: if one service fails to parse, log the error and continue
5. Writes a summary to `extracted/extraction_report.json`: total files processed, errors, hashes, timestamp
6. Supports `--changed-only` flag to skip unchanged files
7. Supports `--extractor <name>` flag to run a single extractor

**Acceptance criteria**:
- Running with `--config config/config.yaml` on the full codebase completes (with errors logged but not fatal)
- `extracted/extraction_report.json` is written with correct counts
- `--changed-only` skips unmodified files on second run
- `--extractor go-service` runs only the Go extractor

---

## Phase 2: Knowledge Store

### TASK-011: Write PostgreSQL schema DDL

**Status**: TODO
**Priority**: P0
**Depends on**: TASK-001
**Files**: `knowledge-store/schema/V001__kep_base_schema.sql`, `knowledge-store/schema/V002__kep_indexes.sql`

Write the full DDL for all knowledge store tables. See `docs/design/knowledge-store.md` for the complete schema design.

V001 must include:
- `CREATE SCHEMA IF NOT EXISTS kep`
- `kep.kep_documents`: source document registry
- `kep.kep_chunks`: chunked content with vector embedding column
- `kep.kep_chunk_sources`: maps chunks to source files (for incremental re-indexing)
- `kep.kep_service_nodes`: service registry
- `kep.kep_service_edges`: service-to-service call relationships
- `kep.kep_service_tables`: service-to-DB-table relationships
- `kep.kep_service_log_events`: service log event catalog
- `kep.kep_ixm_message_refs`: service-to-IXM-message-type relationships

V002 must include:
- HNSW index on `kep_chunks.embedding` using `vector_cosine_ops`
- GIN index on `kep_chunks.content_tsv` (tsvector column)
- Regular B-tree indexes on all foreign key columns and commonly filtered columns

**Acceptance criteria**:
- `psql -f V001__kep_base_schema.sql` and `psql -f V002__kep_indexes.sql` complete without errors on a fresh PostgreSQL 14 instance with pgvector installed
- All tables created in the `kep` schema
- Foreign key constraints reference the correct columns

---

### TASK-012: Implement embedding model loader

**Status**: TODO
**Priority**: P0
**File**: `knowledge-store/embeddings/model_loader.py`

Implement `EmbeddingModelLoader`:
- `__init__(self, model_path: str)`: path to local model directory
- `load() -> SentenceTransformer`: load the model from disk (no internet)
- `embed(texts: list[str]) -> np.ndarray`: encode texts, return shape `(N, 384)`
- `embed_query(text: str) -> np.ndarray`: encode a single query text, shape `(384,)`
- Handle: model not found (clear error message with instructions to run `scripts/download_models.py`)
- Handle: CUDA not available → silently use CPU (no error)
- Batch size: configurable, default 32, to avoid OOM on large documents

Also write `scripts/download_models.py`:
- Download `sentence-transformers/all-MiniLM-L6-v2` from HuggingFace (requires internet)
- Save to `models/all-MiniLM-L6-v2/`
- Verify download with a test encoding

**Acceptance criteria**:
- `loader.embed(["hello world", "biometric enrollment"])` returns shape `(2, 384)`
- Loading from an already-downloaded model path does not make any network requests
- If model path does not exist, raises `ModelNotFoundError` with a message explaining how to download

---

### TASK-013: Implement content-type-aware chunker

**Status**: TODO
**Priority**: P0
**Depends on**: TASK-001
**File**: `knowledge-store/embeddings/chunker.py`

Implement `Chunker` class with type-specific chunking strategies:

- `chunk_service_doc(doc: ServiceDoc) -> list[Chunk]`: one chunk per handler. Chunk content = `"Service: {name}\nHandler: {name}\nPath: {method} {path}\n\n{body_text}"`. Include the function body (first 200 lines max).
- `chunk_schema_doc(doc: SchemaDoc) -> list[Chunk]`: one chunk per table. Content = table DDL as CREATE TABLE statement. Include all columns, constraints, foreign keys.
- `chunk_spec_doc(doc: SpecDoc) -> list[Chunk]`: one chunk per message type. Content = description + field list formatted as a table.
- `chunk_doc_page(page: DocPage) -> list[Chunk]`: split at H2/H3 headings. If a section exceeds 600 tokens, split at paragraph boundaries. Min chunk size: 100 tokens.
- `chunk_log_patterns(patterns: list[LogPattern]) -> list[Chunk]`: group by service; one chunk per service containing all log patterns in a formatted list.

Each `Chunk` model: `id: str` (UUID), `source_type: str`, `service_name: Optional[str]`, `content: str`, `token_count: int`, `metadata: dict`

**Acceptance criteria**:
- `chunk_service_doc(service_doc)` returns one chunk per handler, with content containing the handler body
- `chunk_schema_doc(schema_doc)` returns one chunk per table
- `chunk_doc_page(doc_page)` splits a 5000-word page into multiple chunks at heading boundaries
- No chunk exceeds 700 tokens

---

### TASK-014: Implement database client and upsert logic

**Status**: TODO
**Priority**: P0
**File**: `knowledge-store/db_client.py`

Implement `KepDatabaseClient`:
- `__init__(self, dsn: str)`: connect to PostgreSQL
- `upsert_chunks(chunks: list[Chunk], embeddings: np.ndarray)`: insert or update chunks and their embeddings. Use `ON CONFLICT (id) DO UPDATE`.
- `delete_chunks_for_source(source_file: str)`: delete all chunks derived from a source file (for re-indexing)
- `upsert_service_node(doc: ServiceDoc)`: upsert into `kep_service_nodes`
- `upsert_service_edges(doc: ServiceDoc)`: upsert all edges from `doc.external_deps`
- `upsert_service_tables(doc: ServiceDoc)`: upsert all table references from `doc.db_tables_referenced`
- `upsert_service_log_events(doc: ServiceDoc)`: upsert all log events
- Use connection pooling (psycopg2 ThreadedConnectionPool or asyncpg pool)
- Bulk insert with `executemany` or `COPY` for efficiency

**Acceptance criteria**:
- Inserting 50,000 chunks with 384-dim embeddings completes in under 5 minutes
- Re-running upsert on the same data does not increase row count
- `delete_chunks_for_source` removes exactly the chunks from that file

---

### TASK-015: Implement knowledge graph builder

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-014
**File**: `knowledge-store/graph/builder.py`

Implement `GraphBuilder`:
- `build_from_service_docs(docs: list[ServiceDoc])`: process all service docs and populate graph tables
- `detect_call_type(import_path: str) -> str`: classify as "http_client", "grpc_client", or "shared_lib" based on import path patterns (configurable: `http_client_patterns`, `grpc_client_patterns`)
- `infer_table_operation(query_text: str) -> str`: classify SQL string as "read", "write", or "both" based on SELECT/INSERT/UPDATE/DELETE keywords
- Handle bidirectional relationships: if service A calls service B and service B is also listed in A's external deps, create one edge (A→B), not two

Also implement `knowledge-store/graph/queries.py`:
- `get_service_neighbors(db: KepDatabaseClient, service_name: str, depth: int = 1) -> list[ServiceEdge]`
- `get_services_by_table(db: KepDatabaseClient, table_name: str) -> list[str]`
- `get_tables_for_service(db: KepDatabaseClient, service_name: str) -> list[str]`
- `get_call_path(db: KepDatabaseClient, from_service: str, to_service: str) -> list[list[str]]` (all paths up to depth 5, using recursive CTE)

**Acceptance criteria**:
- After building from service docs, `kep_service_edges` contains the correct relationships (spot-checked)
- `get_call_path("enrollment-svc", "biometric-store-svc")` returns a list of paths
- `get_services_by_table("biometric_records")` returns all services that reference that table

---

### TASK-016: Implement full indexer pipeline

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-012, TASK-013, TASK-014, TASK-015
**File**: `rag/indexer/indexer.py`

Implement `Indexer`:
- `__init__(self, config: Config, db: KepDatabaseClient, embedder: EmbeddingModelLoader)`
- `index_all(extracted_dir: Path)`: walk all JSON files in extracted/, chunk, embed, insert
- `index_service_docs()`, `index_schema()`, `index_confluence()`, `index_ixm_spec()`, `index_log_patterns()`
- Progress reporting: log at INFO level every 100 chunks: `"Indexed 1200/5000 chunks..."`
- `index_changed_only(extracted_dir: Path, hash_cache: Path)`: only index files that changed since last run
- Batch embedding: collect 32 chunks at a time, embed batch, insert batch

**Acceptance criteria**:
- Full indexing of all extracted data completes without errors
- `SELECT COUNT(*) FROM kep.kep_chunks` is non-zero after indexing
- Re-running with `index_changed_only` on unchanged data does not modify the database

---

## Phase 3: RAG Query Engine

### TASK-017: Implement query analyzer

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-016
**File**: `rag/query/analyzer.py`

Implement `QueryAnalyzer`:
- `analyze(query: str, known_services: list[str], known_tables: list[str], known_message_types: list[str]) -> QueryAnalysis`
- `QueryAnalysis` model: `original_query: str`, `intent: QueryIntent`, `mentioned_services: list[str]`, `mentioned_tables: list[str]`, `mentioned_message_types: list[str]`, `keywords: list[str]`
- `QueryIntent` enum: `EXPLAIN_FLOW`, `TRACE_REQUEST`, `DEBUG_ERROR`, `DOCUMENT_ENDPOINT`, `FIND_RELATED`, `GENERAL_QUESTION`
- Intent detection rules (no ML required):
  - TRACE_REQUEST: query contains "trace", "follow", "flow through", "path", "request lifecycle"
  - DEBUG_ERROR: query contains "error", "exception", "fail", "broken", "why is", "not working"
  - EXPLAIN_FLOW: query contains "how does", "explain", "describe", "what does", "walk me through"
  - DOCUMENT_ENDPOINT: query contains "document", "write docs", "API spec", "swagger", "openapi"
  - FIND_RELATED: query contains "related to", "depends on", "calls", "who uses", "what uses"
  - Default: GENERAL_QUESTION
- Entity extraction: use fuzzy string matching against known entity lists (fuzzywuzzy ratio > 75)

**Acceptance criteria**:
- `analyze("how does the enrollment flow work", services=["enrollment-svc", ...])` returns `intent=EXPLAIN_FLOW, mentioned_services=["enrollment-svc"]`
- `analyze("why is the biometric service returning 500 errors")` returns `intent=DEBUG_ERROR`
- `analyze("what tables does enrollment-svc write to")` returns `intent=FIND_RELATED`

---

### TASK-018: Implement vector and keyword search

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-014, TASK-012
**Files**: `rag/query/vector_search.py`, `rag/query/keyword_search.py`

**vector_search.py**:
- `VectorSearch.__init__(self, db: KepDatabaseClient, embedder: EmbeddingModelLoader)`
- `search(query: str, k: int = 20, source_type_filter: Optional[str] = None) -> list[SearchResult]`
- SQL: `SELECT id, content, source_type, service_name, metadata, 1 - (embedding <=> $1) AS score FROM kep.kep_chunks [WHERE source_type = $2] ORDER BY score DESC LIMIT $3`
- Pass embedding as a properly formatted pgvector literal

**keyword_search.py**:
- `KeywordSearch.__init__(self, db: KepDatabaseClient)`
- `search(query: str, k: int = 20) -> list[SearchResult]`
- Pre-process query: remove stop words, handle plural/singular (simple suffix removal), join with `&` for tsquery
- SQL: `SELECT id, content, source_type, service_name, metadata, ts_rank_cd(content_tsv, query) AS score FROM kep.kep_chunks, plainto_tsquery('english', $1) query WHERE content_tsv @@ query ORDER BY score DESC LIMIT $2`

**SearchResult** model: `chunk_id: str`, `content: str`, `source_type: str`, `service_name: Optional[str]`, `score: float`, `metadata: dict`, `rank: Optional[int]`

**Acceptance criteria**:
- `VectorSearch.search("biometric enrollment handler")` returns relevant Go handler chunks
- `KeywordSearch.search("enrollment request")` returns chunks containing those words
- Both return results ranked by score descending
- Both handle empty results gracefully (return empty list, not error)

---

### TASK-019: Implement graph-based context expansion

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-015
**File**: `rag/query/graph_search.py`

Implement `GraphSearch`:
- `__init__(self, db: KepDatabaseClient)`
- `expand_for_services(service_names: list[str]) -> list[SearchResult]`: fetch all chunks associated with the given services and their direct neighbors (depth=1)
- `expand_for_tables(table_names: list[str]) -> list[SearchResult]`: fetch all service chunks for services that reference the given tables
- `expand_for_message_types(message_types: list[str]) -> list[SearchResult]`: fetch all service chunks for services that send/receive the given IXM message types
- Score for graph results: 0.7 (fixed score, below strong semantic matches but above weak ones)

**Acceptance criteria**:
- `expand_for_services(["enrollment-svc"])` returns chunks from enrollment-svc and all services it calls
- `expand_for_tables(["biometric_records"])` returns chunks from all services that reference that table
- Returns empty list if service/table/message type not found (no error)

---

### TASK-020: Implement context assembler and query engine

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-017, TASK-018, TASK-019
**Files**: `rag/query/assembler.py`, `rag/query/engine.py`

**assembler.py**: `ContextAssembler`
- `assemble(vector_results, keyword_results, graph_results, token_budget: int = 6000) -> ContextPackage`
- Merge all results by chunk_id (deduplicate)
- Apply RRF (Reciprocal Rank Fusion): for each unique chunk, score = sum over lists of `1 / (60 + rank)`
- Sort by RRF score descending
- Add chunks to context greedily until token budget is reached
- Group final chunks by source_type: code_chunks, schema_chunks, spec_chunks, doc_chunks, log_chunks

`ContextPackage` model: `code_chunks: list[SearchResult]`, `schema_chunks: list[SearchResult]`, `spec_chunks: list[SearchResult]`, `doc_chunks: list[SearchResult]`, `log_chunks: list[SearchResult]`, `total_tokens: int`, `query_analysis: QueryAnalysis`

**engine.py**: `QueryEngine`
- `__init__(self, db, embedder, config)`
- `query(user_query: str, token_budget: int = 6000) -> ContextPackage`
- Runs analyzer, vector search, keyword search, graph search in parallel (ThreadPoolExecutor)
- Calls assembler to merge results

**Acceptance criteria**:
- `engine.query("enrollment service HTTP endpoints")` returns a `ContextPackage` with code chunks containing Go handler functions
- RRF scoring causes chunks appearing in both vector and keyword results to rank higher
- `total_tokens` in the returned package does not exceed `token_budget`
- The query completes in under 3 seconds on a warm database

---

## Phase 4: Prompt Builder

### TASK-021: Implement prompt templates

**Status**: TODO
**Priority**: P1
**Files**: All files in `prompt-builder/templates/`

Write all 6 prompt templates as plain text files with `{slot_name}` placeholders. See `docs/design/prompt-builder.md` for the full template structure. Each template must:
- Start with a SYSTEM CONTEXT section describing the biometric identity system
- Have clearly labeled sections: RELEVANT CODE, DATABASE SCHEMA, IXM SPEC CONTEXT, LOG PATTERNS, DOCUMENTATION
- End with the QUESTION section
- Include a brief instruction to ChatGPT about how to use the provided context

**Acceptance criteria**:
- All 6 templates exist and contain all required sections
- Templates can be loaded with Python's `string.Template` or `str.format_map`
- No template exceeds 500 tokens when rendered with empty slots

---

### TASK-022: Implement prompt builder

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-020, TASK-021
**Files**: `prompt-builder/builder.py`, `prompt-builder/length_manager.py`

**builder.py**: `PromptBuilder`
- `__init__(self, templates_dir: Path, config: Config)`
- `build(query: str, context: ContextPackage) -> BuiltPrompt`
- Select template based on `context.query_analysis.intent`
- Format each context section (code, schema, spec, docs, logs) into readable text blocks
- Inject into template slots
- Run length manager if total exceeds budget

**length_manager.py**: `LengthManager`
- `trim_to_budget(prompt: str, budget_tokens: int) -> str`
- `estimate_tokens(text: str) -> int`: simple estimate: `len(text.split()) * 1.3`
- Trim sections in priority order: log_patterns → docs → spec → schema → code
- Never trim below 2 chunks in any section

`BuiltPrompt` model: `prompt_text: str`, `token_estimate: int`, `template_used: str`, `sections_included: list[str]`, `sections_trimmed: list[str]`

**Acceptance criteria**:
- `builder.build("how does enrollment work", context)` returns a `BuiltPrompt`
- `prompt_text` is properly formatted and all section headers are present
- `token_estimate` is within 20% of actual token count
- If context has 10,000 tokens of data, `prompt_text` is trimmed to fit the budget

---

### TASK-023: Implement prompt CLI

**Status**: TODO
**Priority**: P1
**Depends on**: TASK-022
**File**: `prompt-builder/cli.py`

Implement a standalone CLI for building prompts:
```
Usage: python -m prompt_builder.cli [OPTIONS] QUERY

Options:
  --config PATH    Config file [default: config/config.yaml]
  --save           Save prompt to prompts/saved/
  --intent TEXT    Override intent detection (explain_flow, trace_request, etc.)
  --budget INT     Token budget [default: 8000]
  --no-color       Disable colored output
```

Output: print the formatted prompt to stdout. If `--save`, also write to `prompts/saved/YYYYMMDD_HHMMSS_<slug>.txt`.

**Acceptance criteria**:
- Running `python -m prompt_builder.cli "how does enrollment work"` prints a complete prompt
- `--save` creates a file in `prompts/saved/`
- `--intent trace_request` forces the trace_request template regardless of query content

---

## Phase 5: UI

### TASK-024: Implement FastAPI web server

**Status**: TODO
**Priority**: P2
**Depends on**: TASK-022
**Files**: `ui/server.py`, `ui/templates/index.html`, `ui/templates/result.html`, `ui/static/app.js`

Implement a FastAPI web server:
- `GET /`: serve `index.html` — query form with text area, intent dropdown, submit button
- `POST /query`: accept `{query: str, intent: Optional[str]}`, run `QueryEngine.query()` and `PromptBuilder.build()`, return rendered `result.html` partial (for HTMX swap)
- `GET /prompt/{id}`: retrieve a saved prompt by ID
- Static files served from `ui/static/`

`index.html`: query form with HTMX (`hx-post="/query"`, `hx-target="#result"`, `hx-swap="innerHTML"`). Show loading indicator during query. Include copy-to-clipboard button.

`app.js`: only one function — `copyToClipboard(elementId)`. Use `navigator.clipboard.writeText()` with a fallback to `document.execCommand('copy')`.

HTMX bundled as `ui/static/htmx.min.js` (download from htmx.org, include in the bundle).

**Acceptance criteria**:
- Server starts with `python -m ui.server --config config/config.yaml`
- Submitting a query returns the prompt in under 3 seconds
- Copy button copies the prompt text to clipboard
- UI works without internet (all assets served locally)

---

### TASK-025: Implement CLI interface

**Status**: TODO
**Priority**: P2
**Depends on**: TASK-022
**File**: `ui/cli.py`

Use `typer` and `rich` to implement:
- `query QUERY_TEXT`: build and print enriched prompt
- `show-service SERVICE_NAME`: print service info (handlers, deps, tables, log events) using Rich table
- `show-graph SERVICE_NAME [--depth N]`: print ASCII service dependency graph using Rich tree
- `show-schema TABLE_NAME`: print table DDL and foreign keys
- `reindex [--changed-only]`: run extraction and indexing pipeline

**Acceptance criteria**:
- All commands run without errors
- `show-graph` displays a tree or ASCII diagram of service dependencies
- `show-schema` displays column names, types, and FK relationships in a table

---

## Infrastructure Tasks

### TASK-026: Write requirements.txt and bundling scripts

**Status**: TODO
**Priority**: P0
**Files**: `requirements.txt`, `scripts/bundle_for_deployment.py`, `scripts/download_models.py`

`requirements.txt` must pin all versions and include:
- pydantic>=2.0
- psycopg2-binary
- sentence-transformers
- torch (CPU only: torch==2.1.0+cpu)
- sqlglot
- lxml
- javalang
- markdownify
- fastapi
- uvicorn
- jinja2
- typer
- rich
- python-json-logger
- numpy
- scikit-learn
- fuzzywuzzy[speedup]
- python-dotenv

`bundle_for_deployment.py`:
- `pip download -r requirements.txt --dest vendor/wheels/` (run on build machine with internet)
- Copy model files to `models/`
- Create tarball of the full project including vendor/wheels and models/
- Write `dist/pipeline-bundle.tar.gz`

**Acceptance criteria**:
- `pip install --no-index --find-links=vendor/wheels -r requirements.txt` succeeds after running bundle script
- Bundle tarball is under 2GB
- Model files are included in the bundle

---

### TASK-027: Write configuration schema and example config

**Status**: TODO
**Priority**: P0
**Files**: `config/example.yaml`, `config/config_schema.py`

See `config/example.yaml` for the full example. `config_schema.py` validates the config with pydantic.

**Acceptance criteria**:
- Loading `config/example.yaml` with `Config.from_yaml()` does not raise validation errors
- All required fields have sensible defaults where possible
- Missing required fields raise clear validation errors

---

### TASK-028: Write setup and helper scripts

**Status**: TODO
**Priority**: P1
**Files**: `scripts/setup_pgvector.sql`, `scripts/run_pipeline.py`

`setup_pgvector.sql`:
- `CREATE EXTENSION IF NOT EXISTS vector`
- `CREATE SCHEMA IF NOT EXISTS kep`
- Notes in comments about minimum pgvector version (0.5.0 for HNSW)

`run_pipeline.py`: run full pipeline in sequence: extract → index → verify
- Verify: run a test query and print results to confirm the system is working

**Acceptance criteria**:
- `psql -f scripts/setup_pgvector.sql` completes on a fresh PostgreSQL instance
- `python scripts/run_pipeline.py --config config/config.yaml` runs the full pipeline
