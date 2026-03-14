# Embeddings

Handles loading the local embedding model and generating vector embeddings for knowledge store chunks.

## Modules

### `model_loader.py` — Load and use the embedding model

```python
from knowledge_store.embeddings.model_loader import EmbeddingModelLoader

# Load from local path (no internet required)
loader = EmbeddingModelLoader(model_path="models/all-MiniLM-L6-v2")
model = loader.load()

# Embed a batch of texts
texts = ["enrollment handler", "biometric records table", "IXM EnrollRequest message"]
embeddings = loader.embed(texts)  # shape: (3, 384), dtype: float32

# Embed a single query (used at query time)
query_vec = loader.embed_query("how does enrollment work")  # shape: (384,)
```

If the model path does not exist, raises `ModelNotFoundError` with a message explaining how to run `scripts/download_models.py`.

### `chunker.py` — Split extracted documents into chunks

```python
from knowledge_store.embeddings.chunker import Chunker
from extractors.shared.models import ServiceDoc, SchemaDoc

chunker = Chunker(max_tokens=500)

# Chunk a service document (one chunk per handler)
chunks = chunker.chunk_service_doc(service_doc)

# Chunk the schema (one chunk per table)
chunks = chunker.chunk_schema_doc(schema_doc)

# Chunk a Confluence page (split at headings, max 500 tokens per chunk)
chunks = chunker.chunk_doc_page(doc_page)

# Chunk an IXM spec document (one chunk per message type)
chunks = chunker.chunk_spec_doc(spec_doc)

# Chunk log patterns (one chunk per service)
chunks = chunker.chunk_log_patterns(log_patterns_for_one_service)
```

Each `Chunk` contains: `id` (UUID), `source_type`, `service_name`, `content` (the text to embed), `token_count`, `metadata` (dict with type-specific attributes).

## Embedding Model Details

**Model**: `sentence-transformers/all-MiniLM-L6-v2`
**Output dimensions**: 384
**Max input tokens**: 512 (chunker keeps chunks under 500 tokens)
**Model size**: ~80MB
**CPU inference**: ~5ms per text, ~50ms per batch of 32

The model must be downloaded before deployment:
```bash
python ../../scripts/download_models.py
```

This places the model files in `../../models/all-MiniLM-L6-v2/`. See [ADR-002](../../docs/adr/002-local-embeddings-model.md) for the rationale.

## Token Counting

Token count is estimated with `len(text.split()) * 1.3` unless `tiktoken` is available. With `tiktoken`:
```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
count = len(enc.encode(text))
```

The 1.3 multiplier compensates for subword tokenization (common English words split into 1 token, technical terms may split into 2–3). This estimate is within ±15% of the true token count for technical text.
