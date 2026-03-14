# Prompt Builder Design

The prompt builder transforms a `ContextPackage` from the RAG engine into a formatted, copy-pasteable prompt for ChatGPT. The output is plain text that fits within ChatGPT's context window and provides enough context for a high-quality answer.

---

## Design Philosophy

### The Prompt Is the Product

Since there is no ChatGPT API, the prompt is literally the product — the user reads it, copies it, and pastes it. This means:
- **Formatting matters**: the prompt must be scannable. Section headers should be obvious.
- **Self-contained**: the prompt cannot reference "see attached" or "as discussed earlier". Everything needed for ChatGPT to answer is in the prompt.
- **Honest framing**: the system context section must accurately describe what the user is working on, or ChatGPT will apply generic knowledge that doesn't fit.
- **Length discipline**: a prompt that is too long will be truncated by ChatGPT's context window. A prompt that is too short wastes the opportunity to provide useful context. Target: 7,000–8,000 tokens.

### System Context Is Non-Negotiable

Every prompt starts with a system context block that describes:
1. What system this is (biometric identity management system)
2. What the codebase looks like (Go microservices, Java Spring Boot, IXM XML, Flyway, OTEL)
3. An instruction to use only the provided context to answer

This ensures ChatGPT doesn't hallucinate about generic "enrollment systems" and instead reasons about the specific architecture the user is working on.

---

## Prompt Templates

Templates are stored in `prompt-builder/templates/` as plain text files using Python's `string.Template` syntax (`$variable` or `${variable}`). They are loaded at startup and filled in by the builder.

### Template Structure

All templates share the same structure. The differences are in the APPROACH section.

```
===== SYSTEM CONTEXT =====
You are analyzing a biometric identity and encounter management system.

Architecture:
- Approximately 120 Go microservices and several Java Spring Boot services
- Each service has its own Go module (go.mod) and is deployed independently
- External communication uses an XML-based IXM (Identity Exchange Message) spec
- Internal service communication uses JSON over HTTP and gRPC
- Database: PostgreSQL managed with Flyway migrations
- Logging: OpenTelemetry (OTEL) structured logging, shipped to Splunk
- Front door and back door services translate IXM XML ↔ internal JSON

Use only the context provided in the sections below. If the context does not contain
enough information to answer the question, say so explicitly.

===== APPROACH =====
${approach_instruction}

===== RELEVANT CODE =====
${code_context}

===== DATABASE SCHEMA =====
${schema_context}

===== IXM SPEC CONTEXT =====
${spec_context}

===== LOG PATTERNS =====
${log_context}

===== DOCUMENTATION =====
${doc_context}

===== QUESTION =====
${user_question}
```

### Template: explain_flow.txt

```
APPROACH:
Provide a step-by-step explanation of how this process or feature works. For each step,
identify which service handles it, what data is involved, and how it transitions to the
next step. Include database interactions and external system calls where relevant.
```

### Template: trace_request.txt

```
APPROACH:
Trace this request through the system step by step. For each step:
1. Identify which service receives or processes the request
2. Note any data transformations (e.g., IXM XML → internal JSON)
3. Note any database reads or writes
4. Identify the next service in the call chain
5. End with how and where the response is returned

If you can identify log events that would be visible at each step, include them.
```

### Template: debug_error.txt

```
APPROACH:
Help diagnose this error or failure. Using the code, schema, and log patterns provided:
1. Identify the most likely cause of the error
2. Identify which service or component is the failure point
3. Suggest relevant log fields to search in Splunk to confirm the diagnosis
4. If multiple causes are possible, list them in order of likelihood
5. Suggest a fix or investigation steps for each cause
```

### Template: document_endpoint.txt

```
APPROACH:
Write clear technical documentation for this endpoint or API. Include:
1. Purpose and description
2. Request format (method, path, headers, body with all fields and types)
3. Response format (status codes, body schema)
4. Error responses and their meanings
5. IXM spec mapping if applicable (how internal fields map to IXM message fields)
6. Related endpoints or services

Format the output as Markdown suitable for inclusion in a Confluence page.
```

