# System Design Overview

## Problem Statement

A development team maintains a large biometric identity and encounter management system. The system consists of approximately 120 Go microservices, several Java Spring Boot services, a PostgreSQL database managed with Flyway migrations, an XML-based IXM specification that governs external message formats, Confluence documentation, and OTEL/Splunk-based structured logging.

The team works in a restricted private network. They have access to ChatGPT via a web browser, but no API access. They cannot install packages from the internet at runtime.

**The core problem**: the system is too large for any single person to hold in their head. When debugging, developers must manually correlate information from multiple sources — the code, the schema, the IXM spec, the logs, and the docs — before they can ask a useful question. The answers from ChatGPT are often poor because the question lacks context about the specific system.

## Goal

Build a system that:
1. Extracts all relevant knowledge from the codebase into a structured, queryable store
2. When a developer asks a question, retrieves the most relevant context from that store
3. Assembles the question + context into a high-quality prompt that can be pasted into ChatGPT
4. Operates fully offline in the restricted network

## Constraints

### Hard Constraints

**No internet at runtime**: the restricted network has no external connectivity. All models, libraries, and data must be bundled before deployment. This rules out: hosted LLM APIs, cloud vector databases, dynamic package installation, CDN-hosted JavaScript, and any tool that phones home.

**No new infrastructure**: the team already operates PostgreSQL with Flyway. Adding Elasticsearch, Redis, Neo4j, or a dedicated vector database would require procurement, security review, and operational overhead. All storage must use the existing PostgreSQL instance.

**ChatGPT via web UI only**: the prompt builder cannot call the ChatGPT API. It must produce text that a human reads and pastes into a browser. This means the prompt must be self-contained, clearly formatted, and fit within ChatGPT's context window.

**Python and Go only**: the team is comfortable with Go (it's their primary language) and Python is acceptable for tooling. Java is acceptable for the Java extractor but should not be the primary language for the pipeline itself.

### Soft Constraints

**Prefer existing tooling**: use tools the team already has (Go compiler, psql, Python 3.10+) before introducing new ones.

**Minimize operational complexity**: the system should start with a single command. No Docker required (though a Dockerfile can be provided as an option). No systemd service required for the interactive use case.

**Incremental value**: each phase of the implementation should deliver standalone value. A developer should be able to use Phase 2 (knowledge store) with direct SQL queries before Phase 3 (RAG engine) is complete.

## Knowledge Sources

### Go Services (~120 services)
- Primary source of truth for system behavior
- Each service is a Go module with its own `go.mod`
- HTTP handlers registered via gorilla/mux, chi, gin, or net/http directly
- gRPC services with protobuf definitions
- Database access via `database/sql`, `sqlx`, or a custom wrapper
- OTEL-structured logging via `slog` or a custom logger that wraps OTEL

### Java Spring Boot Services (few)
- REST endpoints via `@RestController` + `@RequestMapping`/`@GetMapping` etc.
- Service calls via `@FeignClient` or `RestTemplate`
- Database access via Spring Data JPA or `JdbcTemplate`
- SLF4J logging with Logback

### Flyway Migrations
- SQL migration files in `V{version}__{description}.sql` format
- Represent the authoritative history of the database schema
- The current schema is the result of applying all migrations in version order
- May be in a single shared directory or distributed across service repositories

### IXM Spec (XML)
- Defines the message format for the front door and back door services
- The spec is an XML document (possibly XSD) defining message types, field names, types, validation rules
- The Go front-door service translates IXM XML → internal JSON; the back door does the reverse
- Understanding the IXM spec is critical for debugging integration issues

### Confluence Documentation
- Per-service documentation pages, often with architecture diagrams and runbooks
- Space structure: likely one space per team or one per major subsystem
- Content quality varies; some pages are well-maintained, others are stale
- Exportable as XML space export or via REST API

### OTEL/Splunk Log Patterns
- Log calls are visible in the source code as `slog.Info(...)`, `logger.With(...).Info(...)` etc.
- Each log call has a message template and structured key-value fields
- Extracting these from code (rather than from Splunk) means we can build the log pattern catalog without needing Splunk access at pipeline build time
- The extracted patterns are useful for: understanding what observable events each service emits, constructing Splunk queries, debugging when a developer asks "what should I see in the logs when X happens?"

## System Components

The system has five layers:

### Layer 1: Extraction
Parsers that read each knowledge source and emit structured JSON documents. Each extractor is independent and can be run separately. Output is written to `extracted/` as a collection of JSON files.

### Layer 2: Knowledge Store
A PostgreSQL database (schema: `kep`) that holds:
- Vector embeddings of all extracted content (via pgvector), for semantic search
- Full-text search indexes (via PostgreSQL `tsvector`), for keyword search
- A knowledge graph in adjacency table form, for relational queries about service dependencies

### Layer 3: RAG Query Engine
Takes a natural language query, runs hybrid search (vector + keyword + graph), and returns a ranked context package.

### Layer 4: Prompt Builder
Takes a query + context package, selects the appropriate prompt template based on query intent, and produces a formatted, copy-pasteable prompt.

### Layer 5: Interface
A web UI (FastAPI + HTMX) and a CLI (Typer + Rich) for interactive use.

## Out of Scope

- **Automatic Splunk querying**: the pipeline extracts log patterns from code but does not connect to Splunk at runtime. A future phase could add a Splunk query builder component.
- **Automatic code modification**: this system is read-only with respect to the codebase.
- **Real-time indexing**: the knowledge store is updated by running the extraction pipeline, not by watching for file changes in real time. Nightly runs or on-demand runs are sufficient.
- **Multi-user auth**: the tool is for internal developer use. No authentication is implemented.
- **Protobuf/gRPC IDL extraction**: parsing .proto files to extract service/method definitions is a valuable future addition but is not in scope for the initial version.
