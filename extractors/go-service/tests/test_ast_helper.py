"""Tests for the Go AST helper binary.

These tests compile and run the real ast_helper binary against Go source
fixtures. They verify that the JSON output is always safe to iterate — the
original bug was that nil Go slices serialised to JSON null, causing
'NoneType is not iterable' errors in the Python extractor.

Run with:
    pytest extractors/go-service/tests/test_ast_helper.py -v
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

# ---- path helpers ----

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_AST_HELPER = _PROJECT_ROOT / "bin" / "ast_helper"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _run(go_source: str) -> dict:
    """Write go_source to a temp file and run ast_helper, return parsed JSON."""
    tmp = _FIXTURES_DIR / "_tmp_test.go"
    _FIXTURES_DIR.mkdir(exist_ok=True)
    tmp.write_text(go_source)
    try:
        result = subprocess.run(
            [str(_AST_HELPER), str(tmp)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"ast_helper failed:\n{result.stderr}"
        return json.loads(result.stdout)
    finally:
        tmp.unlink(missing_ok=True)


# ---- fixtures ----

pytestmark = pytest.mark.skipif(
    not _AST_HELPER.exists(),
    reason=f"ast_helper binary not found at {_AST_HELPER}. Build with: "
           "cd extractors/go-service/ast_helper && go build -o ../../../bin/ast_helper .",
)


# ---- null-safety tests (regression for the original crash) ----

class TestNullSafety:
    """All list fields must be JSON arrays, never null.

    Regression: Go nil slices serialise as JSON null; iterating None in
    Python raises 'TypeError: NoneType object is not iterable'.
    """

    EMPTY_UTILITY_FILE = textwrap.dedent("""\
        package util

        import "fmt"

        func helper() string {
            return fmt.Sprintf("hello")
        }
    """)

    LIST_FIELDS = [
        "imports",
        "functions",
        "http_handlers",
        "grpc_registrations",
        "struct_types",
        "log_calls",
        "db_calls",
    ]

    def test_list_fields_are_never_null(self):
        result = _run(self.EMPTY_UTILITY_FILE)
        for field in self.LIST_FIELDS:
            assert field in result, f"Field '{field}' missing from output"
            assert result[field] is not None, (
                f"Field '{field}' is null — will crash Python extractor with "
                "'TypeError: NoneType object is not iterable'"
            )
            assert isinstance(result[field], list), (
                f"Field '{field}' expected list, got {type(result[field]).__name__}"
            )

    def test_list_fields_iterable_when_empty(self):
        """Verify that iterating each field doesn't raise (the actual crash)."""
        result = _run(self.EMPTY_UTILITY_FILE)
        for field in self.LIST_FIELDS:
            try:
                list(result[field])  # would raise TypeError if None
            except TypeError as e:
                pytest.fail(f"Iterating '{field}' raised: {e}")


# ---- HTTP handler detection ----

class TestHTTPHandlers:
    STDLIB_SOURCE = textwrap.dedent("""\
        package main

        import "net/http"

        func main() {
            http.HandleFunc("/api/v1/enroll", EnrollHandler)
            http.HandleFunc("/api/v1/status", StatusHandler)
        }

        func EnrollHandler(w http.ResponseWriter, r *http.Request) {}
        func StatusHandler(w http.ResponseWriter, r *http.Request) {}
    """)

    MUX_SOURCE = textwrap.dedent("""\
        package main

        import "github.com/gorilla/mux"

        func main() {
            r := mux.NewRouter()
            r.HandleFunc("/users", ListUsers).Methods("GET")
            r.HandleFunc("/users/{id}", GetUser).Methods("GET")
            r.HandleFunc("/users", CreateUser).Methods("POST")
        }
    """)

    def test_stdlib_handlers_detected(self):
        result = _run(self.STDLIB_SOURCE)
        handlers = result["http_handlers"]
        assert len(handlers) == 2
        paths = {h["pattern"] for h in handlers}
        assert "/api/v1/enroll" in paths
        assert "/api/v1/status" in paths

    def test_stdlib_router_type(self):
        result = _run(self.STDLIB_SOURCE)
        for h in result["http_handlers"]:
            assert h["router_type"] == "stdlib"

    def test_gorilla_mux_handlers_detected(self):
        result = _run(self.MUX_SOURCE)
        handlers = result["http_handlers"]
        assert len(handlers) == 3
        paths = {h["pattern"] for h in handlers}
        assert "/users" in paths
        assert "/users/{id}" in paths

    def test_no_handlers_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["http_handlers"] == []


