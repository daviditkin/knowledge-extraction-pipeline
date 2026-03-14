# Confluence Extractor

Parses Confluence documentation pages and emits `DocPage` JSON for each page. Associates pages with services to enable service-specific documentation retrieval.

## What it extracts

For each Confluence page:

- **Identity**: page ID, title, space key, parent page reference
- **Content**: full page body converted from HTML/Confluence Storage Format to clean Markdown
- **Service associations**: which services the page documents (inferred from title, labels, and content)
- **Metadata**: last modified date, author

## Input Modes

### Mode 1: XML Space Export (recommended for production use)

Export a Confluence space via: **Space Settings → Export → XML**. This produces a ZIP file. Unzip it; the main file is `entities.xml`.

```yaml
confluence:
  input_mode: xml_export
  export_dir: /path/to/unzipped-confluence-export/
```

The extractor looks for `entities.xml` in the specified directory.

### Mode 2: REST API JSON dump (for build phase)

If you have Confluence REST API access (typically only from outside the restricted network), you can dump pages to JSON files first:

```bash
# Dump all pages in a space
curl -u username:api_token \
  "https://confluence.company.com/rest/api/content?spaceKey=DEVDOCS&expand=body.storage,metadata.labels,ancestors&limit=100&start=0" \
  -o confluence_export/page_dump_0.json

# Repeat with start=100, start=200, etc. for large spaces
```

```yaml
confluence:
  input_mode: rest_api_dump
  export_dir: /path/to/json-files/
```

## Confluence Macro Handling

Confluence-specific markup is converted to readable Markdown:

| Confluence element | Converted to |
|---|---|
| `<ac:structured-macro ac:name="info">` | `> **Info:** ...` blockquote |
| `<ac:structured-macro ac:name="warning">` | `> **Warning:** ...` blockquote |
| `<ac:structured-macro ac:name="note">` | `> **Note:** ...` blockquote |
| `<ac:structured-macro ac:name="code">` | ` ```language\n...\n``` ` code fence |
| `<ac:structured-macro ac:name="toc">` | `*(Table of contents omitted)*` |
| `<ac:link>` | Link text (URL stripped — may be internal) |
| `<ac:image>` | `*(image: caption)*` |
| Unknown macros | Body text extracted, wrapper discarded |

## Service Association Logic

Pages are associated with services using a cascade of methods:

1. **Exact title match**: page title exactly matches a service name (case-insensitive)
2. **Label match**: page has a Confluence label that matches a service name
3. **Content frequency**: service name appears 3+ times in the first 500 words
4. **Module path match**: page body contains the internal module path (`company.com/services/xxx`)

The `service_refs` list may contain multiple service names if the page covers multiple services (e.g., an architecture overview page).

## Configuration

```yaml
confluence:
  input_mode: xml_export         # or rest_api_dump
  export_dir: /path/to/export/
  # Service names to look for when associating pages (if not specified, uses extracted service names)
  # Usually this is populated automatically from the Go/Java extractor output
  known_service_names_file: extracted/service_names.txt
```

## Output

One `DocPage` JSON file per page, written to `extracted/confluence/<page-id>.json`.

```json
{
  "page_id": "12345",
  "title": "Enrollment Service",
  "service_refs": ["enrollment-svc"],
  "space_key": "DEVDOCS",
  "content_markdown": "# Enrollment Service\n\n## Overview\n\nThe enrollment service handles...\n\n## API Reference\n\n### POST /api/v1/enroll\n\n...",
  "last_updated": "2024-02-15T10:30:00Z",
  "author": "jsmith"
}
```

## Running

```bash
python scripts/run_extractors.py --config config/config.yaml --extractor confluence
```

## Tips

- **Run this extractor first** (before Go/Java extractors) to populate the known service names list, which is used for service association
- **Or run it last** to use the auto-discovered service names from the Go/Java extractors for better association accuracy
- The XML export is the preferred format — it contains all pages in one file and works completely offline
- Confluence's "last exported" date is in the ZIP filename; note this when comparing doc freshness to code
