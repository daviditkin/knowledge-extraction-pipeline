# Java Service Extractor

Parses Java Spring Boot service source code and emits `ServiceDoc` JSON. Handles the handful of Java services alongside the primary Go codebase.

## What it extracts

For each Java service (directory with `pom.xml` or `build.gradle`):

- **Service identity**: name from `spring.application.name` or Maven `<artifactId>`, language = "java"
- **REST endpoints**: all `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping` methods with their full paths (class-level prefix + method path), HTTP method, and request/response type names
- **Service dependencies**: `@FeignClient` targets and `RestTemplate`/`WebClient` call URL patterns
- **Database access**: Spring Data repository entity types → inferred table names, `JdbcTemplate` calls
- **Log events**: SLF4J `log.info/warn/error/debug` calls with message templates and parameters

## Architecture

```
extractors/java-service/
├── extractor.py          # Uses javalang (pure Python Java parser)
├── spring_annotations.py # Annotation name constants and parsing helpers
└── tests/
    └── test_extractor.py
```

The extractor uses `javalang` for AST parsing. `javalang` is a pure Python Java parser — no JVM required. It handles Java 8–17 syntax (covers all current Spring Boot usage patterns).

## Configuration

```yaml
java_services:
  source_dir: /path/to/java-services
  include_patterns:
    - "**/*.java"
  exclude_patterns:
    - "**/test/**"
    - "**/src/test/**"
    - "**/*Test.java"
    - "**/*IT.java"
  internal_service_urls:
    # URL patterns that indicate a call to an internal service
    # If RestTemplate calls a URL matching these patterns, the target service name is extracted
    - pattern: "http://{{service-name}}.internal/"
      extract: "service-name"
```

## Output

One `ServiceDoc` JSON file per Java service, written to `extracted/services/<service-name>.json`.

```json
{
  "name": "biometric-gateway",
  "language": "java",
  "directory": "/java-services/biometric-gateway",
  "module_path": "com.company.biometric:biometric-gateway",
  "handlers": [
    {
      "name": "handleEnrollment",
      "http_method": "POST",
      "http_path": "/api/v1/enroll",
      "request_type": "EnrollmentRequest",
      "response_type": "ResponseEntity<EnrollmentResponse>",
      "calls_services": ["enrollment-svc"],
      "db_queries": [],
      "file": "src/main/java/.../GatewayController.java",
      "line_start": 45,
      "line_end": 72
    }
  ],
  "external_deps": ["enrollment-svc", "identity-svc"],
  "db_tables_referenced": ["gateway_audit_log"],
  "log_events": [
    {
      "service": "biometric-gateway",
      "level": "INFO",
      "message_template": "Received enrollment request for subject {}",
      "fields": ["subject_id"],
      "file": "src/main/java/.../GatewayController.java",
      "line": 50
    }
  ]
}
```

## Running

```bash
python scripts/run_extractors.py --config config/config.yaml --extractor java-service
```

## Notes on SLF4J Parameterized Logging

SLF4J uses `{}` placeholders in message templates. The extractor records these and matches them to argument positions. When argument names are identifiers (local variables), their names become field names in the `LogEvent`. When arguments are complex expressions, a positional name (`field_0`, `field_1`) is used.

Example: `log.info("Enrollment for subject {} completed in {}ms", subjectId, durationMs)` → fields: `["subjectId", "durationMs"]`

## Known Limitations

- Does not parse `.properties`/`.yml` files for `@Value` URL injection (external service URLs in properties are not resolved)
- Does not handle `@RestControllerAdvice` exception handler methods as endpoints
- Spring WebFlux reactive `@Controller` with `RouterFunction` (functional endpoint routing) is not supported; only annotation-based routing is extracted
- Gradle build files are not parsed for service name; Maven `pom.xml` is the primary source
