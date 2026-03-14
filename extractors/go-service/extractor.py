from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

try:
    import sqlglot
    import sqlglot.expressions as exp
    _SQLGLOT_AVAILABLE = True
except ImportError:
    _SQLGLOT_AVAILABLE = False

from extractors.shared.config import Config
from extractors.shared.file_walker import FileWalker
from extractors.shared.models import HandlerInfo, LogEvent, ServiceDoc
from extractors.shared.output_writer import OutputWriter

logger = logging.getLogger(__name__)

# Log method names that indicate a structured log call
_LOG_METHODS = {"Info", "Warn", "Warning", "Error", "Debug", "Fatal", "AddEvent"}

# DB query methods whose first string arg is SQL
_DB_METHODS = {
    "Query", "QueryRow", "QueryContext", "QueryRowContext",
    "Exec", "ExecContext", "Get", "Select", "NamedQuery", "NamedExec",
}


class GoServiceExtractor:
    def __init__(self, config: Config) -> None:
        if config.go_services is None:
            raise ValueError("go_services config section is required")
        self.cfg = config.go_services
        self.extracted_dir = Path(config.extracted_dir)
        self.writer = OutputWriter(self.extracted_dir)
        self.ast_helper = Path(self.cfg.ast_helper_binary)

    def extract_all(
        self,
        changed_only: bool = False,
        only_service: str | None = None,
    ) -> list[ServiceDoc]:
        if not self.ast_helper.exists():
            raise FileNotFoundError(
                f"ast_helper binary not found at {self.ast_helper}. "
                "Build it with: cd extractors/go-service/ast_helper && go build -o ../../../bin/ast_helper ."
            )

        services = self._discover_services()
        if only_service:
            services = {k: v for k, v in services.items() if k == only_service}
            if not services:
                logger.warning("Service %r not found in %s", only_service, self.cfg.source_dir)

        results: list[ServiceDoc] = []
        for name, service_dir in sorted(services.items()):
            logger.info("Extracting %s ...", name)
            try:
                doc = self._extract_service(name, service_dir, changed_only)
                self.writer.write_service_doc(doc)
                results.append(doc)
                logger.info(
                    "  ✓ %s — %d handlers, %d deps, %d tables, %d log events",
                    name, len(doc.handlers), len(doc.external_deps),
                    len(doc.db_tables_referenced), len(doc.log_events),
                )
            except Exception:
                logger.exception("  ✗ %s — extraction failed", name)

        return results

    # ---- service discovery ----

    def _discover_services(self) -> dict[str, Path]:
        source = Path(self.cfg.source_dir)
        services: dict[str, Path] = {}

        # Walk looking for go.mod files first (module roots = one service per module)
        for go_mod in source.rglob("go.mod"):
            service_dir = go_mod.parent
            # Skip if inside vendor/
            if "vendor" in go_mod.parts:
                continue
            name = self._service_name_from_mod(go_mod)
            services[name] = service_dir

        # Fallback: directories with main.go but no go.mod ancestor already captured
        if not services:
            for main_go in source.rglob("main.go"):
                if "vendor" in main_go.parts:
                    continue
                service_dir = main_go.parent
                name = service_dir.name
                if name not in services:
                    services[name] = service_dir

        return services

    @staticmethod
    def _service_name_from_mod(go_mod: Path) -> str:
        try:
            for line in go_mod.read_text().splitlines():
                line = line.strip()
                if line.startswith("module "):
                    module_path = line.split()[1]
                    return module_path.rstrip("/").split("/")[-1]
        except Exception:
            pass
        return go_mod.parent.name

    # ---- per-service extraction ----

    def _extract_service(
        self, name: str, service_dir: Path, changed_only: bool
    ) -> ServiceDoc:
        walker = FileWalker(
            root_dir=service_dir,
            include_patterns=self.cfg.include_patterns,
            exclude_patterns=self.cfg.exclude_patterns,
        )
        cache_path = self.extracted_dir / ".hashes" / f"{name}.json"

        if changed_only:
            go_files = walker.changed_files(cache_path)
        else:
            go_files = walker.walk()

        # Read module path from go.mod
        module_path = self._read_module_path(service_dir)

        handlers: list[HandlerInfo] = []
        log_events: list[LogEvent] = []
        db_queries: list[str] = []
        all_imports: list[str] = []
        file_hash_map: dict[str, str] = {}

        for go_file in go_files:
            try:
                ast_result = self._run_ast_helper(go_file)
            except Exception as e:
                logger.warning("ast_helper failed on %s: %s", go_file, e)
                continue

            rel = str(go_file.relative_to(service_dir))
            file_hash_map[rel] = walker._sha256(go_file)
            all_imports.extend(ast_result.get("imports", []))

            # HTTP handlers
            for h in ast_result.get("http_handlers", []):
                handlers.append(HandlerInfo(
                    name=h.get("handler_func", ""),
                    http_method=h.get("method"),
                    http_path=h.get("pattern"),
                    grpc_service=None,
                    grpc_method=None,
                    request_type=None,
                    response_type=None,
                    calls_services=[],
                    db_queries=[],
                    file=rel,
                    line_start=h.get("registration_line", 0),
                    line_end=h.get("registration_line", 0),
                ))

            # gRPC registrations
            for g in ast_result.get("grpc_registrations", []):
                handlers.append(HandlerInfo(
                    name=g.get("service_name", ""),
                    http_method=None,
                    http_path=None,
                    grpc_service=g.get("service_name"),
                    grpc_method=None,
                    request_type=None,
                    response_type=None,
                    calls_services=[],
                    db_queries=[],
                    file=rel,
                    line_start=g.get("registration_line", 0),
                    line_end=g.get("registration_line", 0),
                ))

            # Log calls
            for lc in ast_result.get("log_calls", []):
                level, message, fields = self._parse_log_call(lc)
                log_events.append(LogEvent(
                    level=level,
                    message_template=message,
                    fields=fields,
                    file=rel,
                    line=lc.get("line", 0),
                ))

            # DB calls
            for dc in ast_result.get("db_calls", []):
                args = dc.get("args", [])
                if args:
                    sql = self._strip_quotes(args[0])
                    if sql:
                        db_queries.append(sql)

        # Deduplicate DB queries and extract table names
        unique_queries = list(dict.fromkeys(db_queries))
        db_tables = self._extract_tables(unique_queries)

        # External service dependencies from imports
        external_deps = self._extract_deps(all_imports)

        walker.update_hash_cache(go_files, cache_path)

        return ServiceDoc(
            name=name,
            language="go",
            directory=str(service_dir),
            module_path=module_path,
            handlers=handlers,
            external_deps=sorted(set(external_deps)),
            db_tables_referenced=sorted(set(db_tables)),
            log_events=log_events,
            file_hash_map=file_hash_map,
        )

    # ---- helpers ----

    def _run_ast_helper(self, go_file: Path) -> dict:
        result = subprocess.run(
            [str(self.ast_helper), str(go_file)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout)

    def _read_module_path(self, service_dir: Path) -> str:
        go_mod = service_dir / "go.mod"
        if go_mod.exists():
            for line in go_mod.read_text().splitlines():
                line = line.strip()
                if line.startswith("module "):
                    return line.split()[1]
        return service_dir.name

    def _parse_log_call(self, lc: dict) -> tuple[str, str, list[str]]:
        func_name: str = lc.get("func_name", "")
        args: list[str] = lc.get("args", [])

        # Determine level from method name
        level = "INFO"
        for part in ("Error", "Warn", "Warning", "Debug", "Fatal"):
            if part.lower() in func_name.lower():
                level = part.upper()
                break

        message = ""
        fields: list[str] = []

        if args:
            message = self._strip_quotes(args[0])
            # slog-style: "msg", "key1", val1, "key2", val2
            # String literals in positions 1, 3, 5 ... are field names
            i = 1
            while i < len(args):
                key = self._strip_quotes(args[i])
                if key and key.startswith('"') is False and not key.startswith("<"):
                    fields.append(key)
                i += 2

        return level, message, fields

    @staticmethod
    def _strip_quotes(s: str) -> str:
        if (s.startswith('"') and s.endswith('"')) or \
           (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        return s

    def _extract_deps(self, imports: list[str]) -> list[str]:
        prefix = self.cfg.internal_module_prefix
        if not prefix:
            return []
        deps: list[str] = []
        for imp in imports:
            if imp.startswith(prefix):
                remainder = imp[len(prefix):]
                # e.g. "enrollment-svc/client" → "enrollment-svc"
                service_name = remainder.split("/")[0]
                if service_name:
                    deps.append(service_name)
        return deps

    def _extract_tables(self, queries: list[str]) -> list[str]:
        if not _SQLGLOT_AVAILABLE:
            return []
        tables: list[str] = []
        for sql in queries:
            if not sql or sql == "<dynamic SQL>":
                continue
            try:
                parsed = sqlglot.parse_one(sql, dialect="postgres", error_level=None)
                if parsed:
                    for table in parsed.find_all(exp.Table):
                        if table.name:
                            tables.append(table.name)
            except Exception:
                logger.debug("Could not parse SQL: %.80s", sql)
        return tables
