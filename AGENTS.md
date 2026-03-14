# Agent Guide

This document is written for AI coding agents (Claude Code and similar) that will implement the knowledge extraction and RAG system. Read this before writing any code.

---

## What You Are Building

A Python-based pipeline that:
1. Parses Go and Java microservice code, Flyway SQL migrations, XML IXM spec files, and Confluence docs
2. Stores extracted knowledge in PostgreSQL with pgvector
3. Serves RAG-enhanced prompts to a user who pastes them into ChatGPT's web UI

The system must run completely offline on a restricted network after being packaged on a machine with internet access.

## The Stack

- **Language**: Python 3.10+ for all pipeline code
- **Go helper binary**: `extractors/go-service/ast_helper/main.go` — compiled once, invoked as subprocess
- **Database**: PostgreSQL 14+ with pgvector extension, schema `kep`
- **Embedding model**: `sentence-transformers/all-MiniLM-L6-v2`, loaded from `models/` directory
- **Web framework**: FastAPI + Jinja2 + HTMX (no React, no Node.js)
- **CLI**: Typer + Rich

## Repository Layout

```
extractors/          Parser implementations — one subdirectory per source type
knowledge-store/     DB schema DDL and embedding/graph utilities
rag/                 Indexer and query engine
prompt-builder/      Prompt assembly and templates
ui/                  FastAPI server and CLI
config/              Config schema and example
scripts/             Setup and bundle scripts
docs/design/         Deep design docs — READ THESE before implementing a component
docs/adr/            Architecture Decision Records — READ THESE before changing a technology choice
models/              Downloaded embedding models (git-ignored, created by download_models.py)
extracted/           Extractor output JSON (git-ignored, created at runtime)
vendor/wheels/       Vendored Python wheels (git-ignored, created by bundle script)
```

## Before Starting Any Task

1. **Read the relevant design doc** in `docs/design/`. Each component has a design doc that explains the approach in detail. Do not implement a component that contradicts its design doc without first understanding why.
2. **Read the task definition** in `TODO.md`. Every task has acceptance criteria. Your implementation is not done until all acceptance criteria pass.
3. **Check if shared utilities exist** in `extractors/shared/`. Do not re-implement file walking, output writing, or pydantic models.
4. **Check the config schema** in `config/config_schema.py` (once it exists). All configuration values must come from the config, not be hardcoded.

## Coding Conventions

### Python Style
- Python 3.10+ only. Use `match/case`, `X | Y` union types, and `from __future__ import annotations` where needed.
- Type annotations on every function signature. No `Any` without a comment explaining why.
- Pydantic v2 models for all data structures that cross module boundaries. Use `model_config = ConfigDict(extra='forbid')`.
- Dataclasses for internal data structures that don't need validation.
- All file I/O operations must use pathlib `Path`, not string concatenation.
- Structured logging via Python's `logging` module with `python-json-logger`. Never use `print()` for operational output (use `print()` only in CLI commands for intentional user-facing output).