### Template: find_related.txt

```
APPROACH:
Identify all services, tables, and components related to the subject of the question.
For each relationship found:
1. Name the related service or component
2. Describe the nature of the relationship (calls, is called by, reads from, writes to)
3. Note what data flows between them
4. Indicate if the relationship is synchronous (HTTP/gRPC) or asynchronous

Organize the output as a dependency map.
```

### Template: general_question.txt

```
APPROACH:
Answer this question using only the context provided. Be specific and technical.
Reference the exact service names, table names, field names, and code paths from
the context. If the context does not contain the answer, say so clearly rather
than speculating.
```

---

## Context Formatting

Each section of the prompt receives a formatted version of its context chunks.

### Code Context Formatter

```python
def format_code_chunks(chunks: list[SearchResult]) -> str:
    parts = []
    for chunk in chunks:
        meta = chunk.metadata
        header = f"// Service: {chunk.service_name}"
        if meta.get("http_method") and meta.get("http_path"):
            header += f"\n// Handler: {meta['http_method']} {meta['http_path']}"
        parts.append(f"```go\n{header}\n{chunk.content}\n```")
    return "\n\n".join(parts) if parts else "(no relevant code found)"
```

Output example:
```
```go
// Service: enrollment-svc
// Handler: POST /api/v1/enroll

func EnrollHandler(w http.ResponseWriter, r *http.Request) {
    var req EnrollRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        ...
    }
    ...
}
```
```

### Schema Context Formatter

```python
def format_schema_chunks(chunks: list[SearchResult]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(chunk.content)  # Already formatted as table definition
    return "\n\n".join(parts) if parts else "(no relevant schema found)"
```

### Spec Context Formatter

```python
def format_spec_chunks(chunks: list[SearchResult]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(chunk.content)  # Already formatted as message type definition
    return "\n\n".join(parts) if parts else "(no relevant IXM spec context found)"
```

### Log Context Formatter

```python
def format_log_chunks(chunks: list[SearchResult]) -> str:
    parts = []
    for chunk in chunks:
        parts.append(chunk.content)  # Already formatted as log event list
    return "\n\n".join(parts) if parts else "(no relevant log patterns found)"
```

### Documentation Formatter

```python
def format_doc_chunks(chunks: list[SearchResult]) -> str:
    parts = []
    for chunk in chunks:
        title = chunk.metadata.get("page_title", "Unknown page")
        section = chunk.metadata.get("section", "")
        header = f"[From: {title}" + (f" > {section}" if section else "") + "]"
        parts.append(f"{header}\n{chunk.content}")
    return "\n\n".join(parts) if parts else "(no relevant documentation found)"
```

---

## Length Management

The prompt must fit within ChatGPT's context window. Current ChatGPT models support 8K–128K tokens depending on the version, but the practical limit for reliable behavior is approximately 8,000–10,000 tokens for the combined prompt + response.

**Default token budget**: 8,000 tokens for the prompt, leaving 2,000 tokens for the response.

### Token Estimation

```python
def estimate_tokens(text: str) -> int:
    # Simple heuristic: words * 1.3 (accounts for subword tokenization)
    # Good enough for budgeting; use tiktoken for precision if available
    word_count = len(text.split())
    return int(word_count * 1.3)
```

If `tiktoken` is available (bundled with the deployment):
```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-3.5-turbo")
token_count = len(enc.encode(text))
```

### Section Priority for Trimming

When the assembled prompt exceeds the budget, sections are trimmed in this order (lowest priority trimmed first):

1. **Log patterns** (trim first): most likely to be repetitive across services; least likely to contain the specific information needed for the question
2. **Documentation** (trim second): Confluence pages are verbose; the most relevant sentences are usually in the first paragraph of each section, which is already captured in the chunk header
3. **IXM spec** (trim third, unless spec-related): only kept if the query or analysis indicates the spec is relevant
4. **Schema** (trim fourth, unless data-related): kept unless the query clearly doesn't involve the database
5. **Code** (trim last): code snippets are the highest-value context; trim by reducing the number of chunks, keeping the highest-ranked ones

