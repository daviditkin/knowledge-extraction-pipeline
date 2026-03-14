# ADR-003: Use Python as the primary pipeline language

**Status**: Accepted
**Date**: 2024-03
**Deciders**: Pipeline architecture team

---

## Context

The team's primary application language is Go. The pipeline needs to:
- Parse Go code (AST analysis)
- Parse Java code
- Parse SQL migrations
- Parse XML (IXM spec, Confluence export)
- Generate text embeddings
- Run a web UI
- Interface with PostgreSQL

Language options for the pipeline:
1. **Go** (same as the primary codebase)
2. **Python** (strong NLP/ML ecosystem)
3. **Java** (matches the Spring Boot services)
4. **TypeScript/Node.js** (strong web tooling)

## Decision

Use **Python** as the primary pipeline language, with a thin **Go** helper binary for Go AST parsing.

## Rationale

### The ML/NLP ecosystem is Python-first

The core capabilities of this pipeline depend on libraries that are Python-first:

| Capability | Python | Go | Notes |
|---|---|---|---|
| sentence-transformers | ✅ Native | ❌ None | Go has no equivalent |
| pgvector client | ✅ pgvector-python | ✅ pgvector-go | Both work |
| Java AST parsing | ✅ javalang | ❌ None | No Go library for Java parsing |
| SQL parsing | ✅ sqlglot | ⚠️ partial (pg_query_go) | sqlglot is richer |
| XML parsing (lxml) | ✅ lxml, etree | ✅ encoding/xml | Both adequate |
| HTML to Markdown | ✅ markdownify | ⚠️ gomarkdown | Python option is more complete |
| FastAPI web UI | ✅ FastAPI | ✅ Gin/Echo | Both viable |
| Fuzzy matching | ✅ fuzzywuzzy | ⚠️ manual | Python option is simpler |

The absence of a Python equivalent for sentence-transformers is decisive. There is no Go library for running sentence-transformer models. Go can call Python via subprocess or use ONNX Runtime (with additional complexity), but neither approach is as simple as `SentenceTransformer("model").encode(texts)`.

### Go for the Go AST helper is correct

The Go AST parser (`go/ast`) is the most reliable way to parse Go code. It's part of the Go standard library, handles all Go syntax edge cases, and is maintained by the Go team. Any alternative (tree-sitter, regex) is less accurate.

The solution: a thin Go binary (`extractors/go-service/ast_helper/main.go`) that accepts a file path and emits JSON. The Python pipeline invokes it as a subprocess. This gives us:
- `go/ast` accuracy for Go parsing
- Python for everything else (embedding, DB, web, XML, SQL)

This is a clean separation: the Go binary does one thing (parse a Go file) and the Python pipeline does orchestration and analysis.

### Java is not appropriate

The Java Spring Boot services represent a small fraction of the codebase. A Java pipeline would require:
- Maven or Gradle build tool
- JVM on the build and target machines
- More complex deployment (WAR/JAR vs. a Python script)

None of these are justified for what is tooling, not a production service.

### TypeScript/Node.js is not appropriate

Node.js would be excellent for the web UI component but poor for the ML and parsing components. Adding Node.js just for the UI when Python's FastAPI is adequate is unnecessary complexity.

### Go team members can still contribute

The pipeline uses straightforward Python (no async, no metaclasses, no magic frameworks). A Go developer can read and modify Python pipeline code without difficulty. Pydantic models are explicit and type-safe. The structure mirrors what a Go developer would write (structs with typed fields, explicit error handling, clear function signatures).

## Consequences

### Positive

- sentence-transformers, sqlglot, javalang, lxml, markdownify, FastAPI — all available natively
- Go AST parsing accuracy retained via the Go helper binary
- Python is the team's most likely second language after Go
- Faster development: rich ecosystem for every component (parsing, embedding, web, DB)

### Negative

- Team must maintain a Python virtual environment and know basic Python tooling (pip, venv)
- Deployment bundle includes Python wheels (larger than a Go binary)
- Python's dynamic typing requires more discipline to maintain quality; mitigated by pydantic and type annotations
- The subprocess interface to the Go AST helper is a cross-language boundary that adds complexity

### Mitigations

**Type safety**: all inter-module data flows through pydantic models. All function signatures have type annotations. Mypy is run in CI.

**Bundle size**: the Python wheels (including PyTorch) make the bundle 1.5GB. This is manageable for physical transfer to the restricted network. The alternative (pure Go with ONNX Runtime for embeddings) would also be large due to ONNX Runtime binaries.

**Go helper maintenance**: the Go AST helper is minimal (~200 lines) and uses only stdlib. It is unlikely to need frequent changes. When Go syntax changes, the Go team is better positioned to update it than the Python developers maintaining the rest of the pipeline.

## Rejected Alternative: Go with ONNX Runtime

A fully-Go pipeline could use ONNX Runtime (with Go bindings via CGo) to run the sentence-transformer model. This would produce a single Go binary deployable without Python. However:
- ONNX Runtime CGo bindings are not well maintained and have compatibility issues
- The model would need to be converted to ONNX format (an extra build step)
- Java parsing, SQL parsing, and HTML-to-Markdown would require custom Go implementations
- The development cost would be substantially higher with less library support

The result would be a less maintainable pipeline with more custom code for no operational benefit.
