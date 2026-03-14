# Knowledge Graph

Builds and queries the service dependency graph stored in PostgreSQL adjacency tables. The graph captures:
- Which services call which other services (via HTTP or gRPC)
- Which services read from or write to which database tables
- Which services send or receive which IXM message types
- Which log events each service emits

## Why PostgreSQL, not Neo4j

At 120 services with ~500 edges, a relational adjacency list is simpler, faster, and requires no new infrastructure. Recursive CTEs handle multi-hop graph traversal in milliseconds. See [ADR-001](../../docs/adr/001-pgvector-over-dedicated-vectordb.md).

## Modules

### `builder.py` — Build graph from extracted ServiceDocs

```python
from knowledge_store.graph.builder import GraphBuilder
from knowledge_store.db_client import KepDatabaseClient

builder = GraphBuilder(db=db_client)

# Build from a list of ServiceDoc objects (from the extractor output)
builder.build_from_service_docs(service_docs)
# Populates: kep_service_nodes, kep_service_edges, kep_service_tables, kep_service_log_events
```

The builder is idempotent: re-running on the same data does not create duplicates (uses `ON CONFLICT DO UPDATE`).

### `queries.py` — Graph traversal queries

```python
from knowledge_store.graph.queries import GraphQueries

gq = GraphQueries(db=db_client)

# What services does enrollment-svc call?
neighbors = gq.get_service_neighbors("enrollment-svc", depth=1)

# Who calls enrollment-svc?
callers = gq.get_callers_of("enrollment-svc")

# Full reachable subgraph from enrollment-svc (up to 5 hops)
subgraph = gq.get_reachable_services("enrollment-svc", max_depth=5)

# What tables does enrollment-svc use?
tables = gq.get_tables_for_service("enrollment-svc")

# What services use the biometric_records table?
services = gq.get_services_by_table("biometric_records")

# Find all paths from enrollment-svc to biometric-store-svc
paths = gq.get_call_paths("enrollment-svc", "biometric-store-svc")

# What IXM message types does enrollment-svc handle?
messages = gq.get_ixm_messages_for_service("enrollment-svc")
```

## Graph Structure

```
enrollment-svc ──[HTTP]──► identity-svc ──[gRPC]──► biometric-store-svc
      │                                                        │
      └──[HTTP]──► notification-svc              ──[READ]──► biometric_records (table)
      │                                          ──[WRITE]──► biometric_records
      └──[WRITE]──► enrollment_records (table)
```

Stored as:
```
kep_service_edges:
  enrollment-svc → identity-svc     (call_type: http_client)
  enrollment-svc → notification-svc (call_type: http_client)
  identity-svc   → biometric-store-svc (call_type: grpc_client)

kep_service_tables:
  enrollment-svc   → enrollment_records (operation: write)
  biometric-store-svc → biometric_records (operation: both)
```

## Example SQL Queries

### Find all services in the call chain from front-door to a given service

```sql
WITH RECURSIVE chain AS (
    SELECT 'front-door-svc'::TEXT AS service, 0 AS depth, ARRAY['front-door-svc'] AS path
    UNION ALL
    SELECT e.to_service, c.depth + 1, c.path || e.to_service
    FROM kep.kep_service_edges e
    JOIN chain c ON c.service = e.from_service
    WHERE e.to_service != ALL(c.path)
    AND c.depth < 5
    AND c.service != 'biometric-store-svc'  -- target service
)
SELECT service, depth, path FROM chain WHERE service = 'biometric-store-svc';
```

### Find services that share a table (potential coupling)

```sql
SELECT a.service_name AS service_a, b.service_name AS service_b, a.schema_table
FROM kep.kep_service_tables a
JOIN kep.kep_service_tables b
    ON a.schema_table = b.schema_table
    AND a.service_name < b.service_name  -- avoid duplicates
ORDER BY a.schema_table, a.service_name;
```
