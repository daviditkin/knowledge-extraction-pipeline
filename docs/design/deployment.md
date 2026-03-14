# Deployment Guide: Restricted Network

This guide explains how to package the knowledge extraction pipeline on a machine with internet access and deploy it to a restricted private network with no internet connectivity.

---

## Two-Phase Workflow

### Phase 1: Build (on a machine with internet access)

This is where you run Claude Code and have access to PyPI, HuggingFace, and GitHub.

1. Set up the project and install dependencies
2. Download the embedding model
3. Vendor all Python wheels
4. Compile the Go AST helper binary for the target OS/architecture
5. Bundle everything into a tarball

### Phase 2: Deploy (on the restricted network)

1. Transfer the tarball to the restricted network
2. Extract and install from vendored wheels (no internet required)
3. Set up pgvector on the existing PostgreSQL instance
4. Configure the pipeline to point at your codebase directories
5. Run the extraction and indexing pipeline
6. Start the web UI or use the CLI

---

## Step-by-Step: Build Phase

### Prerequisites on the build machine

- Python 3.10 or 3.11 (match the version available on the restricted network)
- Go 1.21+ (for compiling the AST helper)
- `tar` and `pip`
- Approximately 3GB of free disk space

### 1. Install dependencies

```bash
cd knowledge-extraction-pipeline

# Create a virtual environment
python3.10 -m venv .venv
source .venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
```

### 2. Download the embedding model

```bash
python scripts/download_models.py

# This downloads ~80MB to models/all-MiniLM-L6-v2/
# Verifies the download with a test embedding
```

**What gets downloaded**:
```
models/all-MiniLM-L6-v2/
├── config.json
├── tokenizer.json
├── tokenizer_config.json
├── vocab.txt
├── special_tokens_map.json
├── sentence_bert_config.json
└── model.safetensors   (or pytorch_model.bin)
```

### 3. Vendor Python wheels

```bash
mkdir -p vendor/wheels

# Download all required wheels for the target platform
# Specify the platform if cross-compiling (e.g., deploying to Linux from macOS)
pip download \
  --platform linux_x86_64 \
  --python-version 3.10 \
  --only-binary=:all: \
  -r requirements.txt \
  --dest vendor/wheels/

# Verify offline install works
pip install --no-index --find-links=vendor/wheels -r requirements.txt --dry-run
```

**Important**: PyTorch (CPU-only) is the largest wheel (~700MB). Specify the correct platform and Python version to download the right binaries.

For PyTorch CPU specifically, you may need to add the PyTorch index:
```bash
pip download \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  torch==2.1.0+cpu \
  --dest vendor/wheels/ \
  --platform linux_x86_64 --python-version 3.10 --only-binary=:all:
```

### 4. Compile the Go AST helper

```bash
mkdir -p bin

# Compile for the target OS and architecture
GOOS=linux GOARCH=amd64 go build \
  -o bin/ast_helper \
  ./extractors/go-service/ast_helper/

# Verify the binary
file bin/ast_helper
# Expected: ELF 64-bit LSB executable, x86-64
```

If the restricted network runs a different architecture (e.g., ARM64 for newer servers):
```bash
GOOS=linux GOARCH=arm64 go build -o bin/ast_helper_arm64 ./extractors/go-service/ast_helper/
```

### 5. Bundle for deployment

```bash
python scripts/bundle_for_deployment.py --output dist/pipeline-bundle.tar.gz

# This creates a tarball containing:
# - All Python source files
# - vendor/wheels/  (Python wheels, no-index install)
# - models/all-MiniLM-L6-v2/  (embedding model)
# - bin/ast_helper  (compiled Go binary)
# - config/example.yaml
# - scripts/setup_pgvector.sql
# - requirements.txt
```

**Expected bundle size**: 1.5–2.0GB (dominated by PyTorch: ~700MB and model: ~80MB)

**Verify the bundle**:
```bash
tar -tzf dist/pipeline-bundle.tar.gz | grep -E "(vendor/wheels|models/|bin/)" | head -20
```

---

## Step-by-Step: Deploy Phase

### Prerequisites on the restricted network server

- Python 3.10 (matching the build machine version)
- PostgreSQL 14+ running and accessible
- Sufficient disk space (~2GB for the bundle + ~500MB for the knowledge store)
- The server must have network access to the PostgreSQL instance