### Error Handling
- Extractors must not crash on a single bad file. Catch parse errors, log them at WARNING level with the file path and error message, and continue.
- Database errors are fatal — let them propagate (don't swallow them with a broad `except Exception`).
- Missing configuration values must raise a clear `ConfigurationError` with the missing key name.
- Model not found must raise `ModelNotFoundError` with instructions to run `scripts/download_models.py`.

### Imports
No relative imports across package boundaries. Use absolute imports: `from extractors.shared.models import ServiceDoc`, not `from ..shared.models import ServiceDoc`.

Use the following package structure:
```python
# extractors/go_service/extractor.py
from extractors.shared.models import ServiceDoc, HandlerInfo, LogEvent
from extractors.shared.file_walker import FileWalker
from extractors.shared.output_writer import OutputWriter
```

### Testing
- Unit tests in `tests/unit/<module_name>_test.py`
- Integration tests in `tests/integration/<component>_test.py`
- Integration tests require a PostgreSQL instance and are marked `@pytest.mark.integration`
- Use `pytest-fixtures` for shared test data; do not repeat fixture definitions across test files
- A `conftest.py` in `tests/` provides:
  - `sample_service_doc()` — a realistic `ServiceDoc` fixture
  - `sample_schema_doc()` — a realistic `SchemaDoc` fixture
  - `test_db_client()` — a `KepDatabaseClient` pointing at a test database (integration only)
  - `test_config()` — a `Config` pointing at test data directories

Run all unit tests with: `pytest tests/unit`
Run integration tests with: `pytest tests/integration -m integration`

## The Restricted Network Constraint

This is the most important constraint. Every decision must account for it.

**What this means concretely:**
- No `pip install` at runtime — all wheels must be in `vendor/wheels/`
- No model downloads at runtime — the model must be in `models/`
- No HTTP calls to external services — no HuggingFace API, no OpenAI API, no PyPI
- No CDN resources in the web UI — bundle HTMX, any fonts, any CSS frameworks
- pgvector extension must be installed on the PostgreSQL instance before deployment (include setup instructions)

**What to check before adding a dependency:**
1. Is it in `requirements.txt`? If not, add it there first.
2. Does it make any network calls at import time or initialization? If yes, ensure the calls can be disabled or are only made when explicitly configured.
3. Does it download anything at runtime (model weights, data files, etc.)? If yes, add a download step to `scripts/download_models.py` and a check at startup.

**Libraries that silently download things (watch out for these):**
- `sentence-transformers`: downloads model on first use — mitigated by loading from local path
- `spaCy`: downloads language model separately — if used, add to download script
- `nltk`: downloads corpora — if used, add to download script and bundle the corpus files
- `torch`: does NOT download at runtime if already installed

## How Extractors Work

Each extractor follows this pattern:

```python
class XxxExtractor:
    def __init__(self, config: Config, output_writer: OutputWriter):
        self.config = config
        self.writer = output_writer
        self.walker = FileWalker(
            root_dir=config.xxx.source_dir,
            include_patterns=config.xxx.include_patterns,
            exclude_patterns=config.xxx.exclude_patterns,
        )

    def extract_all(self, changed_only: bool = False) -> ExtractionStats:
        stats = ExtractionStats(total=0, processed=0, errors=0)
        hash_cache_path = Path(self.config.extracted_dir) / ".hashes" / "xxx.json"
        files = self.walker.changed_files(hash_cache_path) if changed_only else list(self.walker.walk())
        for path in files:
            stats.total += 1
            try:
                result = self.extract_one(path)
                self.writer.write_xxx(result)
                stats.processed += 1
            except Exception as e:
                logger.warning("Failed to extract %s: %s", path, e)
                stats.errors += 1
        self.walker.save_hash_cache(hash_cache_path, self.walker.load_hash_cache(hash_cache_path) | new_hashes)
        return stats
```

Do not deviate from this pattern without a good reason documented in a comment.

## The Go AST Helper

`extractors/go-service/ast_helper/main.go` is a Go binary that must be compiled before the Go extractor can run. The Python extractor invokes it as a subprocess:

```python
import subprocess, json
result = subprocess.run(
    [str(ast_helper_binary), str(go_file_path)],
    capture_output=True, text=True, timeout=10
)
if result.returncode != 0:
    raise GoParseError(f"ast_helper failed on {go_file_path}: {result.stderr}")
data = json.loads(result.stdout)
```

**Do not try to parse Go ASTs in Python.** tree-sitter is an alternative but requires a compiled parser library. The Go helper is more reliable because it uses the Go standard library's own parser.

The helper must be compiled on the build machine and included in the deployment bundle. Add compilation to `scripts/bundle_for_deployment.py`:
```bash
cd extractors/go-service/ast_helper && go build -o ../../../bin/ast_helper .
```

## pgvector Specifics

The embedding column type is `vector(384)` (384 dimensions for all-MiniLM-L6-v2). When inserting:

```python
# Convert numpy array to pgvector literal format
embedding_list = embedding.tolist()  # numpy array → Python list
# psycopg2 with pgvector registered adapter:
from pgvector.psycopg2 import register_vector
register_vector(conn)
cursor.execute(
    "INSERT INTO kep.kep_chunks (id, content, embedding) VALUES (%s, %s, %s)",
    (chunk_id, content, embedding_list)
)
```

Always use `pgvector` Python package for type adaptation. Do not format the vector as a string manually.

For the HNSW index, always build it **after** bulk insert, not before:
```sql
-- After bulk insert:
CREATE INDEX kep_chunks_embedding_hnsw_idx
ON kep.kep_chunks USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

## Database Conventions

- All tables in the `kep` schema (prefix: `kep_`)
- UUID primary keys generated by Python (`str(uuid.uuid4())`)
- All timestamps in UTC (`TIMESTAMPTZ`, stored with `datetime.now(timezone.utc)`)
- `metadata` columns are `JSONB`, not separate columns for every attribute
- No ORM (no SQLAlchemy). Write SQL directly with psycopg2.
- Parameterized queries always. Never format SQL with string interpolation.

## Chunking Token Budget

The embedding model has a max input of 512 tokens (~384 words). Chunks must not exceed 500 tokens to avoid truncation. The chunker must:
- Count tokens before yielding a chunk
- Split at natural boundaries (function end, paragraph break, heading) if over budget
- Never split mid-sentence

Token estimation: use `len(text.split()) * 1.3` if `tiktoken` is not available. If `tiktoken` is available, use `tiktoken.encoding_for_model("gpt-3.5-turbo").encode(text)`.

## Prompt Builder Conventions

Templates are in `prompt-builder/templates/*.txt`. They use Python's `string.Template` syntax (`$variable` or `${variable}`). Do not use Jinja2 for prompt templates (keep the prompt builder independent of the web framework).

The prompt format has these sections in this order:
1. SYSTEM CONTEXT (fixed header describing the biometric system)
2. RELEVANT CODE (Go/Java handler snippets)
3. DATABASE SCHEMA (table definitions)
4. IXM SPEC CONTEXT (message type definitions)
5. LOG PATTERNS (log event catalog for relevant services)
6. DOCUMENTATION (Confluence page excerpts)
7. QUESTION (the user's original question)

Each section is delimited with `===== SECTION NAME =====` for easy visual parsing when the user reads the prompt in ChatGPT.

## What Not to Do

- **Do not use SQLAlchemy or any ORM.** Direct psycopg2 only. The schema is simple enough.
- **Do not use Celery or task queues.** The pipeline runs as a script, not a service. Use `ThreadPoolExecutor` for parallelism within a single run.
- **Do not use Redis.** pgvector on existing PostgreSQL is the only storage backend.
- **Do not add a JavaScript build step.** No webpack, no Vite, no npm. HTMX and any CSS are static files bundled with the application.
- **Do not call the HuggingFace or OpenAI API at runtime.** Everything must work offline.
- **Do not use `async def` in extractors.** Extractors run as scripts and async adds complexity without benefit. The RAG query engine may use async for the FastAPI integration, but the core query logic should be synchronous.
- **Do not hardcode service names, table names, or IXM message types.** These come from the extracted data and the config.
- **Do not add dependencies without updating `requirements.txt` and checking that they can be bundled.**

## How to Test Without the Real Codebase

The `tests/fixtures/` directory (to be created) contains synthetic test data:
- `tests/fixtures/sample_go_service/`: a minimal Go service with one HTTP handler, one DB query, one log call
- `tests/fixtures/sample_java_service/`: a minimal Java Spring Boot service with one endpoint
- `tests/fixtures/sample_migrations/`: 3 Flyway migration files creating 2 tables
- `tests/fixtures/sample_confluence/`: 2 Confluence page JSON files
- `tests/fixtures/sample_ixm_spec/`: a small IXM XML spec with 2 message types
- `tests/fixtures/extracted/`: pre-generated extraction output from the above (for testing the indexer without running extractors)

All unit tests use only the data in `tests/fixtures/`. Do not hardcode paths to the actual biometric system codebase in tests.

## Deployment Checklist

Before handing off a bundle for deployment to the restricted network, verify:

1. `vendor/wheels/` directory contains all required wheels: `ls vendor/wheels/ | wc -l` should be non-trivial
2. `models/all-MiniLM-L6-v2/` directory exists and contains `config.json`, `tokenizer.json`, `pytorch_model.bin` (or `model.safetensors`)
3. `bin/ast_helper` binary exists (compiled Go binary for the target OS/arch)
4. `scripts/setup_pgvector.sql` can be run on the target PostgreSQL instance
5. `config/config.yaml` has been updated with correct paths for the restricted network
6. Running `pip install --no-index --find-links=vendor/wheels -r requirements.txt` on the target machine succeeds
7. Running `python -m rag.indexer --verify-only` succeeds (checks DB connection, model load, config validity)

## Asking for Clarification

If you are unsure about any of the following, stop and ask rather than guessing:
- The internal service naming convention (how are services named in the Go module paths?)
- The IXM spec format (XSD? custom XML? what are the root element names?)
- The Confluence export format (XML space export? REST API? what labels are used?)
- The Flyway migration file location (is it one directory or per-service?)
- The PostgreSQL connection details for the restricted network
- Whether the team can install pgvector on their PostgreSQL instance (requires superuser)

These details are unknowable from the codebase alone and will significantly affect extractor implementation.
