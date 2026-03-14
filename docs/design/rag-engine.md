# RAG Query Engine Design

The RAG (Retrieval-Augmented Generation) query engine takes a natural language question and returns a ranked, deduplicated context package containing the most relevant knowledge from the knowledge store. This context package is then passed to the prompt builder.

---

## Overview

The query engine runs three searches in parallel and merges the results:

1. **Semantic (vector) search**: embeds the query with all-MiniLM-L6-v2 and retrieves the most similar chunks by cosine distance
2. **Keyword (full-text) search**: runs a PostgreSQL `tsvector`/`tsquery` search for exact term matches
3. **Graph expansion**: if the query mentions known service names, table names, or IXM message types, fetches all chunks associated with those entities and their neighbors in the knowledge graph

The three result sets are merged using Reciprocal Rank Fusion (RRF), deduplicated by chunk ID, grouped by content type, and truncated to fit the prompt's token budget.

---

## Query Analysis

### Intent Classification

Before searching, the query is classified into one of six intents. Intent drives template selection in the prompt builder and can influence search weights.

```python
class QueryIntent(str, Enum):
    EXPLAIN_FLOW   = "explain_flow"     # "how does X work", "explain the enrollment process"
    TRACE_REQUEST  = "trace_request"    # "trace a request through", "follow this request", "request lifecycle"
    DEBUG_ERROR    = "debug_error"      # "why is X failing", "500 error", "exception", "timeout"
    DOCUMENT_ENDPOINT = "document_endpoint"  # "write documentation for", "API spec for", "OpenAPI"
    FIND_RELATED   = "find_related"     # "what services call X", "related to", "depends on", "who uses"
    GENERAL_QUESTION = "general_question"  # everything else
```

**Classification rules** (checked in order, first match wins):
1. If query contains "trace", "follow the request", "request path", "call chain" → TRACE_REQUEST
2. If query contains "error", "fail", "exception", "broken", "500", "not working", "debug", "why is" → DEBUG_ERROR
3. If query contains "document", "write docs", "openapi", "swagger", "api spec", "readme" → DOCUMENT_ENDPOINT
4. If query contains "what calls", "who uses", "depends on", "related to", "what services" → FIND_RELATED
5. If query contains "how does", "explain", "describe", "what does", "walk me through", "overview" → EXPLAIN_FLOW
6. Default → GENERAL_QUESTION

### Entity Extraction

The analyzer identifies mentions of known entities in the query using fuzzy string matching:

**Service names**: all service names from `kep.kep_service_nodes`. Fuzzy match with ratio > 75 using `fuzzywuzzy.fuzz.partial_ratio`. This handles cases where the user types "enrollment service" and the actual name is "enrollment-svc".

**Table names**: all table names from the schema extraction. Same fuzzy matching.

**IXM message types**: all message type names from `kep.kep_ixm_message_refs`. Fuzzy match. Also match common abbreviations (e.g., "enroll request" → "EnrollRequest").

**Keywords**: all remaining tokens after stop-word removal. Used for keyword search. Stop words: standard English stop words plus domain-specific noise words (remove: "service", "what", "how", "the", "a", "an", "is", "are", "does").

---

## Search Strategies

### Semantic Search (Vector Similarity)

```sql
SELECT
    id,
    content,
    source_type,
    service_name,
    metadata,
    1 - (embedding <=> $1::vector) AS score
FROM kep.kep_chunks
WHERE ($2::text IS NULL OR source_type = $2)
ORDER BY embedding <=> $1::vector
LIMIT $3;
```

- `$1`: query embedding as a pgvector literal (384 floats)
- `$2`: optional source_type filter (NULL = search all types)
- `$3`: k, default 20

The `<=>` operator is cosine distance. `1 - distance = cosine similarity`. Results are ordered by ascending distance (most similar first).