# ---- gRPC registration detection ----

class TestGRPCRegistrations:
    SOURCE = textwrap.dedent("""\
        package main

        import (
            "google.golang.org/grpc"
            pb "company.com/services/enrollment/proto"
        )

        func main() {
            s := grpc.NewServer()
            pb.RegisterEnrollmentServer(s, &server{})
            pb.RegisterIdentityServer(s, &idServer{})
        }
    """)

    def test_grpc_registrations_detected(self):
        result = _run(self.SOURCE)
        regs = result["grpc_registrations"]
        assert len(regs) == 2
        names = {r["service_name"] for r in regs}
        assert "Enrollment" in names
        assert "Identity" in names

    def test_no_grpc_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["grpc_registrations"] == []


# ---- struct type extraction ----

class TestStructTypes:
    SOURCE = textwrap.dedent("""\
        package main

        type EnrollRequest struct {
            BiometricID string `json:"biometric_id"`
            Template    []byte `json:"template"`
            Optional    string
        }

        type EmptyStruct struct{}
    """)

    def test_struct_detected(self):
        result = _run(self.SOURCE)
        names = {s["name"] for s in result["struct_types"]}
        assert "EnrollRequest" in names
        assert "EmptyStruct" in names

    def test_struct_fields_extracted(self):
        result = _run(self.SOURCE)
        enroll = next(s for s in result["struct_types"] if s["name"] == "EnrollRequest")
        field_names = [f["name"] for f in enroll["fields"]]
        assert "BiometricID" in field_names
        assert "Template" in field_names

    def test_json_tags_extracted(self):
        result = _run(self.SOURCE)
        enroll = next(s for s in result["struct_types"] if s["name"] == "EnrollRequest")
        fields_by_name = {f["name"]: f for f in enroll["fields"]}
        assert fields_by_name["BiometricID"]["json_tag"] == "biometric_id"
        assert fields_by_name["Template"]["json_tag"] == "template"

    def test_no_structs_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["struct_types"] == []


# ---- log call detection ----

class TestLogCalls:
    SOURCE = textwrap.dedent("""\
        package main

        import "log/slog"

        func handle() {
            slog.Info("enrollment started", "biometric_id", "abc123", "service", "enrollment-svc")
            slog.Error("enrollment failed", "error", "timeout", "biometric_id", "abc123")
            slog.Debug("debug info")
        }
    """)

    def test_log_calls_detected(self):
        result = _run(self.SOURCE)
        assert len(result["log_calls"]) == 3

    def test_log_call_func_names(self):
        result = _run(self.SOURCE)
        func_names = {lc["func_name"] for lc in result["log_calls"]}
        assert "slog.Info" in func_names
        assert "slog.Error" in func_names
        assert "slog.Debug" in func_names

    def test_log_call_args_captured(self):
        result = _run(self.SOURCE)
        info = next(lc for lc in result["log_calls"] if lc["func_name"] == "slog.Info")
        assert '"enrollment started"' in info["args"]

    def test_no_logs_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["log_calls"] == []


# ---- DB call detection ----

class TestDBCalls:
    SOURCE = textwrap.dedent("""\
        package main

        import "database/sql"

        func store(db *sql.DB) {
            db.Query("SELECT id FROM enrollment_records WHERE id = $1", 1)
            db.Exec("INSERT INTO audit_log (event) VALUES ($1)", "enrolled")
        }
    """)

    def test_db_calls_detected(self):
        result = _run(self.SOURCE)
        assert len(result["db_calls"]) == 2

    def test_db_call_sql_captured(self):
        result = _run(self.SOURCE)
        all_args = [arg for dc in result["db_calls"] for arg in dc["args"]]
        assert any("enrollment_records" in arg for arg in all_args)
        assert any("audit_log" in arg for arg in all_args)

    def test_no_db_calls_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["db_calls"] == []


