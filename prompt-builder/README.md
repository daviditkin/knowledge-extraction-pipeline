# Prompt Builder

Assembles a user question + RAG context package into a formatted, copy-pasteable prompt for ChatGPT.

## Overview

The prompt builder is the last step before the human takes over. It:
1. Selects the right template based on the query's detected intent
2. Formats each context section (code, schema, spec, docs, logs) into readable text
3. Injects the formatted sections into the template
4. Trims to fit the token budget (default: 8,000 tokens)
5. Returns the complete prompt as a string ready to copy into ChatGPT

## Usage

### From Python

```python
from prompt_builder.builder import PromptBuilder
from rag.query.engine import QueryEngine

engine = QueryEngine(db=db, embedder=embedder, config=config)
builder = PromptBuilder(templates_dir=Path("prompt-builder/templates"), config=config)

# Run query
context = engine.query("how does biometric enrollment work")

# Build prompt
prompt = builder.build(query="how does biometric enrollment work", context=context)

print(prompt.prompt_text)           # The complete prompt, ready to paste into ChatGPT
print(f"~{prompt.token_estimate} tokens")
print(f"Template: {prompt.template_used}")
print(f"Trimmed: {prompt.sections_trimmed}")
```

### From CLI

```bash
# Print prompt to stdout
python -m prompt_builder.cli "how does enrollment work"

# Copy to clipboard
python -m prompt_builder.cli "how does enrollment work" --copy

# Save to file
python -m prompt_builder.cli "how does enrollment work" --save

# Force a specific template
python -m prompt_builder.cli "enrollment endpoint" --intent document_endpoint

# Override token budget
python -m prompt_builder.cli "trace enrollment request" --budget 10000
```

## Prompt Structure

Every prompt has the same overall structure:

```
===== SYSTEM CONTEXT =====
[Description of the biometric identity system]
[Instruction to use provided context only]

===== APPROACH =====
[Task-specific instructions for ChatGPT]

===== RELEVANT CODE =====
[Go/Java handler snippets]

===== DATABASE SCHEMA =====
[Table definitions]

===== IXM SPEC CONTEXT =====
[Message type definitions]

===== LOG PATTERNS =====
[Log event catalog for relevant services]

===== DOCUMENTATION =====
[Confluence page excerpts]

===== QUESTION =====
[The user's original question]
```

## Templates

| Template | Intent | Best for |
|---|---|---|
| `explain_flow.txt` | `explain_flow` | "how does X work", "explain the Y process" |
| `trace_request.txt` | `trace_request` | "trace a request", "follow this through the system" |
| `debug_error.txt` | `debug_error` | "why is X failing", "debug this error" |
| `document_endpoint.txt` | `document_endpoint` | "write docs for", "OpenAPI spec for" |
| `find_related.txt` | `find_related` | "what calls X", "what does Y depend on" |
| `general_question.txt` | `general_question` | everything else |

## Token Budget Management

If the assembled prompt exceeds the budget, sections are trimmed in this priority order (lowest priority first):

1. Log patterns
2. Documentation
3. IXM spec context (unless the query is spec-related)
4. Schema (unless the query is data-related)
5. Code snippets (trimmed last — highest value)

Trimming removes the lowest-ranked chunks from each section, not characters from individual chunks.

Full design: [`docs/design/prompt-builder.md`](../docs/design/prompt-builder.md)
