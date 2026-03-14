# Shared Extractor Utilities

Common utilities shared by all extractors. Every extractor imports from this package.

## Modules

### `models.py` — Pydantic data models

All extractor output types defined as pydantic v2 models. These models are the contract between extractors and the indexer.

```python
from extractors.shared.models import (
    ServiceDoc,      # Output of Go and Java extractors
    HandlerInfo,     # One handler (HTTP or gRPC) within a service
    LogEvent,        # One log call site
    SchemaDoc,       # Output of Flyway schema extractor
    TableInfo,       # One table
    ColumnInfo,      # One column
    SpecDoc,         # Output of IXM spec extractor
    SpecField,       # One field in a message type
    DocPage,         # Output of Confluence extractor
    LogPattern,      # Output of log patterns extractor
)
```

All models use:
- `model_config = ConfigDict(extra='forbid')` — catches unexpected fields early
- Descriptions on all fields (for documentation and serialization readability)
- Optional fields typed as `Optional[X]` not `X | None` for Python 3.10 compatibility

### `file_walker.py` — File traversal and change detection

```python
from extractors.shared.file_walker import FileWalker

walker = FileWalker(
    root_dir="/services",
    include_patterns=["**/*.go"],
    exclude_patterns=["**/vendor/**", "**/*_test.go"],
)

# Iterate all matching files
for path in walker.walk():
    print(path)

# Get only changed files since last run
for path in walker.changed_files(cache_path=Path("extracted/.hashes/go.json")):
    print(path)  # only files that have changed
```

**Change detection**: computes SHA-256 of file content, compares against JSON cache. Cache is a dict of `{str(path): hex_hash}`.

**Glob semantics**: uses Python's `pathlib.Path.glob` with `**` for recursive matching. The `exclude_patterns` are checked after include — any path matching any exclude pattern is skipped.

### `output_writer.py` — Atomic output file writing

```python
from extractors.shared.output_writer import OutputWriter

writer = OutputWriter(output_dir=Path("extracted"))

writer.write_service_doc(service_doc)      # → extracted/services/<name>.json
writer.write_schema_doc(schema_doc)        # → extracted/schema.json
writer.write_spec_doc(spec_doc)            # → extracted/ixm-spec/<MessageType>.json
writer.write_doc_page(doc_page)            # → extracted/confluence/<page_id>.json
writer.write_log_patterns(log_patterns)    # → extracted/log-patterns.json
```

All writes are atomic: data is written to a `.tmp` file, then `os.rename()` replaces the target. This prevents corrupt output files if the process is interrupted.

### `config.py` — Configuration loading

```python
from extractors.shared.config import Config

config = Config.from_yaml("config/config.yaml")

# Access config values
print(config.go_services.source_dir)
print(config.database.host)
print(config.embedding.model_path)
```

The `Config` model is a pydantic model that validates the YAML structure at load time. Missing required fields raise `ConfigValidationError` with a clear message.

## Usage in Extractors

All extractors follow this pattern:

```python
from extractors.shared.file_walker import FileWalker
from extractors.shared.output_writer import OutputWriter
from extractors.shared.models import ServiceDoc
from extractors.shared.config import Config

class MyExtractor:
    def __init__(self, config: Config):
        self.walker = FileWalker(
            root_dir=config.my_source.source_dir,
            include_patterns=config.my_source.include_patterns,
            exclude_patterns=config.my_source.exclude_patterns,
        )
        self.writer = OutputWriter(Path(config.extracted_dir))

    def extract_all(self, changed_only: bool = False) -> None:
        hash_cache = Path(config.extracted_dir) / ".hashes" / "my-extractor.json"
        files = self.walker.changed_files(hash_cache) if changed_only else list(self.walker.walk())
        for path in files:
            try:
                doc = self._extract_one(path)
                self.writer.write_service_doc(doc)
            except Exception as e:
                logger.warning("Failed to parse %s: %s", path, e)
```

## Testing

```python
import pytest
from extractors.shared.models import ServiceDoc

def test_service_doc_validation():
    # Valid doc
    doc = ServiceDoc(
        name="test-svc",
        language="go",
        directory="/services/test-svc",
        module_path="company.com/services/test-svc",
        handlers=[],
        external_deps=[],
        db_tables_referenced=[],
        log_events=[],
        file_hash_map={},
    )
    assert doc.name == "test-svc"

def test_service_doc_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ServiceDoc(name="test-svc", unknown_field="value", ...)
```