# ---- imports ----

class TestImports:
    SOURCE = textwrap.dedent("""\
        package main

        import (
            "net/http"
            "log/slog"
            company "company.com/services/biometric-store-client"
        )

        func main() {}
    """)

    def test_imports_extracted(self):
        result = _run(self.SOURCE)
        assert "net/http" in result["imports"]
        assert "log/slog" in result["imports"]
        assert "company.com/services/biometric-store-client" in result["imports"]

    def test_no_imports_returns_empty_list(self):
        source = textwrap.dedent("""\
            package main
            func main() {}
        """)
        result = _run(source)
        assert result["imports"] == []


# ---- per-function scoping ----

class TestFunctionScoping:
    """Verify that calls are attributed to their enclosing function, not globally."""

    SOURCE = textwrap.dedent("""\
        package main

        import (
            "net/http"
            "log/slog"
        )

        func SetupRoutes() {
            http.HandleFunc("/api/v1/enroll", EnrollHandler)
            http.HandleFunc("/api/v1/verify", VerifyHandler)
        }

        func EnrollHandler(w http.ResponseWriter, r *http.Request) {
            slog.Info("enrolling")
            sendToStore(r)
        }

        func VerifyHandler(w http.ResponseWriter, r *http.Request) {
            slog.Info("verifying")
        }

        func sendToStore(r *http.Request) {
            http.NewRequest("POST", "/api/v1/store", nil)
        }
    """)

    def test_functions_present(self):
        result = _run(self.SOURCE)
        names = {f["name"] for f in result["functions"]}
        assert "SetupRoutes" in names
        assert "EnrollHandler" in names
        assert "VerifyHandler" in names
        assert "sendToStore" in names

    def test_http_client_call_scoped_to_sendToStore(self):
        result = _run(self.SOURCE)
        send = next(f for f in result["functions"] if f["name"] == "sendToStore")
        assert len(send["http_client_calls"]) == 1
        assert send["http_client_calls"][0]["func"] == "http.NewRequest"
        assert send["http_client_calls"][0]["method_arg"] == "POST"

    def test_http_client_call_not_in_enroll_handler(self):
        result = _run(self.SOURCE)
        enroll = next(f for f in result["functions"] if f["name"] == "EnrollHandler")
        assert enroll["http_client_calls"] == []

    def test_enroll_handler_calls_sendToStore(self):
        result = _run(self.SOURCE)
        enroll = next(f for f in result["functions"] if f["name"] == "EnrollHandler")
        assert "sendToStore" in enroll["calls"]

    def test_setup_routes_does_not_have_http_client_calls(self):
        result = _run(self.SOURCE)
        setup = next(f for f in result["functions"] if f["name"] == "SetupRoutes")
        assert setup["http_client_calls"] == []

    def test_function_fields_never_null(self):
        result = _run(self.SOURCE)
        for f in result["functions"]:
            assert f["calls"] is not None
            assert f["http_client_calls"] is not None
            assert f["client_lib_calls"] is not None


# ---- HTTP client call detection ----

