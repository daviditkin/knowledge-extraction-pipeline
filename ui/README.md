# User Interface

Two interface options for the knowledge pipeline:

1. **Web UI** (FastAPI + HTMX): a browser-based interface, no JavaScript build tools required
2. **CLI** (Typer + Rich): a command-line interface for terminal users and scripting

Both run fully offline. The web UI serves all assets locally; no CDN, no npm.

## Web UI

### Starting the server

```bash
python -m ui.server --config config/config.yaml
# Opens at http://localhost:8080

# Custom host/port
python -m ui.server --config config/config.yaml --host 0.0.0.0 --port 9090
```

### Features

- **Query input**: text area for the question, optional intent dropdown to override auto-detection
- **Prompt display**: formatted prompt with syntax-highlighted code sections
- **Copy button**: one click to copy the prompt to the clipboard (using `navigator.clipboard.writeText`)
- **Show Sources**: toggle to reveal which chunks were included and their relevance scores
- **Save Prompt**: button to save the prompt to `prompts/saved/` with a timestamp
- **Saved Prompts**: list of previously saved prompts, loadable with one click
- **Service Explorer**: browse the knowledge graph — click a service to see its handlers, dependencies, tables, and log events

### Technology

- **FastAPI**: serves HTML pages and handles HTMX partial updates
- **Jinja2**: server-side template rendering (no client-side framework)
- **HTMX**: `hx-post="/query"` submits the form and replaces `<div id="result">` with the rendered prompt. HTMX is bundled as `ui/static/htmx.min.js` — no CDN dependency.
- **CSS**: plain CSS, no framework. Bundled in `ui/static/style.css`.
- **JavaScript**: `ui/static/app.js` — fewer than 30 lines. Only one function: `copyToClipboard()`.

### Page structure

```
/                    → index.html (query form + result area)
/query               → POST: run query + build prompt, return result partial
/prompts/saved       → list saved prompts
/prompts/save        → POST: save current prompt
/explore             → service graph explorer
/explore/service/{name} → service detail page
/health              → health check endpoint
```

---

## CLI

### Installation check

```bash
python -m ui.cli --help
```

### Commands

#### `query` — Build a prompt for a question

```bash
python -m ui.cli query "how does biometric enrollment work"
python -m ui.cli query "trace enrollment request" --intent trace_request
python -m ui.cli query "enrollment handler" --save
python -m ui.cli query "what tables does enrollment-svc use" --budget 10000
```

Output: formatted prompt printed to stdout. With `--save`: also saved to `prompts/saved/`.

#### `show-service` — Display service details

```bash
python -m ui.cli show-service enrollment-svc
```

Output (Rich-formatted table):
```
Service: enrollment-svc (Go)
Directory: /services/enrollment-svc

Handlers (3):
  Method  Path                      Request Type     Response Type
  ─────────────────────────────────────────────────────────────
  POST    /api/v1/enroll            EnrollRequest    EnrollResponse
  GET     /api/v1/enroll/{id}       -                EnrollStatusResponse
  DELETE  /api/v1/enroll/{id}       -                -

Dependencies (2):
  → biometric-store-svc (http_client)
  → notification-svc (http_client)

Tables (1):
  enrollment_records (write)

Log Events (4):
  [INFO]  "enrollment started"         fields: biometric_id, correlation_id
  [INFO]  "enrollment completed"       fields: biometric_id, duration_ms
  [ERROR] "enrollment failed"          fields: biometric_id, error
  [WARN]  "duplicate enrollment"       fields: biometric_id, existing_id
```

#### `show-graph` — Display service dependency graph

```bash
python -m ui.cli show-graph --from-service enrollment-svc --depth 2
```

Output (Rich tree):
```
enrollment-svc
├── biometric-store-svc  [http]
│   └── biometric-db-svc  [grpc]
└── notification-svc  [http]
```

#### `show-schema` — Display a table definition

```bash
python -m ui.cli show-schema biometric_records
```

#### `search` — Run a raw query and show retrieved chunks

```bash
python -m ui.cli search "enrollment handler HTTP endpoint"
# Shows the top-10 retrieved chunks with their scores and source types
```

#### `reindex` — Re-run extraction and indexing

```bash
python -m ui.cli reindex --changed-only
python -m ui.cli reindex --source-type go_service
```

#### `stats` — Show knowledge store statistics

```bash
python -m ui.cli stats
```

Output:
```
Knowledge Store Statistics
  Services indexed:    124 (120 Go, 4 Java)
  Chunks indexed:      47,823
  Confluence pages:    312
  IXM message types:   28
  DB tables:           48
  Log patterns:        5,241
  Last indexed:        2024-03-15 02:00:12 UTC
  Index size:          428 MB
```

---

## Module Structure

```
ui/
├── server.py           # FastAPI application
├── cli.py              # Typer CLI entry point and command registration
├── cli_commands/
│   ├── query.py        # query command
│   ├── show.py         # show-service, show-graph, show-schema
│   ├── search.py       # search command
│   └── reindex.py      # reindex command
├── templates/
│   ├── index.html      # Main query page
│   ├── result.html     # Prompt display partial (for HTMX)
│   ├── service.html    # Service detail page
│   └── base.html       # Base template with nav
└── static/
    ├── htmx.min.js     # HTMX (bundled, no CDN)
    ├── style.css       # Application CSS
    └── app.js          # copyToClipboard() and nothing else
```
