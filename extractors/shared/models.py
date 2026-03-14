from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class OutboundCall(BaseModel):
    """One outbound HTTP call made (directly or transitively) from a handler."""
    model_config = ConfigDict(extra="forbid")

    call_type: Literal["http", "client_lib"]
    via_functions: list[str] = []         # call chain from handler to this call
    http_method: Optional[str] = None     # "GET", "POST" if statically known
    path_literal: Optional[str] = None   # "/api/v1/enroll" if a string arg starts with /
    target_service: Optional[str] = None  # filled by second-pass resolution
    target_path: Optional[str] = None     # filled by second-pass resolution
    receiver: Optional[str] = None        # client_lib: the variable name (e.g. "mcbsClient")
    method: Optional[str] = None          # client_lib: the method name (e.g. "StoreTemplate")
    resolved: bool = False
    line: int = 0
    file: str = ""


class ClientFunction(BaseModel):
    """Exported function from a client library (e.g. mcbs) that wraps HTTP calls.
    Populated for library services so callers can resolve their client_lib calls."""
    model_config = ConfigDict(extra="forbid")

    name: str                             # exported function name, e.g. "StoreTemplate"
    http_method: Optional[str] = None
    path_literal: Optional[str] = None   # best-effort path, e.g. "/api/v1/store"
    string_args: list[str] = []          # all string literals found in the HTTP calls


class HandlerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    http_method: Optional[str] = None
    http_path: Optional[str] = None
    grpc_service: Optional[str] = None
    grpc_method: Optional[str] = None
    request_type: Optional[str] = None
    response_type: Optional[str] = None
    calls_services: list[str] = []
    db_queries: list[str] = []
    outbound_calls: list[OutboundCall] = []
    file: str
    line_start: int
    line_end: int


class LogEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str
    message_template: str
    fields: list[str] = []
    file: str
    line: int


class ServiceDoc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    language: Literal["go", "java"]
    directory: str
    module_path: str
    handlers: list[HandlerInfo] = []
    external_deps: list[str] = []
    db_tables_referenced: list[str] = []
    log_events: list[LogEvent] = []
    client_functions: list[ClientFunction] = []
    file_hash_map: dict[str, str] = {}
