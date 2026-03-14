from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


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
    file_hash_map: dict[str, str] = {}
