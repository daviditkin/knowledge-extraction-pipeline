# Extractor Design

Each extractor reads a specific knowledge source and emits structured JSON conforming to the pydantic models in `extractors/shared/models.py`. All extractors share the same file walking, hash caching, and output writing utilities from `extractors/shared/`.

---

## Shared Utilities

### File Walker (`extractors/shared/file_walker.py`)

The `FileWalker` class handles recursive directory traversal with glob filtering and content-based change detection.

**Change detection**: each run computes SHA-256 of file content and compares against a stored hash cache (`extracted/.hashes/<extractor>.json`). Only changed files are re-extracted on incremental runs. This is safe because the extractors are purely functional (same input → same output), so an unchanged file always produces the same extraction output.

**Glob patterns**: include/exclude patterns use Python's `fnmatch` semantics. Example config:
```yaml
go_service:
  include: ["**/*.go"]
  exclude: ["**/vendor/**", "**/*_test.go", "**/testdata/**", "**/mock_*.go"]
```

### Output Models (`extractors/shared/models.py`)

All pydantic v2 models. The key types are:

```python
class HandlerInfo(BaseModel):
    name: str
    http_method: Optional[str]      # GET, POST, PUT, DELETE, PATCH, HEAD
    http_path: Optional[str]        # e.g., "/api/v1/enroll/{id}"
    grpc_service: Optional[str]     # e.g., "BiometricService"
    grpc_method: Optional[str]      # e.g., "Enroll"
    request_type: Optional[str]     # Go/Java type name
    response_type: Optional[str]
    calls_services: list[str]       # internal service names called
    db_queries: list[str]           # SQL strings found in handler body
    file: str                       # source file path
    line_start: int
    line_end: int

class ServiceDoc(BaseModel):
    name: str                       # e.g., "enrollment-svc"
    language: Literal["go", "java"]
    directory: str                  # absolute path to service root
    module_path: str                # Go: module path from go.mod; Java: groupId:artifactId
    handlers: list[HandlerInfo]
    external_deps: list[str]        # other service names this service calls
    db_tables_referenced: list[str] # table names found in SQL queries
    log_events: list[LogEvent]
    file_hash_map: dict[str, str]   # file path → SHA-256, for change detection
```

---

## Go Service Extractor

### Architecture

The Go extractor uses a two-tier approach:
1. A thin Go binary (`ast_helper`) that uses `go/ast` to parse individual `.go` files and emit JSON
2. A Python wrapper that invokes ast_helper as a subprocess for each file and aggregates results per service

**Why a Go helper?** Go's `go/ast` package is the most reliable way to parse Go code. It handles edge cases (build tags, generated code, interface embeddings) that regex-based or tree-sitter-based parsers miss. The Go compiler's own parser does not require a running Go module — it can parse files in isolation.

### Service Discovery

A "service" is a directory that contains either:
- A `go.mod` file (Go module root — the most common pattern for microservices)
- A `main.go` file with `package main` (single-file services or older patterns)

The service name is derived from:
1. `go.mod` module path: take the last path component, e.g., `company.com/services/enrollment-svc` → `enrollment-svc`
2. Directory name as fallback

### Go AST Helper (`extractors/go-service/ast_helper/main.go`)

The helper parses a single `.go` file and emits:

```json
{
  "package": "main",
  "imports": ["net/http", "github.com/gorilla/mux", "company.com/services/biometric-store-client"],
  "functions": [
    {
      "name": "EnrollHandler",
      "params": [{"name": "w", "type": "http.ResponseWriter"}, {"name": "r", "type": "*http.Request"}],
      "returns": [],
      "start_line": 42,
      "end_line": 89,
      "body_text": "func EnrollHandler(w http.ResponseWriter, r *http.Request) {\n..."
    }
  ],
  "http_handlers": [
    {
      "pattern": "/api/v1/enroll",
      "method": "POST",
      "handler_func": "EnrollHandler",
      "registration_line": 15,
      "router_type": "gorilla/mux"
    }
  ],
  "grpc_registrations": [
    {
      "service_name": "BiometricService",
      "handler_var": "server",
      "registration_line": 22
    }
  ],
  "struct_types": [
    {
      "name": "EnrollRequest",
      "fields": [
        {"name": "BiometricID", "type": "string", "json_tag": "biometric_id"},
        {"name": "Template", "type": "[]byte", "json_tag": "template"}
      ]
    }
  ],
  "log_calls": [
    {
      "func_name": "slog.Info",
      "args": ["\"enrollment started\"", "\"biometric_id\"", "req.BiometricID", "\"service\"", "\"enrollment-svc\""],
      "line": 55
    }
  ]
}
```

