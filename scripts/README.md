# Scripts

Helper scripts for setup, extraction, indexing, and deployment bundling.

## Scripts Overview

| Script | When to run | What it does |
|---|---|---|
| `download_models.py` | Build machine (internet) | Downloads embedding model to `models/` |
| `run_extractors.py` | Build or restricted network | Runs all extractors, writes to `extracted/` |
| `run_pipeline.py` | Restricted network | Full pipeline: extract → index → verify |
| `bundle_for_deployment.py` | Build machine (internet) | Creates deployment tarball with vendored wheels |
| `setup_pgvector.sql` | Restricted network (one time) | Creates pgvector extension and kep schema |

## `download_models.py`

Run on a machine with internet access before building the deployment bundle.

```bash
python scripts/download_models.py

# Downloads to models/all-MiniLM-L6-v2/
# Verifies the download with a test embedding
# Prints the model directory size

# Download a specific model (default: all-MiniLM-L6-v2)
python scripts/download_models.py --model sentence-transformers/all-MiniLM-L6-v2

# Download to a custom path
python scripts/download_models.py --output-dir /custom/models/path
```

## `run_extractors.py`

Run on the restricted network server (or the build machine if source code is accessible there).

```bash
# Run all extractors
python scripts/run_extractors.py --config config/config.yaml

# Run only changed files
python scripts/run_extractors.py --config config/config.yaml --changed-only

# Run a single extractor
python scripts/run_extractors.py --config config/config.yaml --extractor go-service
# Extractors: go-service, java-service, flyway-schema, confluence, ixm-spec, log-patterns

# Debug a single service
python scripts/run_extractors.py --config config/config.yaml --extractor go-service --service enrollment-svc

# Verbose output
python scripts/run_extractors.py --config config/config.yaml --log-level DEBUG
```

Output:
```
[1/6] Running Go service extractor...
      Processing 120 services (changed: 3)
      ✓ enrollment-svc (12 handlers, 3 deps)
      ✓ identity-svc (8 handlers, 5 deps)
      ⚠ legacy-svc: parse warning in auth.go:342 (continuing)
      Completed: 120/120 services, 1 warning, 0 errors

[2/6] Running Java service extractor...
...

Summary:
  Services: 124 extracted
  Schema: 48 tables
  Confluence: 312 pages
  IXM spec: 28 message types
  Log patterns: 5,241 patterns
  Total time: 4m 32s
  Errors: 0, Warnings: 2
```

## `run_pipeline.py`

Convenience script that runs extraction and indexing in sequence, then verifies.

```bash
# Full pipeline
python scripts/run_pipeline.py --config config/config.yaml

# Changed files only (for nightly runs)
python scripts/run_pipeline.py --config config/config.yaml --changed-only

# Skip extraction (re-index from existing extracted/ files)
python scripts/run_pipeline.py --config config/config.yaml --skip-extraction
```

## `bundle_for_deployment.py`

Run on the build machine (with internet access) to create the deployment bundle.

```bash
# Standard bundle for Linux x86_64
python scripts/bundle_for_deployment.py \
  --config config/config.yaml \
  --output dist/pipeline-bundle.tar.gz \
  --platform linux_x86_64 \
  --python-version 3.10

# Bundle includes:
# - Python source code
# - vendor/wheels/ (all required Python wheels, no-index install)
# - models/all-MiniLM-L6-v2/ (embedding model)
# - bin/ast_helper (compiled Go binary for target platform)
# - scripts/setup_pgvector.sql
# - config/example.yaml
# - requirements.txt

# Verify bundle contents
python scripts/bundle_for_deployment.py --verify dist/pipeline-bundle.tar.gz
```

**Prerequisite**: run `download_models.py` before bundling (model must be in `models/`).

## `setup_pgvector.sql`

Run once on the restricted network server against your PostgreSQL instance.

```bash
# Requires PostgreSQL superuser for CREATE EXTENSION
psql -h <host> -U postgres -d <database> -f scripts/setup_pgvector.sql

# Then grant the pipeline user access to the kep schema:
psql -h <host> -U postgres -d <database> -c "GRANT USAGE, CREATE ON SCHEMA kep TO pipeline_user"
```

Contents:
```sql
CREATE EXTENSION IF NOT EXISTS vector;      -- requires pgvector 0.5.0+
CREATE SCHEMA IF NOT EXISTS kep;            -- pipeline schema
```

## Nightly Cron Setup

To keep the knowledge store up to date with code changes:

```bash
# Add to crontab (crontab -e)
# Run at 2am daily
0 2 * * * /path/to/pipeline/.venv/bin/python \
  /path/to/pipeline/scripts/run_pipeline.py \
  --config /path/to/pipeline/config/config.yaml \
  --changed-only \
  >> /var/log/knowledge-pipeline/nightly.log 2>&1
```