**Score interpretation**: cosine similarity of 1.0 means identical vectors, 0.0 means orthogonal (unrelated). In practice, strong matches score 0.75–0.95; weak matches score 0.4–0.6. Results below 0.3 are noise and are dropped after merging.

**Source type filter**: when the query clearly targets a specific content type (e.g., a DEBUG_ERROR query should weight code chunks higher), the search can be restricted. In practice, we run the unrestricted search and let RRF handle weighting.

### Keyword Search (Full-Text)

```sql
SELECT
    id,
    content,
    source_type,
    service_name,
    metadata,
    ts_rank_cd(content_tsv, to_tsquery('english', $1)) AS score
FROM kep.kep_chunks
WHERE content_tsv @@ to_tsquery('english', $1)
ORDER BY score DESC
LIMIT $2;
```

- `$1`: tsquery expression. Built from extracted keywords joined with `&` for AND or `|` for OR.
  - For 3+ keywords: use `keyword1 | keyword2 | keyword3` (OR), then re-rank by how many keywords match
  - For 1-2 keywords: use `keyword1 & keyword2` (AND) to reduce noise
- `$2`: k, default 20

**Query construction**: the query analyzer removes stop words and stems terms using `pg_catalog.english` dictionary. For the query "enrollment service HTTP endpoints", the tsquery becomes `enroll | endpoint | http` (after stemming "enrollment" → "enroll", dropping "service" as a stop word, keeping "HTTP" uppercase).

**ts_rank_cd**: the "cover density" ranking function, which weights matches more highly when matching terms appear close together in the document. This rewards chunks where "enroll" and "endpoint" appear in the same sentence over chunks where they appear paragraphs apart.

### Graph Expansion

When entities are detected in the query, the graph search fetches all chunks for the mentioned entities and their direct neighbors:

```python
def expand_for_services(service_names: list[str]) -> list[SearchResult]:
    # Get all neighbors (depth 1)
    neighbors = db.execute("""
        SELECT DISTINCT
            CASE WHEN from_service = ANY($1) THEN to_service ELSE from_service END AS neighbor
        FROM kep.kep_service_edges
        WHERE from_service = ANY($1) OR to_service = ANY($1)
    """, [service_names])

    all_services = set(service_names) | {r.neighbor for r in neighbors}

    # Fetch all chunks for these services
    chunks = db.execute("""
        SELECT id, content, source_type, service_name, metadata
        FROM kep.kep_chunks
        WHERE service_name = ANY($1)
    """, [list(all_services)])

    return [SearchResult(chunk_id=c.id, content=c.content, ..., score=0.7) for c in chunks]
```