### 1. Transfer and extract the bundle

```bash
# Transfer via whatever mechanism is available (USB drive, internal file share, scp within the network)
# Then on the target server:
tar -xzf pipeline-bundle.tar.gz
cd pipeline-bundle
```

### 2. Create a virtual environment and install from vendored wheels

```bash
python3.10 -m venv .venv
source .venv/bin/activate

pip install \
  --no-index \
  --find-links=vendor/wheels \
  -r requirements.txt

# Verify installation
python -c "import sentence_transformers; import pgvector; import fastapi; print('OK')"
```

### 3. Set up pgvector on PostgreSQL

Connect to the PostgreSQL instance and run the setup script:

```bash
psql -h <host> -U <superuser> -d <database> -f scripts/setup_pgvector.sql
```

Contents of `scripts/setup_pgvector.sql`:
```sql
-- Requires PostgreSQL superuser or pg_extension_owner membership
CREATE EXTENSION IF NOT EXISTS vector;

-- Create the knowledge extraction pipeline schema
CREATE SCHEMA IF NOT EXISTS kep;

-- Grant access to the pipeline user
GRANT USAGE ON SCHEMA kep TO <pipeline_user>;
GRANT CREATE ON SCHEMA kep TO <pipeline_user>;
```

**pgvector version requirement**: 0.5.0 or later for HNSW index support. Check the installed version:
```sql
SELECT installed_version FROM pg_available_extensions WHERE name = 'vector';
```

If pgvector is not installed, a database administrator must install it from the package:
```bash
# On Debian/Ubuntu:
apt-get install postgresql-14-pgvector

# On RHEL/CentOS:
yum install pgvector_14

# Or from source (requires a C compiler):
git clone --branch v0.5.1 https://github.com/pgvector/pgvector.git
cd pgvector
make
make install  # as postgres user or with PG_CONFIG path set
```

The pgvector source can be bundled with the deployment tarball for air-gapped environments:
```bash
# On build machine:
git clone --branch v0.5.1 https://github.com/pgvector/pgvector.git vendor/pgvector
# Include vendor/pgvector/ in the tarball
```

### 4. Configure the pipeline

```bash
cp config/example.yaml config/config.yaml
```

Edit `config/config.yaml` to set:

```yaml
# Path to the Go microservices source code
go_services:
  source_dir: /path/to/your/services

# Path to the Java services
java_services:
  source_dir: /path/to/your/java-services

# Path to Flyway migrations
flyway:
  migrations_dir: /path/to/flyway/migrations

# Path to Confluence export (XML or JSON directory)
confluence:
  export_dir: /path/to/confluence-export

# Path to IXM spec files
ixm_spec:
  spec_dir: /path/to/ixm-spec

# PostgreSQL connection
database:
  host: localhost
  port: 5432
  database: your_database
  user: pipeline_user
  password: pipeline_password
  schema: kep

# Path to the downloaded embedding model
embedding:
  model_path: ./models/all-MiniLM-L6-v2

# Path to the compiled ast_helper binary
go_extractor:
  ast_helper_binary: ./bin/ast_helper

# Output directory for extracted JSON
extracted_dir: ./extracted

# Web UI
ui:
  host: 127.0.0.1
  port: 8080
```

### 5. Run the knowledge store schema migration

```bash
source .venv/bin/activate

# Create the knowledge store tables
psql -h <host> -U <pipeline_user> -d <database> \
  -f knowledge-store/schema/V001__kep_base_schema.sql \
  -f knowledge-store/schema/V002__kep_indexes.sql
```

### 6. Run the extraction pipeline

```bash
# Run all extractors
python scripts/run_extractors.py --config config/config.yaml

# Monitor progress — this can take 30–60 minutes for 120 services
# Output will be in extracted/
ls extracted/services/ | wc -l   # should equal number of Go services
ls extracted/confluence/          # one file per Confluence page
```

### 7. Run the indexing pipeline

```bash
# Index all extracted data into PostgreSQL
python -m rag.indexer --config config/config.yaml

# Monitor progress — this can take 30–60 minutes for full indexing
# The HNSW index is built at the end of this step
```

### 8. Verify the installation

