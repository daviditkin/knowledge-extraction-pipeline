#!/usr/bin/env python3
"""Run the Go service extractor.

Usage:
    python scripts/extract_go.py --config config/config.yaml
    python scripts/extract_go.py --config config/config.yaml --source-dir /path/to/services
    python scripts/extract_go.py --config config/config.yaml --changed-only
    python scripts/extract_go.py --config config/config.yaml --service enrollment-svc
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from extractors.shared.config import Config

# go-service directory has a hyphen so we load it by path
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "go_service_extractor",
    Path(__file__).parent.parent / "extractors" / "go-service" / "extractor.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
GoServiceExtractor = _mod.GoServiceExtractor


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract knowledge from Go services")
    parser.add_argument("--config", required=True, help="Path to config.yaml")
    parser.add_argument("--source-dir", help="Override go_services.source_dir from config")
    parser.add_argument("--changed-only", action="store_true", help="Only re-extract changed files")
    parser.add_argument("--service", help="Extract a single service by name (for debugging)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )

    config = Config.from_yaml(args.config)

    if args.source_dir:
        if config.go_services is None:
            parser.error("go_services section missing from config")
        config.go_services.source_dir = args.source_dir

    if config.go_services is None:
        parser.error("go_services section missing from config — add it or use --source-dir")

    extractor = GoServiceExtractor(config)
    docs = extractor.extract_all(changed_only=args.changed_only, only_service=args.service)

    print(f"\nExtraction complete: {len(docs)} services written to {config.extracted_dir}/services/")


if __name__ == "__main__":
    main()
