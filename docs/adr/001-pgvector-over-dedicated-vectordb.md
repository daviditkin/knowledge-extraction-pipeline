# ADR-001: Use pgvector on existing PostgreSQL instead of a dedicated vector database

**Status**: Accepted
**Date**: 2024-03
**Deciders**: Pipeline architecture team

---

## Context

The RAG pipeline requires a vector store to hold semantic embeddings (~50,000 384-dimensional vectors) and support approximate nearest-neighbor search. Several options were evaluated:

- **pgvector** (PostgreSQL extension): adds vector column types and ANN indexes to PostgreSQL
- **Qdrant**: dedicated open-source vector database, written in Rust
- **Weaviate**: dedicated vector database with built-in embedding support
- **Chroma**: lightweight embedded vector database, Python-native
- **FAISS** (Meta): in-memory vector search library, no persistence layer
- **Milvus**: distributed vector database

## Decision

Use **pgvector** on the team's existing PostgreSQL instance.

## Rationale

### The team already operates PostgreSQL

The application's database is PostgreSQL managed with Flyway migrations. The operations team already knows how to run it, back it up, monitor it, and connect to it. Adding the knowledge pipeline to the same instance (different schema: `kep`) means:
- No new service to manage
- No new backup procedure
- No new network policy (the pipeline connects to the same DB)
- No new credentials to manage

### Restricted network constraint

Every additional service on the restricted network requires:
1. Installation and configuration
2. Security review and network policy approval
3. Operational documentation
4. Monitoring integration

A dedicated vector database adds all of this overhead. pgvector adds only: `CREATE EXTENSION vector` (one SQL command, requires PostgreSQL superuser).

### Scale adequacy

The knowledge store holds approximately 50,000 chunks (120 services × ~300 chunks each, plus schema, spec, and docs). pgvector with HNSW indexes provides:
- Sub-100ms approximate nearest-neighbor search at this scale
- Tested and documented at 1M+ vectors — our 50K is well within the comfortable range

Dedicated vector databases are designed for 100M+ vectors with distributed architectures. That's 2,000× more vectors than we have. The overhead is not justified.

### Hybrid search is simpler

The RAG engine uses hybrid search: vector similarity (semantic) + full-text search (keyword). PostgreSQL natively provides full-text search via `tsvector`/`tsquery`. With pgvector, both live in the same database, enabling a single query or simple parallel queries. With a dedicated vector database, the full-text search would require either a second service (Elasticsearch) or duplicating the chunks in PostgreSQL anyway.

### ACID semantics and SQL

The knowledge graph (service dependencies, table relationships, log event catalog) is relational data that benefits from SQL: JOINs, recursive CTEs, aggregations. Storing the graph in PostgreSQL alongside the vectors enables queries like "find all chunks for services that write to table X" in a single SQL statement. This would require application-level joining if the vectors were in a separate store.

## Consequences

### Positive

- Zero new infrastructure to manage
- pgvector HNSW indexes provide adequate performance (sub-100ms at 50K vectors)
- Full-text search and vector search in the same transaction/query
- Existing backup and monitoring covers the knowledge store
- SQL-based knowledge graph enables expressive traversal queries

### Negative

- pgvector requires PostgreSQL 14+ (check target version)
- HNSW index requires pgvector 0.5.0+ (check installed version; older servers may have 0.4.x)
- Vector search quality is slightly below purpose-built vector databases at 1M+ scale (not relevant at our scale)
- Cannot use pgvector's HNSW index for exact search (but approximate is acceptable for RAG)

### Risks and Mitigations

**Risk**: pgvector extension is not approved for installation by the database administrators.
**Mitigation**: pgvector can be used as a separate PostgreSQL instance (e.g., a single Docker container or a second PostgreSQL installation) if the production DB cannot be modified. The pipeline code does not change.

**Risk**: Storing knowledge store data in the application's production DB creates data retention or privacy concerns.
**Mitigation**: use a separate PostgreSQL database (on the same server) or a separate PostgreSQL instance. The schema is entirely in the `kep` namespace. No application tables are touched.

## Alternatives Rejected

**Chroma (embedded)**: Chroma stores vectors in a SQLite file and is Python-native. Appealing for simplicity, but: no SQL full-text search, no knowledge graph, no persistence guarantees, and the persistence layer has had stability issues in early versions. Also adds a new storage technology to the stack.

**FAISS**: in-memory only (no persistence without a custom serialization layer). Requires loading all 50K vectors into RAM on startup (~75MB at float32, manageable, but adds startup latency). No full-text search. Would require building a persistence wrapper on top.

**Qdrant**: strong technical choice, but requires a new service on the restricted network. Its main advantage (filtering with vector search in one pass) is not critical for our scale.