```bash
# Run the verification check
python -m rag.indexer --verify-only --config config/config.yaml

# Expected output:
# [OK] Database connection: kep schema found
# [OK] pgvector extension: v0.5.1 installed
# [OK] Embedding model: models/all-MiniLM-L6-v2 loaded (384-dim output verified)
# [OK] Knowledge store: 47823 chunks indexed
# [OK] Knowledge graph: 124 services, 512 edges
# [OK] Test query: "enrollment handler" returned 5 results in 43ms
```

### 9. Start the web UI

```bash
python -m ui.server --config config/config.yaml

# Access at http://localhost:8080 (or configured host/port)
```

Or use the CLI:
```bash
python -m ui.cli query "how does biometric enrollment work"
```

---

## Maintenance and Updates

### Updating the knowledge store after code changes

When the codebase changes (new services, changed handlers, schema migrations):

```bash
# Re-run extraction for changed files only
python scripts/run_extractors.py --config config/config.yaml --changed-only

# Re-index changed documents
python -m rag.indexer --config config/config.yaml --changed-only
```

This is fast: only modified files are re-processed. New files are processed fully. Deleted files are removed from the index.

For a nightly cron job:
```bash
0 2 * * * /path/to/pipeline/.venv/bin/python \
  /path/to/pipeline/scripts/run_pipeline.py \
  --config /path/to/pipeline/config/config.yaml \
  --changed-only \
  >> /var/log/knowledge-pipeline.log 2>&1
```

### Updating after a new deployment bundle

When new features are developed outside the network and a new bundle is produced:

```bash
# Stop the web UI if running
kill $(cat pipeline.pid)

# Extract new bundle (the config.yaml and extracted/ and models/ should be preserved)
tar -xzf new-pipeline-bundle.tar.gz --exclude=config/config.yaml --exclude=extracted/ --exclude=models/

# Reinstall dependencies (new wheels may have been added)
.venv/bin/pip install --no-index --find-links=vendor/wheels -r requirements.txt

# Restart
python -m ui.server --config config/config.yaml &
```

---

## Configuration Reference

See `config/example.yaml` for the full configuration with all options and comments. Key sections:

| Section | Description |
|---|---|
| `database` | PostgreSQL connection parameters |
| `embedding.model_path` | Path to the local model directory |
| `go_services.source_dir` | Root directory of Go microservices |
| `java_services.source_dir` | Root directory of Java services |
| `flyway.migrations_dir` | Path to Flyway SQL migration files |
| `confluence.export_dir` | Path to Confluence export directory |
| `ixm_spec.spec_dir` | Path to IXM XML spec files |
| `go_extractor.ast_helper_binary` | Path to compiled ast_helper binary |
| `extracted_dir` | Output directory for extraction results |
| `ui.host` | Web UI bind address (default: 127.0.0.1) |
| `ui.port` | Web UI port (default: 8080) |
| `rag.token_budget` | Maximum tokens in assembled prompt (default: 8000) |
| `rag.vector_k` | Top-K results from vector search (default: 20) |
| `rag.keyword_k` | Top-K results from keyword search (default: 20) |

---

## Troubleshooting

### "pgvector extension not available"

The pgvector extension is not installed on the PostgreSQL instance. A database administrator must install it (requires access to the PostgreSQL server host to install the extension). See step 3 above.

### "ModelNotFoundError: model not found at models/all-MiniLM-L6-v2"

The embedding model was not included in the bundle or was extracted to the wrong path. Check that `models/all-MiniLM-L6-v2/config.json` exists. If missing, the model must be re-downloaded on the build machine and a new bundle created.

### "ast_helper: cannot execute binary file"

The Go AST helper binary was compiled for a different architecture. Recompile with the correct `GOARCH` on the build machine and create a new bundle.

### "pip install: no matching distribution found"

A wheel was not downloaded for the correct platform. Re-run `pip download` on the build machine with the correct `--platform` and `--python-version` flags. Common issue: the build machine is macOS but the target is Linux — the platform wheels are incompatible.

### Extraction produces zero results for Go services

Check that `go_extractor.ast_helper_binary` points to the compiled binary. Check that the binary is executable (`chmod +x bin/ast_helper`). Check that `go_services.source_dir` is correct and contains `.go` files.

### Knowledge store queries return empty results

After extraction, indexing must be run separately. Verify with: `SELECT COUNT(*) FROM kep.kep_chunks`. If count is 0, run the indexer. If count is non-zero but queries return nothing, check the pgvector HNSW index exists: `\d kep.kep_chunks`.
