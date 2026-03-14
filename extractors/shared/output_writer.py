from __future__ import annotations

import os
from pathlib import Path

from extractors.shared.models import ServiceDoc


class OutputWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.services_dir = output_dir / "services"

    def write_service_doc(self, doc: ServiceDoc) -> Path:
        self.services_dir.mkdir(parents=True, exist_ok=True)
        dest = self.services_dir / f"{doc.name}.json"
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(doc.model_dump_json(indent=2))
        os.rename(tmp, dest)
        return dest
