# Prompt Templates

Plain text templates for the six query intents. Templates use Python's `string.Template` syntax — variables are `$variable_name` or `${variable_name}`.

## Templates

| File | Intent | Trigger phrases |
|---|---|---|
| `explain_flow.txt` | Explain how something works | "how does", "explain", "describe", "what does", "walk me through" |
| `trace_request.txt` | Trace a request through services | "trace", "follow", "request path", "call chain", "lifecycle" |
| `debug_error.txt` | Debug a failure or error | "error", "failing", "exception", "broken", "500", "timeout", "why is" |
| `document_endpoint.txt` | Write documentation | "document", "write docs", "openapi", "swagger", "api spec" |
| `find_related.txt` | Find related services/tables | "related to", "depends on", "calls", "who uses", "what uses" |
| `general_question.txt` | General questions | (default, when no other intent matches) |

## Template Variables

All templates receive these variables from the prompt builder:

| Variable | Content |
|---|---|
| `$code_context` | Formatted Go/Java code snippets |
| `$schema_context` | Formatted table definitions |
| `$spec_context` | Formatted IXM message type definitions |
| `$log_context` | Formatted log event catalog |
| `$doc_context` | Formatted Confluence page excerpts |
| `$user_question` | The user's original question text |

## Adding a New Template

1. Create `new_intent.txt` in this directory
2. Add the intent to `QueryIntent` enum in `rag/query/analyzer.py`
3. Add trigger phrases to the intent detection rules in `QueryAnalyzer.detect_intent()`
4. Add the template file name to `TEMPLATE_MAP` in `prompt_builder/builder.py`

## Example: How `explain_flow.txt` Looks When Rendered

```
===== SYSTEM CONTEXT =====
You are analyzing a biometric identity and encounter management system.

Architecture:
- Approximately 120 Go microservices and several Java Spring Boot services
...

===== APPROACH =====
Provide a step-by-step explanation of how this process or feature works...

===== RELEVANT CODE =====
```go
// Service: enrollment-svc
// Handler: POST /api/v1/enroll

func EnrollHandler(w http.ResponseWriter, r *http.Request) {
    ...
}
```

===== DATABASE SCHEMA =====
Table: biometric_records
...

===== IXM SPEC CONTEXT =====
IXM Message Type: EnrollRequest
...

===== QUESTION =====
how does biometric enrollment work
```