**HTTP handler detection patterns** (all result in an `http_handlers` entry):
- `http.HandleFunc("/path", HandlerFunc)` — standard library
- `mux.HandleFunc("/path", HandlerFunc)` — gorilla/mux variable
- `r.Get("/path", HandlerFunc)`, `r.Post(...)`, etc. — chi router
- `router.GET("/path", HandlerFunc)`, `router.POST(...)` — gin
- `r.Handle("/path", http.HandlerFunc(...))` — generic

**gRPC registration detection**:
- Match `pb.Register<ServiceName>Server(grpcServer, <handler>)` where the function name starts with `Register` and ends with `Server`

**Log call detection**:
- Match any call expression where the selector (function name) contains: `Info`, `Warn`, `Warning`, `Error`, `Debug`, `Fatal`, `Log`, `With`, `Emit`
- Check the receiver/package name for: `log`, `logger`, `slog`, `span`, `otel`
- Extract all arguments as strings

### Python Aggregation Layer

The Python `GoServiceExtractor`:

1. Invokes ast_helper on every `.go` file in the service directory
2. Aggregates across all files:
   - All `http_handlers` + `grpc_registrations` → `handlers: list[HandlerInfo]`
   - All imports matching the internal service pattern → `external_deps`
   - All log_calls → `log_events` (parse key-value args from the argument list)
   - All SQL strings found in `db.Query(...)`, `db.Exec(...)`, `sqlx.Get(...)` call args → `db_tables_referenced` (parse table names from SQL)

**Log call parsing**: for `slog.Info("message", "key1", val1, "key2", val2)`, the message is arg[0] (string literal), and subsequent args come in pairs where odd-indexed string literals are field names. For `logger.With("key1", val1).Info("message")`, the `With` args provide the fields. This requires recognizing the chain pattern.

**SQL table extraction**: parse SQL string literals found as arguments to db query functions. Use `sqlglot.parse_one(sql).find_all(exp.Table)` to get table names. Handle cases where the SQL is not a string literal (e.g., a variable reference) by logging a warning and skipping.

**Internal service dependency detection**: given a configured internal module prefix (e.g., `company.com/services/`), any import matching `<prefix><service-name>` or `<prefix><service-name>/client` is treated as a dependency on `<service-name>`.

---

## Java Spring Boot Extractor

### Architecture

Uses `javalang` (pure Python Java parser). Does not require a JVM.

### Service Discovery

A Java service is identified by:
- A `pom.xml` (Maven) with Spring Boot parent/dependency
- A `build.gradle` (Gradle) with `spring-boot-starter-web` dependency

Service name priority:
1. `spring.application.name` in `src/main/resources/application.properties` or `application.yml`
2. Maven `<artifactId>` in `pom.xml`
3. Directory name

### Endpoint Extraction

Walk all `.java` files. For each class annotated with `@RestController` or `@Controller`:
1. Extract class-level `@RequestMapping` path prefix
2. For each method annotated with `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`:
   - Combine class prefix + method path → full path
   - Extract HTTP method from annotation name
   - Extract first parameter type (the `@RequestBody` type) as `request_type`
   - Extract return type as `response_type`

### Dependency Detection

**FeignClient**: classes annotated `@FeignClient(name="service-name", url="...")` → add `service-name` to `external_deps`

**RestTemplate/WebClient**: method bodies containing `restTemplate.postForObject(...)`, `webClient.post().uri(...)` — extract the URL string if it's a literal; try to resolve it if it references a `@Value("${some.url}")` property.

