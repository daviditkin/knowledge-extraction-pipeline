# Knowledge Extraction and RAG Pipeline

A fully offline, self-contained system for extracting knowledge from a large microservice codebase and enabling RAG-enhanced prompt construction for use with ChatGPT (or a local LLM).

## Purpose

This system solves a specific problem: you have 120+ Go microservices, several Java Spring Boot services, a PostgreSQL schema managed by Flyway migrations, XML-based IXM specs, Confluence documentation, and OTEL/Splunk logs — all in a restricted private network with no external API access. You have ChatGPT available via web UI only.

The pipeline runs **outside** the restricted network to build tooling, then the packaged tooling is deployed inside the restricted network. Once deployed, it operates fully offline to:

1. Extract structured knowledge from your codebase and documentation
2. Store that knowledge in a local vector + graph database (using your existing PostgreSQL)
3. Answer queries about the system with retrieved context
4. Build enriched, copy-pasteable prompts for ChatGPT that include the right context

## Quick Start (Outside Restricted Network — Build Phase)

```bash
# 1. Clone or copy this repo to your development machine (public internet access)
git clone <this-repo>
cd knowledge-extraction-pipeline

# 2. Install Python dependencies (Python 3.10+)
pip install -r requirements.txt

# 3. Download the embedding model for offline use
python scripts/download_models.py

# 4. Copy your source code, Flyway migrations, and spec files into the input directory
#    (or configure paths in config/config.yaml)
cp config/example.yaml config/config.yaml
# Edit config/config.yaml to point to your source directories

# 5. Run the full extraction pipeline
python scripts/run_pipeline.py --config config/config.yaml

# 6. Bundle everything for deployment to the restricted network
python scripts/bundle_for_deployment.py --output dist/pipeline-bundle.tar.gz
```

## Quick Start (Inside Restricted Network — Deploy Phase)

```bash
# 1. Copy dist/pipeline-bundle.tar.gz to the restricted network
# 2. Extract it
tar -xzf pipeline-bundle.tar.gz
cd pipeline-bundle

# 3. Install from bundled wheels (no internet required)
pip install --no-index --find-links=./vendor/wheels -r requirements.txt

# 4. Set up pgvector extension on your existing PostgreSQL instance
psql -U <user> -d <database> -f scripts/setup_pgvector.sql

# 5. Run the indexer against your local codebase
python -m rag.indexer --config config/config.yaml

# 6. Start the web UI or use the CLI
python -m ui.server --config config/config.yaml
# OR
python -m ui.cli --config config/config.yaml
```

## Component Map

```
knowledge-extraction-pipeline/
├── extractors/           # Phase 1: Extract knowledge from sources
│   ├── go-service/       #   Parse Go ASTs, find handlers, deps, log calls
│   ├── java-service/     #   Parse Java/Spring Boot, find endpoints, beans
│   ├── flyway-schema/    #   Parse SQL migrations → current DB schema
│   ├── confluence/       #   Export and parse Confluence docs
│   ├── ixm-spec/         #   Parse IXM XML spec → message/field catalog
│   ├── log-patterns/     #   Scan code for OTEL log calls → log field catalog
│   └── shared/           #   Shared utilities: file walking, output formats
│
├── knowledge-store/      # Phase 2: Store and index extracted knowledge
│   ├── schema/           #   PostgreSQL + pgvector schema DDL
│   ├── embeddings/       #   Embedding generation (sentence-transformers)
│   └── graph/            #   Knowledge graph (service→service, service→DB)
│
├── rag/                  # Phase 3: Query engine
│   ├── indexer/          #   Build the index from extracted knowledge
│   └── query/            #   Hybrid search, context assembly, ranking
│
├── prompt-builder/       # Phase 4: Prompt construction for ChatGPT
│   └── templates/        #   Task-specific prompt templates
│
├── ui/                   # Phase 5: Web UI and CLI
│
├── config/               # Configuration files
├── scripts/              # Helper scripts
└── docs/                 # Design documentation
    ├── design/           #   Deep design docs per component
    └── adr/              #   Architecture Decision Records
```

## Technology Choices (All Offline-Compatible)

| Concern | Choice | Rationale |
|---|---|---|
| Vector storage | pgvector on existing PostgreSQL | No new infrastructure; already managed |
| Knowledge graph | PostgreSQL adjacency tables | Same instance, no Neo4j needed |
| Embedding model | sentence-transformers/all-MiniLM-L6-v2 | Runs locally, 80MB, strong performance |
| Go AST parsing | `go/ast` via subprocess or tree-sitter | Native Go toolchain available in codebase |
| Java parsing | javalang or tree-sitter-java | Pure Python, no JVM required for parsing |
| SQL parsing | sqlglot | Pure Python, no DB connection needed for parsing |
| XML parsing | lxml | Standard, handles IXM spec format |
| Web UI | FastAPI + HTMX | Lightweight, no React build toolchain needed |
| LLM (optional) | Ollama with llama3 or mistral | If available on restricted network |

## Key Constraints Honored

- **No external internet on restricted network**: all models, wheels, and data bundled ahead of time
- **No new infrastructure**: pgvector on existing PostgreSQL handles both vector store and knowledge graph
- **ChatGPT via web UI only**: prompt builder outputs copy-pasteable enriched prompts, not API calls
- **Works on the biometric system's actual stack**: extractors are tuned for Go microservices with IXM XML, Flyway migrations, and OTEL logging patterns

## Documentation

- [Architecture](ARCHITECTURE.md) — component diagram, data flows, design decisions
- [Implementation Plan](PLAN.md) — phased rollout with deliverables
- [Task List](TODO.md) — agent-ready task breakdown with acceptance criteria
- [Agent Guide](AGENTS.md) — instructions for AI agents implementing this system
- [System Design](docs/design/overview.md) — problem statement, constraints, approach
- [Extractor Design](docs/design/extractors.md) — deep design of all 5 extractors
- [Knowledge Store Design](docs/design/knowledge-store.md) — pgvector schema, chunking, graph
- [RAG Engine Design](docs/design/rag-engine.md) — query, context assembly, ranking
- [Prompt Builder Design](docs/design/prompt-builder.md) — templates, assembly, length management
- [Deployment Guide](docs/design/deployment.md) — restricted network packaging and setup