**Fixed score of 0.7**: graph-expanded results get a fixed score of 0.7. This places them below strong semantic matches (0.75–0.95) but above weak ones (0.3–0.6). The rationale: graph expansion retrieves results we know are relevant (they're about the same service the user asked about) but we don't know which specific chunks within that service are most relevant to the question.

---

## Score Fusion: Reciprocal Rank Fusion

RRF combines results from multiple ranked lists into a single ranking without requiring score normalization.

For each chunk that appears in any result list:
```
RRF_score = Σ_lists (1 / (k + rank_in_list))
```
where `k = 60` is a smoothing constant that reduces the influence of very high ranks.

**Example**: a chunk that appears at rank 3 in the vector search and rank 7 in the keyword search:
```
RRF_score = 1/(60+3) + 1/(60+7) = 0.01587 + 0.01493 = 0.0308
```

A chunk that appears at rank 1 in only the keyword search:
```
RRF_score = 1/(60+1) = 0.01639
```

The chunk appearing in both lists scores higher (0.0308 vs 0.0164) even though it ranked lower in each individual list. This naturally promotes chunks that multiple search strategies agree on.

**Graph expansion ranking**: graph-expanded results that are NOT in the vector or keyword results are added at the bottom of their respective list with a rank equal to k+20 (a penalty rank). This means graph expansion adds breadth without displacing strongly matched chunks.

---

## Context Assembly

After RRF scoring, the assembler:

1. **Deduplicates**: if the same chunk ID appears in multiple result sets, keep one entry with the highest RRF score.

2. **Drops weak results**: remove chunks with RRF score below 0.008 (corresponds roughly to rank 125 in a single list). These are unlikely to be relevant.

3. **Groups by source type**: separate the ranked list into:
   - `code_chunks` (source_type in ['go_service', 'java_service'])
   - `schema_chunks` (source_type = 'schema')
   - `spec_chunks` (source_type = 'ixm_spec')
   - `doc_chunks` (source_type = 'confluence')
   - `log_chunks` (source_type = 'log_patterns')

4. **Truncates to token budget**: add chunks greedily from the merged ranked list until the token budget is reached. The default budget is 6000 tokens, leaving 2000 tokens for ChatGPT's response in an 8K context window.

5. **Minimum representation**: ensure at least 1 chunk from code_chunks if any code chunks exist in the results. Do not let the budget run out entirely on one type (e.g., 20 schema chunks) while leaving no room for code.

**Token counting**: each chunk has a `token_count` field. The assembler sums these plus estimated overhead for section headers and separators (~200 tokens total).

---

## Query Result Structure

```python
@dataclass
class ContextPackage:
    code_chunks: list[SearchResult]
    schema_chunks: list[SearchResult]
    spec_chunks: list[SearchResult]
    doc_chunks: list[SearchResult]
    log_chunks: list[SearchResult]
    total_tokens: int
    query_analysis: QueryAnalysis
    search_stats: SearchStats  # timing and result counts per strategy

@dataclass
class SearchStats:
    vector_results: int
    keyword_results: int
    graph_results: int
    merged_results: int
    final_results: int
    vector_time_ms: float
    keyword_time_ms: float
    graph_time_ms: float
    total_time_ms: float
```

---

## Performance

**Target latency**: under 3 seconds for an interactive query on a warm database.

**Bottleneck analysis**:
- Vector embedding of query: ~5ms (model already loaded in memory)
- pgvector HNSW search (top-20): ~10ms (50K vectors, m=16)
- Full-text search: ~15ms (GIN index)
- Graph expansion: ~20ms (indexed lookups + chunk fetch)
- RRF + assembly: ~5ms (in-memory Python)
- Total: ~55ms

The 3-second budget is very comfortable. The main latency risk is the first query after startup, when the embedding model is being loaded from disk (~500ms) and the database connection pool is being established (~100ms). Subsequent queries use the warm model and pool.

**Parallel execution**: the three search strategies are run concurrently using `ThreadPoolExecutor(max_workers=3)`. This halves effective latency compared to sequential execution.

---

## Query Caching

For the interactive use case (web UI), the engine caches recent query results in memory:
- Cache key: SHA-256 of the normalized query string
- Cache size: 50 entries (LRU eviction)
- TTL: 10 minutes

This is particularly useful when a user refines a query slightly ("how does enrollment work" → "how does biometric enrollment work") — the first query fills the cache; the second query may be a cache hit or a near-miss.

Caching is in-memory only (not in PostgreSQL). Cache is invalidated on re-index.

---

## Failure Modes and Recovery

**Embedding model not loaded**: if the model is not found at startup, the engine falls back to keyword-only search. Results will be less semantically rich but still useful. A warning is logged.

**pgvector extension not available**: if the embedding column doesn't exist or pgvector is not installed, the engine falls back to keyword-only search. The startup check (`python -m rag.indexer --verify-only`) reports this.

**Empty results**: if all three search strategies return zero results, the engine returns an empty `ContextPackage`. The prompt builder handles this case gracefully with a message: "No relevant context was found in the knowledge store. The following answer is based on ChatGPT's general knowledge only."

**Database connection failure**: logged at ERROR level; the query raises `DatabaseError` which the web UI catches and displays as "Knowledge store unavailable. Please check database connection."