**Trimming within a section**: when trimming a section, reduce the number of chunks (drop the lowest-ranked ones) rather than truncating individual chunk content. A complete chunk is more useful than a partial one.

**Minimum representation**: after trimming, always retain at least:
- 1 code chunk (if any code was retrieved)
- 1 schema chunk (if a schema is relevant)
- The full SYSTEM CONTEXT section (never trimmed)

### Implementation

```python
@dataclass
class BuiltPrompt:
    prompt_text: str
    token_estimate: int
    template_used: str
    sections_included: list[str]    # sections with at least one chunk
    sections_trimmed: list[str]     # sections that were reduced to fit budget

def build(query: str, context: ContextPackage, budget: int = 8000) -> BuiltPrompt:
    template = TEMPLATES[context.query_analysis.intent]
    sections = {
        "code": format_code_chunks(context.code_chunks),
        "schema": format_schema_chunks(context.schema_chunks),
        "spec": format_spec_chunks(context.spec_chunks),
        "log": format_log_chunks(context.log_chunks),
        "doc": format_doc_chunks(context.doc_chunks),
    }

    # Render template
    prompt = template.substitute(
        approach_instruction=APPROACH_TEXT[context.query_analysis.intent],
        code_context=sections["code"],
        schema_context=sections["schema"],
        spec_context=sections["spec"],
        log_context=sections["log"],
        doc_context=sections["doc"],
        user_question=query,
    )

    # Trim if needed
    trimmed = []
    if estimate_tokens(prompt) > budget:
        prompt, trimmed = trim_to_budget(prompt, budget, context, sections)

    return BuiltPrompt(
        prompt_text=prompt,
        token_estimate=estimate_tokens(prompt),
        template_used=context.query_analysis.intent.value,
        sections_included=[k for k, v in sections.items() if "no relevant" not in v],
        sections_trimmed=trimmed,
    )
```

---

## Saved Prompts

Users can save prompts to `prompts/saved/` for reuse and reference.

**File naming**: `YYYYMMDD_HHMMSS_{slug}.txt` where slug is the first 5 words of the query, slugified (lowercase, alphanumeric and hyphens only).

Example: query "how does biometric enrollment work" saved on 2024-03-15 at 14:23:05 → `20240315_142305_how-does-biometric-enrollment-work.txt`

**File format**: the saved file contains the full prompt text, preceded by a metadata header:

```
# Saved Prompt
# Query: how does biometric enrollment work
# Intent: explain_flow
# Template: explain_flow
# Saved: 2024-03-15T14:23:05Z
# Token estimate: 6842
# Sections included: code, schema, spec, doc
# Sections trimmed: log
# ---

===== SYSTEM CONTEXT =====
...
```

Users can maintain a personal library of prompts for recurring questions. The web UI lists saved prompts and allows reloading them.

---

## Prompt Output Formats

### Default: Print to stdout

```
$ python -m prompt_builder.cli "how does enrollment work"

===== SYSTEM CONTEXT =====
You are analyzing a biometric identity and encounter management system.
...

===== RELEVANT CODE =====
...

[Press Ctrl+C to stop, or pipe to a file]
Estimated tokens: 6842
Template used: explain_flow
```

### Clipboard Copy (CLI)

With `--copy` flag: copy the prompt text to the system clipboard using `xclip` (Linux), `pbcopy` (macOS), or `clip.exe` (Windows).

```bash
python -m prompt_builder.cli "how does enrollment work" --copy
# Output: "Prompt copied to clipboard (6842 tokens)"
```

### Save to File

With `--save` flag: write to `prompts/saved/`.

```bash
python -m prompt_builder.cli "how does enrollment work" --save
# Output: "Prompt saved to prompts/saved/20240315_142305_how-does-biometric-enrollment-work.txt"
```

### Web UI

The web UI renders the prompt in a `<pre>` block with:
- Syntax highlighting for code sections (using highlight.js, bundled locally)
- A "Copy Prompt" button (clipboard API)
- A "Save Prompt" button (POST to /prompts/save)
- A "Show Sources" toggle that reveals the original chunk metadata
