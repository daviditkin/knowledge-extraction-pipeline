# RAG Engine

The retrieval-augmented generation engine. Takes a natural language question and returns a ranked `ContextPackage` containing the most relevant knowledge from the knowledge store.

## Components

```
rag/
├── indexer/    # Build the knowledge store from extracted JSON files
└── query/      # Query the knowledge store, assemble context
```

## Quick Reference

### Running the indexer

```bash
# Full index (after running extractors)
python -m rag.indexer --config config/config.yaml

# Incremental (only changed files)
python -m rag.indexer --config config/config.yaml --changed-only

# Verify without changes
python -m rag.indexer --config config/config.yaml --verify-only
```

### Querying programmatically

```python
from rag.query.engine import QueryEngine
from knowledge_store.db_client import KepDatabaseClient
from knowledge_store.embeddings.model_loader import EmbeddingModelLoader
from extractors.shared.config import Config

config = Config.from_yaml("config/config.yaml")
db = KepDatabaseClient(dsn=config.database.dsn)
embedder = EmbeddingModelLoader(model_path=config.embedding.model_path)
embedder.load()

engine = QueryEngine(db=db, embedder=embedder, config=config)

# Run a query
context = engine.query("how does biometric enrollment work")

# The context package contains ranked chunks grouped by type
print(f"Found {len(context.code_chunks)} code chunks")
print(f"Found {len(context.schema_chunks)} schema chunks")
print(f"Total tokens: {context.total_tokens}")
```

## Search Strategy

Three searches run in parallel:

1. **Semantic (vector)**: embeds the query with all-MiniLM-L6-v2, searches pgvector HNSW index for top-20 similar chunks
2. **Keyword (full-text)**: PostgreSQL tsvector/tsquery search for top-20 keyword matches
3. **Graph expansion**: if service/table/message type names are detected in the query, fetches all chunks for those entities and their neighbors

Results are merged with Reciprocal Rank Fusion (RRF) and truncated to the configured token budget (default: 6000 tokens).

Full design: [`docs/design/rag-engine.md`](../docs/design/rag-engine.md)