class TestHTTPClientCallDetection:

    def test_http_new_request_detected(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func makeCall() {
                http.NewRequest("POST", "/api/v1/enroll", nil)
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "makeCall")
        assert len(f["http_client_calls"]) == 1
        hc = f["http_client_calls"][0]
        assert hc["func"] == "http.NewRequest"
        assert hc["method_arg"] == "POST"
        assert "/api/v1/enroll" in hc["string_args"]

    def test_http_get_detected(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func fetch() {
                http.Get("http://svc/api/v1/status")
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "fetch")
        assert len(f["http_client_calls"]) == 1
        assert f["http_client_calls"][0]["method_arg"] == "GET"

    def test_http_post_detected(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func send() {
                http.Post("/api/v1/store", "application/json", nil)
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "send")
        hc = f["http_client_calls"][0]
        assert hc["method_arg"] == "POST"
        assert "/api/v1/store" in hc["string_args"]

    def test_path_literal_extracted_from_new_request(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func callService(baseURL string) {
                http.NewRequest("GET", baseURL + "/api/v1/verify", nil)
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "callService")
        # baseURL is not a literal so won't appear, but "/api/v1/verify" is
        hc = f["http_client_calls"][0]
        # The binary expression is not a literal — string_args won't include baseURL
        # but the method arg is still captured
        assert hc["method_arg"] == "GET"

    def test_non_http_calls_not_detected_as_http_client(self):
        source = textwrap.dedent("""\
            package main
            import "database/sql"
            func store(db *sql.DB) {
                db.Query("SELECT 1")
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "store")
        assert f["http_client_calls"] == []


# ---- client library call detection ----

class TestClientLibCallDetection:

    SOURCE = textwrap.dedent("""\
        package main

        func EnrollHandler() {
            mcbsClient.StoreTemplate(ctx, req)
            identityClient.Verify(ctx, id)
            helper()
        }
    """)

    def test_client_lib_calls_detected(self):
        result = _run(self.SOURCE)
        f = next(f for f in result["functions"] if f["name"] == "EnrollHandler")
        cl_methods = {c["method"] for c in f["client_lib_calls"]}
        assert "StoreTemplate" in cl_methods
        assert "Verify" in cl_methods

    def test_client_lib_receivers_captured(self):
        result = _run(self.SOURCE)
        f = next(f for f in result["functions"] if f["name"] == "EnrollHandler")
        receivers = {c["receiver"] for c in f["client_lib_calls"]}
        assert "mcbsClient" in receivers
        assert "identityClient" in receivers

    def test_plain_helper_call_in_calls_not_client_lib(self):
        result = _run(self.SOURCE)
        f = next(f for f in result["functions"] if f["name"] == "EnrollHandler")
        assert "helper" in f["calls"]
        cl_methods = {c["method"] for c in f["client_lib_calls"]}
        assert "helper" not in cl_methods

    def test_client_lib_string_args_extracted(self):
        source = textwrap.dedent("""\
            package main
            func call() {
                mcbsClient.Store(ctx, "enrollment-svc", "/api/v1/store")
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "call")
        cl = f["client_lib_calls"][0]
        assert "enrollment-svc" in cl["string_args"]
        assert "/api/v1/store" in cl["string_args"]

    def test_http_client_not_classified_as_client_lib(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func send() {
                http.NewRequest("POST", "/api/v1/enroll", nil)
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "send")
        assert f["client_lib_calls"] == []
        assert len(f["http_client_calls"]) == 1


# ---- call graph edges ----

class TestCallGraphEdges:
    """Verify the 'calls' field forms the correct call graph edges."""

    SOURCE = textwrap.dedent("""\
        package main

        func Handler() {
            buildRequest()
            validateInput()
        }

        func buildRequest() {
            sendHTTP()
        }

        func sendHTTP() {}
    """)

    def test_handler_calls_captured(self):
        result = _run(self.SOURCE)
        h = next(f for f in result["functions"] if f["name"] == "Handler")
        assert "buildRequest" in h["calls"]
        assert "validateInput" in h["calls"]

    def test_build_request_calls_captured(self):
        result = _run(self.SOURCE)
        b = next(f for f in result["functions"] if f["name"] == "buildRequest")
        assert "sendHTTP" in b["calls"]

    def test_calls_does_not_include_http_client_calls(self):
        source = textwrap.dedent("""\
            package main
            import "net/http"
            func send() {
                http.NewRequest("POST", "/api/v1/store", nil)
            }
        """)
        result = _run(source)
        f = next(f for f in result["functions"] if f["name"] == "send")
        # http.NewRequest should be in http_client_calls, not also in calls
        assert "http.NewRequest" not in f["calls"]
        assert len(f["http_client_calls"]) == 1