**Spring Data Repositories**: interfaces extending `JpaRepository`, `CrudRepository`, `PagingAndSortingRepository` — infer table name from the entity generic type parameter (e.g., `JpaRepository<BiometricRecord, UUID>` → table `biometric_records` via snake_case conversion of `BiometricRecord`).

### Log Call Extraction

SLF4J patterns:
- `log.info("message {} {}", arg1, arg2)` — parameterized logging. Message template is the first string arg; parameters are subsequent args. Field names are inferred from `{}` position in the template.
- `log.error("message", exception)` — when the second arg is not a string, it's an exception
- `MDC.put("key", value)` — extract the key as a field name associated with the current service

---

## Flyway Schema Extractor

### Migration File Discovery

Flyway migration files follow the naming convention: `V{version}__{description}.sql`

Version sorting: split on `_` to get version components, compare lexicographically by major/minor version. `V1_1` sorts between `V1` and `V2`. `V10` sorts after `V9` (numeric, not lexicographic).

File types:
- `V*.sql` — versioned migrations (applied in order)
- `R__*.sql` — repeatable migrations (applied whenever checksum changes)
- `B__*.sql` — baseline (skip; represents the schema at baseline point)

### SQL Parsing Strategy

Use `sqlglot` to parse each migration file. Parse in dialect `postgres`. Process each statement in order.

**Schema state machine**: maintain an in-memory dict mapping table names to their current column/constraint definitions.

```python
schema_state: dict[str, TableState] = {}

class TableState:
    columns: dict[str, ColumnState]
    indexes: list[IndexState]
    foreign_keys: list[ForeignKeyState]
    created_in_migration: str

class ColumnState:
    name: str
    data_type: str
    nullable: bool
    default: Optional[str]
    is_primary_key: bool
```

**Statement handlers**:

`CREATE TABLE foo (col1 TYPE1, col2 TYPE2, ...)`:
- Create new `TableState`
- Parse column list: extract name, type, `NOT NULL`, `DEFAULT`, `PRIMARY KEY` inline constraints
- Extract `CONSTRAINT pk_foo PRIMARY KEY (col1, col2)` for composite PKs
- Extract inline `REFERENCES other_table(col)` for inline FKs

`ALTER TABLE foo ADD COLUMN bar TYPE [NOT NULL] [DEFAULT val]`:
- Add column to existing table's `columns` dict

`ALTER TABLE foo DROP COLUMN bar`:
- Remove column from `columns` dict

`ALTER TABLE foo ALTER COLUMN bar TYPE new_type`:
- Update `data_type` in `columns` dict

`ALTER TABLE foo ALTER COLUMN bar SET NOT NULL`:
- Set `nullable = False`

`ALTER TABLE foo ALTER COLUMN bar DROP NOT NULL`:
- Set `nullable = True`

`ALTER TABLE foo ADD CONSTRAINT fk_... FOREIGN KEY (col) REFERENCES other_table(col)`:
- Append to `foreign_keys`

`CREATE [UNIQUE] INDEX idx_name ON foo (col1, col2)`:
- Append to table's `indexes`

`DROP TABLE [IF EXISTS] foo`:
- Remove from `schema_state`

`CREATE VIEW foo AS ...`:
- Record view name in separate `views` list

**Handling SQL parse failures**: if `sqlglot` cannot parse a statement (uncommon but possible for very PG-specific syntax), log the statement text and migration version at WARNING level and continue. Do not crash.

### Output

Emit a single `SchemaDoc` with all tables, their columns, indexes, and foreign keys. The `as_of_migration` field is the version string of the last processed migration file.

---

## Confluence Extractor

### Input Mode 1: XML Space Export

When a Confluence administrator exports a space, the result is a ZIP file containing an `entities.xml` file and media attachments.

