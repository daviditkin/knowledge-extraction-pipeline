# Configuration

All pipeline configuration lives in `config/config.yaml`. The file is validated at startup against the pydantic schema in `extractors/shared/config.py`.

## Files

| File | Description |
|---|---|
| `example.yaml` | Full example configuration with all options documented in comments |
| `config.yaml` | Your actual configuration (git-ignored; copy from example.yaml) |

## Quick Start

```bash
cp config/example.yaml config/config.yaml
# Edit config/config.yaml with your paths and database credentials
```

## Configuration Sections

### `database` — PostgreSQL connection

```yaml
database:
  host: localhost
  port: 5432
  database: your_application_db   # The database where kep schema will be created
  user: pipeline_user
  password: pipeline_password
  schema: kep                     # Schema name for all kep.* tables
  pool_min: 2
  pool_max: 10
```

### `embedding` — Local model settings

```yaml
embedding:
  model_path: ./models/all-MiniLM-L6-v2   # Downloaded by scripts/download_models.py
  batch_size: 32                           # Chunks per embedding batch
  device: cpu                              # cpu or cuda (use cpu unless GPU is available)
```

### `go_services` — Go service extractor

```yaml
go_services:
  source_dir: /path/to/services
  include_patterns: ["**/*.go"]
  exclude_patterns: ["**/vendor/**", "**/*_test.go", "**/testdata/**"]
  internal_module_prefix: "company.com/services/"
  ast_helper_binary: ./bin/ast_helper
```

### `java_services` — Java service extractor

```yaml
java_services:
  source_dir: /path/to/java-services
  include_patterns: ["**/*.java"]
  exclude_patterns: ["**/test/**", "**/*Test.java"]
```

### `flyway` — Schema extractor

```yaml
flyway:
  migrations_dir: /path/to/flyway/migrations
```

### `confluence` — Documentation extractor

```yaml
confluence:
  input_mode: xml_export    # or rest_api_dump
  export_dir: /path/to/confluence-export
```

### `ixm_spec` — IXM spec extractor

```yaml
ixm_spec:
  spec_dir: /path/to/ixm-spec
  format: auto              # auto, xsd, or custom
```

### `rag` — Query engine settings

```yaml
rag:
  token_budget: 6000        # Max tokens in context package (passed to prompt builder)
  vector_k: 20              # Top-K results from vector search
  keyword_k: 20             # Top-K results from keyword search
  rrf_k: 60                 # RRF smoothing constant
  min_vector_score: 0.3     # Drop vector results below this similarity
  cache_size: 50            # LRU cache entries for query results
  cache_ttl_minutes: 10
```

### `prompt_builder` — Prompt assembly

```yaml
prompt_builder:
  token_budget: 8000        # Max tokens in the final prompt
  templates_dir: ./prompt-builder/templates
  saved_prompts_dir: ./prompts/saved
```

### `ui` — Web UI

```yaml
ui:
  host: 127.0.0.1           # Bind to localhost only; use 0.0.0.0 for network access
  port: 8080
  reload: false             # Set to true during development
```

### `llm` — Optional LLM integration

```yaml
llm:
  provider: none            # Options: none, ollama
  ollama:
    base_url: http://localhost:11434
    model: llama3
    timeout_seconds: 120
```

## Environment Variables

Sensitive values (database password) can be provided as environment variables instead of in the YAML file:

```yaml
database:
  password: ${KEP_DB_PASSWORD}    # Reads from KEP_DB_PASSWORD env var
```

Supported env var overrides:
- `KEP_DB_HOST`, `KEP_DB_PORT`, `KEP_DB_NAME`, `KEP_DB_USER`, `KEP_DB_PASSWORD`
- `KEP_CONFIG_FILE` — path to config file (overrides `--config` argument)
