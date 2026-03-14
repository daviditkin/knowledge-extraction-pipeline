from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict


class GoServicesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_dir: str
    include_patterns: list[str] = ["**/*.go"]
    exclude_patterns: list[str] = [
        "**/vendor/**",
        "**/*_test.go",
        "**/testdata/**",
        "**/mock_*.go",
        "**/*.pb.go",
    ]
    internal_module_prefix: str = ""
    ast_helper_binary: str = "./bin/ast_helper"


class Config(BaseModel):
    model_config = ConfigDict(extra="ignore")

    extracted_dir: str = "./extracted"
    go_services: Optional[GoServicesConfig] = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)