`entities.xml` structure (relevant parts):
```xml
<hibernate-generic datetime="...">
  <object class="Space" package="com.atlassian.confluence.spaces">
    <property name="key"><![CDATA[MYSPACE]]></property>
    ...
  </object>
  <object class="Page" package="com.atlassian.confluence.pages">
    <id name="id">12345</id>
    <property name="title"><![CDATA[Enrollment Service]]></property>
    <property name="space"><!-- reference to space --></property>
    <property name="parent"><!-- reference to parent page --></property>
    <property name="bodyContents">
      <!-- collection of BodyContent objects -->
    </property>
    ...
  </object>
  <object class="BodyContent" ...>
    <id name="id">67890</id>
    <property name="body"><![CDATA[<p>Service description...</p>]]></property>
  </object>
</hibernate-generic>
```

Parsing approach:
1. Parse `entities.xml` with `lxml.etree`
2. Build an index of all objects by class name and ID
3. For each `Page` object: extract ID, title, parent reference, labels
4. Resolve `bodyContents` → `BodyContent` → `body` HTML
5. Convert HTML to Markdown using `markdownify` with custom converters for Confluence macros

**Confluence macro handling**:
- `<ac:structured-macro ac:name="info">` / `note` / `warning` / `tip`: convert the macro body to a Markdown blockquote with appropriate prefix
- `<ac:structured-macro ac:name="code">`: extract content, wrap in a Markdown code fence. Detect language from `ac:parameter[@ac:name="language"]`
- `<ac:structured-macro ac:name="toc">`: replace with `*(Table of contents omitted)*`
- `<ac:link>` and `<ac:image>`: replace with a placeholder or the link text
- Unknown macros: extract text content, discard the macro wrapper

### Input Mode 2: REST API JSON Dump

For use during the build phase when Confluence is accessible:

```bash
# Export all pages in a space
curl -u user:token \
  "https://confluence.company.com/rest/api/content?spaceKey=MYSPACE&expand=body.storage,metadata.labels,ancestors&limit=100" \
  -o confluence_export.json
```

The `body.storage.value` field contains Confluence Storage Format (a dialect of XML). Parse it with `lxml.etree` and apply the same macro handling as the XML export mode.

### Service Association

Associate each Confluence page with one or more services by:

1. **Exact title match**: if the page title exactly matches a known service name (case-insensitive, with `-` normalized to spaces), associate it.
2. **Label match**: if the page has a Confluence label matching a service name, associate it.
3. **Content scan**: tokenize the first 500 words of the page content. Count occurrences of each known service name (case-insensitive). If a service name appears 3+ times, associate it.
4. **URL/code pattern match**: if the page contains references to the service's internal module path (`company.com/services/enrollment-svc`), associate it.

The `service_refs` field on `DocPage` lists all associated service names.

---

## IXM Spec Extractor

### Input Format

The IXM (Identity Exchange Message) spec describes the XML message format for the system's front door and back door. It may be provided as:
- An XML Schema Definition (`.xsd`) — if the spec is formalized as an XSD
- A custom XML document with proprietary element names — if the spec is hand-written

The extractor handles both formats.

### XSD-based Spec

Walk `xs:element` and `xs:complexType` definitions:

```xml
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="EnrollRequest">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="BiometricID" type="xs:string" minOccurs="1" maxOccurs="1"/>
        <xs:element name="Template" type="xs:base64Binary" minOccurs="1" maxOccurs="1"/>
        <xs:element name="Modality" minOccurs="0" maxOccurs="1">
          <xs:simpleType>
            <xs:restriction base="xs:string">
              <xs:enumeration value="FINGERPRINT"/>
              <xs:enumeration value="IRIS"/>
              <xs:enumeration value="FACE"/>
            </xs:restriction>
          </xs:simpleType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
```

Extraction:
- Top-level `xs:element` → one `SpecDoc` per element
- `xs:sequence` / `xs:all` → field list
- `minOccurs="0"` → `required=False`; `minOccurs="1"` → `required=True`
- `maxOccurs="unbounded"` or `>1` → `cardinality="many"`; otherwise `cardinality="one"`
- `xs:restriction base="xs:string"` with `xs:enumeration` children → `allowed_values` list
- `xs:restriction` with `xs:pattern` → `validation_pattern` (the regex)
- `xs:annotation/xs:documentation` → `description`

### Custom XML-based Spec

