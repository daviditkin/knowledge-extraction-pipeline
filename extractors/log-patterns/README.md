# Log Patterns Extractor

Scans all Go and Java source files for structured log call sites and emits a catalog of `LogPattern` JSON objects. This is a static analysis — no services need to be running.

## What it extracts

For every log call site in the codebase:

- **Service**: which service owns the file containing the log call
- **Level**: DEBUG, INFO, WARN, ERROR, FATAL
- **Message template**: the string literal passed as the log message (e.g., `"enrollment started"`)
- **Fields**: structured key names passed alongside the message (e.g., `["biometric_id", "correlation_id", "duration_ms"]`)
- **Location**: source file path and line number

## Why extract log patterns from code?

On the restricted network, we do not have programmatic access to Splunk. But we can still answer questions like:
- "What should I see in the logs when enrollment fails?" — answered by the log patterns for enrollment-svc at ERROR level
- "What fields are available on the 'enrollment completed' log event?" — answered by the log pattern for that message
- "What log events does the identity service emit?" — answered by all log patterns for identity-svc

This is useful for writing Splunk queries, debugging, and understanding service behavior.

## Detected Patterns

### Go (OTEL / slog)

```go
// slog standard library
slog.Info("enrollment started", "biometric_id", req.ID, "service", "enrollment-svc")
slog.Error("enrollment failed", "biometric_id", req.ID, "error", err)

// Named logger (common pattern)
logger.Info("enrollment started", slog.String("biometric_id", req.ID))
logger.With("biometric_id", req.ID).Info("enrollment started")

// OTEL span events
span.AddEvent("enrollment_started", trace.WithAttributes(
    attribute.String("biometric_id", req.ID),
    attribute.String("modality", req.Modality),
))
```

All of the above produce `LogPattern` entries with the message template and field names extracted.

### Java (SLF4J / Logback)

```java
// SLF4J parameterized
log.info("Enrollment started for subject {}", subjectId);
log.error("Enrollment failed for subject {}: {}", subjectId, e.getMessage());

// MDC fields
MDC.put("biometric_id", subjectId);
log.info("Processing enrollment");
```

SLF4J `{}` placeholders are matched to argument names where identifiable.

## Configuration

```yaml
log_patterns:
  # Scans the same source directories as the Go and Java extractors
  # No separate configuration needed; uses go_services.source_dir and java_services.source_dir

  # Optionally exclude paths (test files are already excluded by the Go/Java extractor config)
  exclude_patterns:
    - "**/vendor/**"
    - "**/*_test.go"
```

## Output

Single file written to `extracted/log-patterns.json` containing a list of all detected patterns.

```json
[
  {
    "service": "enrollment-svc",
    "level": "INFO",
    "message_template": "enrollment started",
    "fields": ["biometric_id", "service", "correlation_id"],
    "file": "cmd/server/handler.go",
    "line": 55
  },
  {
    "service": "enrollment-svc",
    "level": "ERROR",
    "message_template": "enrollment failed",
    "fields": ["biometric_id", "error", "duration_ms"],
    "file": "cmd/server/handler.go",
    "line": 78
  },
  {
    "service": "identity-svc",
    "level": "INFO",
    "message_template": "identity lookup completed",
    "fields": ["subject_id", "found", "duration_ms"],
    "file": "internal/lookup/handler.go",
    "line": 112
  }
]
```

## How the knowledge store uses this

Each service's log patterns are grouped into a single chunk for the knowledge store. When a developer asks "what do the logs look like when enrollment fails?", the RAG engine retrieves the log patterns chunk for enrollment-svc (via graph expansion for the service) and includes it in the prompt context.

## Running

```bash
python scripts/run_extractors.py --config config/config.yaml --extractor log-patterns
```

## Accuracy Notes

Static log extraction is approximately 90–95% accurate:
- **Correctly extracted**: all `slog.Info/Warn/Error/Debug` with string literal message and string key-value pairs
- **Partially extracted**: `logger.With("key", val).Info("msg")` — the `With` fields may not be attributed to the right log site in all cases
- **Missed**: dynamically-constructed log messages (`msg := "prefix: " + detail; log.Info(msg)`)
- **False positives**: ~1–2% from comments containing log-like syntax (these are filtered by checking that the match is not inside a `//` or `/* */` comment)

The 90–95% coverage is sufficient for the use case (providing context for debugging questions) — the log catalog does not need to be exhaustive.
