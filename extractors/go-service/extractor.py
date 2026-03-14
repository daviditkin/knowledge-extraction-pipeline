from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
import subprocess

try:
    import sqlglot
    import sqlglot.expressions as exp
    _SQLGLOT_AVAILABLE = True
except ImportError:
    _SQLGLOT_AVAILABLE = False

from extractors.shared.config import Config
from extractors.shared.file_walker import FileWalker
from extractors.shared.models import (
    ClientFunction,
    HandlerInfo,
    LogEvent,
    OutboundCall,
    ServiceDoc,
)
from extractors.shared.output_writer import OutputWriter

logger = logging.getLogger(__name__)


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
                    "  \u2713 %s \u2014 %d handlers, %d deps, %d tables, %d log events, %d client funcs",
                    name, len(doc.handlers), len(doc.external_deps),
                    len(doc.db_tables_referenced), len(doc.log_events),
                    len(doc.client_functions),
                )
            except Exception:
                logger.exception("  \u2717 %s \u2014 extraction failed", name)

        # Second pass: resolve client-library calls across all services
        self.resolve_client_lib_calls(results, self.writer)
        return results

    # ---- service discovery ----

    def _discover_services(self) -> dict[str, Path]:
        source = Path(self.cfg.source_dir)
        services: dict[str, Path] = {}

        for go_mod in source.rglob("go.mod"):
            if "vendor" in go_mod.parts:
                continue
            name = self._service_name_from_mod(go_mod)
            services[name] = go_mod.parent

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

        go_files = walker.changed_files(cache_path) if changed_only else walker.walk()
        module_path = self._read_module_path(service_dir)

        handlers: list[HandlerInfo] = []
        log_events: list[LogEvent] = []
        db_queries: list[str] = []
        all_imports: list[str] = []
        file_hash_map: dict[str, str] = {}

        # func name → raw FunctionInfo dict from ast_helper; used for call graph
        func_index: dict[str, dict] = {}

        for go_file in go_files:
            try:
                ast_result = self._run_ast_helper(go_file)
            except Exception as e:
                logger.warning("ast_helper failed on %s: %s", go_file, e)
                continue

            rel = str(go_file.relative_to(service_dir))
            file_hash_map[rel] = walker._sha256(go_file)
            all_imports.extend(ast_result.get("imports") or [])

            # Build function index for call graph resolution
            for fi in ast_result.get("functions") or []:
                if fi.get("name"):
                    func_index[fi["name"]] = fi

            # HTTP handlers
            for h in ast_result.get("http_handlers") or []:
                handlers.append(HandlerInfo(
                    name=h.get("handler_func", ""),
                    http_method=h.get("method"),
                    http_path=h.get("pattern"),
                    file=rel,
                    line_start=h.get("registration_line", 0),
                    line_end=h.get("registration_line", 0),
                ))

            # gRPC registrations
            for g in ast_result.get("grpc_registrations") or []:
                handlers.append(HandlerInfo(
                    name=g.get("service_name", ""),
                    grpc_service=g.get("service_name"),
                    file=rel,
                    line_start=g.get("registration_line", 0),
                    line_end=g.get("registration_line", 0),
                ))

            # Log calls
            for lc in ast_result.get("log_calls") or []:
                level, message, fields = self._parse_log_call(lc)
                log_events.append(LogEvent(
                    level=level,
                    message_template=message,
                    fields=fields,
                    file=rel,
                    line=lc.get("line", 0),
                ))

            # DB calls
            for dc in ast_result.get("db_calls") or []:
                args = dc.get("args") or []
                if args:
                    sql = self._strip_quotes(args[0])
                    if sql:
                        db_queries.append(sql)

        # Resolve outbound calls per handler using the call graph
        for handler in handlers:
            handler.outbound_calls = self._resolve_outbound_calls(
                handler.name, func_index, handler.file
            )

        # Build client_functions for library services (e.g. mcbs)
        client_functions = self._build_client_functions(func_index)

        unique_queries = list(dict.fromkeys(db_queries))
        db_tables = self._extract_tables(unique_queries)
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
            client_functions=client_functions,
            file_hash_map=file_hash_map,
        )

    # ---- call graph resolution ----

    def _resolve_outbound_calls(
        self,
        handler_func: str,
        func_index: dict[str, dict],
        handler_file: str,
        max_depth: int = 5,
    ) -> list[OutboundCall]:
        """BFS from handler_func through the intra-service call graph,
        collecting all reachable HTTP client calls and client lib calls.

        Each queue entry carries site_strings — the string literals that were
        passed at the call site leading to this function. These supplement the
        function's own http_client_call string_args, handling Pattern 2 where
        the URL is built at the call site and passed as a parameter."""
        visited: set[str] = set()
        # (func_name, via, site_strings)
        queue: deque[tuple[str, list[str], list[str]]] = deque([(handler_func, [], [])])
        result: list[OutboundCall] = []

        while queue:
            func_name, via, site_strings = queue.popleft()
            if func_name in visited or len(via) > max_depth:
                continue
            visited.add(func_name)
            func = func_index.get(func_name)
            if func is None:
                continue

            for hc in func.get("http_client_calls") or []:
                # Merge the call's own string_args with strings from the call site.
                # This resolves Pattern 2: the URL was passed in from the caller.
                string_args = (hc.get("string_args") or []) + site_strings
                path = self._extract_path_literal(string_args)
                result.append(OutboundCall(
                    call_type="http",
                    via_functions=list(via),
                    http_method=hc.get("method_arg") or None,
                    path_literal=path,
                    resolved=path is not None,
                    line=hc.get("line", 0),
                    file=handler_file,
                ))

            for cl in func.get("client_lib_calls") or []:
                result.append(OutboundCall(
                    call_type="client_lib",
                    via_functions=list(via),
                    receiver=cl.get("receiver"),
                    method=cl.get("method"),
                    resolved=False,
                    line=cl.get("line", 0),
                    file=handler_file,
                ))

            # Follow intra-service call edges, carrying the call-site string args.
            for edge in func.get("call_edges") or []:
                callee = edge.get("callee", "") if isinstance(edge, dict) else edge
                edge_strings = (edge.get("string_args") or []) if isinstance(edge, dict) else []
                simple = callee.split(".")[-1] if "." in callee else callee
                queue.append((simple, via + [func_name], edge_strings))

        return result

    def _build_client_functions(
        self, func_index: dict[str, dict]
    ) -> list[ClientFunction]:
        """For each exported function that has reachable HTTP client calls,
        emit a ClientFunction entry. Used by other services to resolve their
        client_lib_calls in the second pass."""
        result: list[ClientFunction] = []
        for func_name in func_index:
            if not func_name or not func_name[0].isupper():
                continue  # skip unexported functions

            # Mini BFS to collect reachable http_client_calls with call-site strings.
            visited: set[str] = set()
            q: deque[tuple[str, int, list[str]]] = deque([(func_name, 0, [])])
            # (call dict, site_strings from caller)
            http_calls: list[tuple[dict, list[str]]] = []
            while q:
                name, depth, site_strings = q.popleft()
                if name in visited or depth > 3:
                    continue
                visited.add(name)
                f = func_index.get(name)
                if f is None:
                    continue
                for hc in f.get("http_client_calls") or []:
                    http_calls.append((hc, site_strings))
                for edge in f.get("call_edges") or []:
                    callee = edge.get("callee", "") if isinstance(edge, dict) else edge
                    edge_strings = (edge.get("string_args") or []) if isinstance(edge, dict) else []
                    simple = callee.split(".")[-1] if "." in callee else callee
                    q.append((simple, depth + 1, edge_strings))

            if not http_calls:
                continue

            all_string_args = [
                arg
                for hc, site_strings in http_calls
                for arg in (hc.get("string_args") or []) + site_strings
            ]
            path = self._extract_path_literal(all_string_args)
            method = next(
                (hc.get("method_arg") for hc, _ in http_calls if hc.get("method_arg")),
                None,
            )
            result.append(ClientFunction(
                name=func_name,
                http_method=method,
                path_literal=path,
                string_args=all_string_args,
            ))

        return result

    @staticmethod
    def resolve_client_lib_calls(
        docs: list[ServiceDoc],
        writer: OutputWriter,
    ) -> None:
        """Second pass: resolve client_lib OutboundCalls against all services'
        client_functions. Re-writes any ServiceDoc that gains new resolutions."""
        # method_name → (service_name, ClientFunction); first occurrence wins
        method_map: dict[str, tuple[str, ClientFunction]] = {}
        for doc in docs:
            for cf in doc.client_functions:
                if cf.name not in method_map:
                    method_map[cf.name] = (doc.name, cf)

        if not method_map:
            return

        for doc in docs:
            changed = False
            for handler in doc.handlers:
                for call in handler.outbound_calls:
                    if call.call_type == "client_lib" and not call.resolved and call.method:
                        if call.method in method_map:
                            svc_name, cf = method_map[call.method]
                            call.target_service = svc_name
                            call.target_path = cf.path_literal
                            call.resolved = True
                            changed = True
            if changed:
                writer.write_service_doc(doc)
                logger.debug("Re-wrote %s after client-lib resolution", doc.name)

    # ---- helpers ----

    @staticmethod
    def _extract_path_literal(string_args: list[str]) -> str | None:
        """Return the first string arg that looks like a URL or path.

        Accepts both path-only strings ("/api/v1/enroll") and full URLs
        ("http://svc/api/v1/enroll" or fmt.Sprintf format strings like
        "http://svc/%s/enroll"). Full URLs are returned as-is so callers
        can see the target service name in the host portion."""
        for arg in string_args:
            if arg.startswith("/"):
                return arg
            if "://" in arg:
                return arg
        return None

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

        level = "INFO"
        for part in ("Error", "Warn", "Warning", "Debug", "Fatal"):
            if part.lower() in func_name.lower():
                level = part.upper()
                break

        message = ""
        fields: list[str] = []
        if args:
            message = self._strip_quotes(args[0])
            i = 1
            while i < len(args):
                key = self._strip_quotes(args[i])
                if key and not key.startswith("<"):
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