If the spec uses a custom format, the extractor looks for common patterns:
```xml
<MessageTypes>
  <MessageType name="EnrollRequest" direction="inbound">
    <Description>Request to enroll a new biometric subject</Description>
    <Fields>
      <Field name="BiometricID" type="string" required="true" maxLength="36">
        <Description>UUID of the biometric subject</Description>
        <ValidationRule pattern="[0-9a-f-]{36}"/>
      </Field>
    </Fields>
  </MessageType>
</MessageTypes>
```

The extractor uses configurable XPath expressions to handle variations in the custom format.

### Direction Inference

If direction is not explicit in the spec:
- Names ending in `Request`, `Input`, `Message` + no corresponding `Response` → `inbound`
- Names ending in `Response`, `Result`, `Output`, `Ack` → `outbound`
- Names appearing in pairs (`EnrollRequest` + `EnrollResponse`) → `inbound` for Request, `outbound` for Response
- Otherwise: `both`

---

## Log Pattern Extractor

### Scope

The log pattern extractor scans all Go and Java source files for log call sites. It does not execute the code — it statically analyzes the source text with regex patterns.

**Why static analysis rather than runtime capture?** On the restricted network, we cannot run the services to capture their logs. Static analysis from code gives us the log catalog without requiring execution. The tradeoff is that dynamic log fields (computed values) are represented as their source expressions, not their runtime values.

### Go Log Pattern Detection

Regex patterns applied to each line of each `.go` file:

**slog patterns**:
```python
# slog.Info("message", "key1", val1, "key2", val2)
SLOG_CALL = re.compile(
    r'slog\.(Info|Warn|Warning|Error|Debug|Fatal)\s*\(\s*"([^"]+)"(.*?)\)',
    re.DOTALL
)

# logger.Info("message", ...)  where logger is *slog.Logger
LOGGER_CALL = re.compile(
    r'\blog(ger)?\.(Info|Warn|Warning|Error|Debug|Fatal)\s*\(\s*"([^"]+)"(.*?)\)',
    re.DOTALL
)

# logger.With("key", val).Info("message")
LOGGER_WITH = re.compile(
    r'\.With\s*\((.*?)\)\.(Info|Warn|Error|Debug)\s*\(\s*"([^"]+)"',
    re.DOTALL
)
```

**OTEL span event patterns**:
```python
# span.AddEvent("event_name", trace.WithAttributes(attribute.String("key", "val")))
SPAN_EVENT = re.compile(
    r'\.AddEvent\s*\(\s*"([^"]+)"'
)

# attribute.String("key", val), attribute.Int("key", val), etc.
ATTRIBUTE = re.compile(
    r'attribute\.(String|Int|Int64|Float64|Bool)\s*\(\s*"([^"]+)"'
)
```

**Key-value field extraction from slog calls**:
Parse the argument list after the message template. String literals in odd positions (index 1, 3, 5, ...) are field names. Example:
- `"biometric_id", req.ID, "service", "enrollment-svc"` → fields: `["biometric_id", "service"]`
- `slog.String("key", val), slog.Int("count", n)` → fields: `["key", "count"]`

### Java Log Pattern Detection

SLF4J parameterized logging:
```python
# log.info("message {} with {} placeholders", arg1, arg2)
SLF4J_CALL = re.compile(
    r'\blog\.(info|warn|warning|error|debug|trace)\s*\(\s*"([^"]+)"'
)

# MDC.put("key", value)
MDC_PUT = re.compile(
    r'MDC\.put\s*\(\s*"([^"]+)"'
)
```

For SLF4J, the field names are inferred from `{}` placeholder positions in the message template combined with the argument names where identifiable. If arguments are method calls or complex expressions, the placeholder index is used as the field name (`field_0`, `field_1`, etc.).

### Service Attribution

The log pattern extractor attributes each log call to the service that owns the file. It uses the same service discovery logic as the Go and Java extractors: map file path to service directory by finding the nearest ancestor directory with a `go.mod` or `pom.xml`.

### Output

The log patterns are written to `extracted/log-patterns.json` as a list of `LogPattern` objects, grouped by service. Example:

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
  }
]
```
