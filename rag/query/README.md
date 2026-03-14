# Query Engine

Handles the query-time retrieval and context assembly for the RAG pipeline.

## Module Structure

```
rag/query/
├── engine.py       # Unified QueryEngine: orchestrates all search strategies
├── analyzer.py     # Query analysis: intent detection, entity extraction
├── vector_search.py  # Semantic search via pgvector
├── keyword_search.py # Full-text search via PostgreSQL tsvector
├── graph_search.py   # Graph-based context expansion
└── assembler.py    # Merge, deduplicate, rank, truncate to budget
```

## Usage

```python
from rag.query.engine import QueryEngine

engine = QueryEngine(db=db, embedder=embedder, config=config)

# Simple query
context = engine.query("how does enrollment work")
# Returns a ContextPackage with .code_chunks, .schema_chunks, .spec_chunks, .doc_chunks, .log_chunks

# With options
context = engine.query(
    "trace the enrollment request from front door to database",
    token_budget=6000,          # max tokens in context package
    intent_override="trace_request",  # skip intent detection
    source_type_filter="go_service",  # search only this source type
)

# With timing info
context = engine.query("enrollment handler", include_stats=True)
print(f"Vector search: {context.search_stats.vector_time_ms}ms")
print(f"Total: {context.search_stats.total_time_ms}ms")
```

## Search Strategies

### Vector Search (`vector_search.py`)

Embeds the query and finds the nearest chunks by cosine similarity:

```sql
SELECT id, content, source_type, service_name, metadata,
       1 - (embedding <=> $1::vector) AS score
FROM kep.kep_chunks
ORDER BY embedding <=> $1::vector
LIMIT 20;
```

Top-20 results returned. Scores above 0.3 are considered relevant.

### Keyword Search (`keyword_search.py`)

Full-text search using PostgreSQL's tsvector:

```sql
SELECT id, content, source_type, service_name, metadata,
       ts_rank_cd(content_tsv, query) AS score
FROM kep.kep_chunks, plainto_tsquery('english', $1) AS query
WHERE content_tsv @@ query
ORDER BY score DESC
LIMIT 20;
```

### Graph Expansion (`graph_search.py`)

When the query mentions known service/table/message type names, fetches all chunks associated with those entities via the knowledge graph:

```python
# If query mentions "enrollment-svc"
graph_search.expand_for_services(["enrollment-svc"])
# Returns chunks for enrollment-svc AND all services it calls (depth=1)
```

### Score Fusion (`assembler.py`)

Reciprocal Rank Fusion (RRF) with k=60:

```
RRF_score(chunk) = Σ_lists (1 / (60 + rank_in_list))
```

A chunk appearing at rank 3 in vector and rank 5 in keyword scores higher than a chunk appearing at rank 1 in only one list.

Full design: [`docs/design/rag-engine.md`](../../docs/design/rag-engine.md)
