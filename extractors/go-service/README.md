# Go Service Extractor

Parses Go microservice source code and emits structured `ServiceDoc` JSON for each service. This is the most complex extractor, handling ~120 Go services.

## What it extracts

For each Go service (directory with `go.mod` or `main.go`):

- **Service identity**: name (from module path or directory), module path, language
- **HTTP handlers**: method, path pattern, handler function name — detected via `http.HandleFunc`, gorilla/mux `r.Get/Post/...`, chi `r.Get/Post/...`, gin `router.GET/POST/...`
- **gRPC services**: service name and method — detected via `pb.RegisterXxxServer(...)` calls
- **Request/response types**: Go struct type names for handler parameters
- **External service calls**: internal service dependencies from import paths matching the configured prefix
- **Database queries**: SQL strings passed to `db.Query`, `db.Exec`, `sqlx.Get`, `sqlx.Select`, `tx.Query`, `tx.Exec` — table names extracted from SQL
- **Log events**: all `slog.Info/Warn/Error/Debug`, `logger.Info/...`, and OTEL span event calls with their message templates and structured field names

## Architecture

```
extractors/go-service/
├── extractor.py          # Main Python extractor — orchestrates per-service extraction
├── ast_helper/
│   ├── main.go           # Go binary — parses one .go file, emits JSON
│   └── go.mod
└── tests/
    └── test_extractor.py
```

The Python extractor invokes the Go binary as a subprocess for each source file. The binary uses `go/ast` and `go/parser` (standard library) and handles all Go syntax edge cases correctly.

## Go AST Helper

The helper accepts one argument (path to a `.go` file) and writes JSON to stdout:

```bash
./bin/ast_helper ./path/to/handler.go
```

Output:
```json
{
  "package": "main",
  "imports": ["net/http", "company.com/services/biometric-store-client"],
  "functions": [...],
  "http_handlers": [{"pattern": "/api/v1/enroll", "method": "POST", "handler_func": "EnrollHandler"}],
  "grpc_registrations": [],
  "struct_types": [...],
  "log_calls": [{"func_name": "slog.Info", "args": ["\"enrollment started\"", "\"biometric_id\"", "req.ID"], "line": 55}]
}
```

### Building the helper

```bash
cd extractors/go-service/ast_helper
go build -o ../../../bin/ast_helper .

# Cross-compile for Linux (if building on macOS):
GOOS=linux GOARCH=amd64 go build -o ../../../bin/ast_helper_linux .
```

## Configuration

```yaml
go_services:
  source_dir: /path/to/your/services
  include_patterns:
    - "**/*.go"
  exclude_patterns:
    - "**/vendor/**"
    - "**/*_test.go"
    - "**/testdata/**"
    - "**/mock_*.go"
  internal_module_prefix: "company.com/services/"   # Used to detect service-to-service calls
  ast_helper_binary: ./bin/ast_helper
```

## Output

One `ServiceDoc` JSON file per service, written to `extracted/services/<service-name>.json`.

Example output structure:
```json
{
  "name": "enrollment-svc",
  "language": "go",
  "directory": "/services/enrollment-svc",
  "module_path": "company.com/services/enrollment-svc",
  "handlers": [
    {
      "name": "EnrollHandler",
      "http_method": "POST",
      "http_path": "/api/v1/enroll",
      "grpc_service": null,
      "grpc_method": null,
      "request_type": "EnrollRequest",
      "response_type": "EnrollResponse",
      "calls_services": ["biometric-store-svc", "identity-svc"],
      "db_queries": ["INSERT INTO enrollment_records ..."],
      "file": "cmd/server/handler.go",
      "line_start": 42,
      "line_end": 89
    }
  ],
  "external_deps": ["biometric-store-svc", "identity-svc", "notification-svc"],
  "db_tables_referenced": ["enrollment_records", "biometric_templates"],
  "log_events": [...],
  "file_hash_map": {"cmd/server/handler.go": "sha256:..."}
}
```

## Running

```bash
# Run just the Go service extractor
python scripts/run_extractors.py --config config/config.yaml --extractor go-service

# Run with verbose logging
python scripts/run_extractors.py --config config/config.yaml --extractor go-service --log-level DEBUG

# Run changed files only
python scripts/run_extractors.py --config config/config.yaml --extractor go-service --changed-only
```

## Known Limitations

- Does not follow function calls into called functions (only analyzes registration sites and the handler function itself; does not trace through middleware)
- SQL detection only works for string literal queries; dynamically-constructed SQL is noted as `"<dynamic SQL>"` with a warning
- Handler detection covers gorilla/mux, chi, gin, and stdlib; other routers require adding patterns to the configuration
- Does not parse `.proto` files; gRPC service registrations are found but method-level details require proto parsing (future enhancement)
