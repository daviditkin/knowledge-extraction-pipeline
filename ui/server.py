"""FastAPI UI server for the knowledge extraction pipeline.

Reads extracted service JSON files directly — no database required.

Usage:
    python -m ui.server
    python -m ui.server --extracted-dir ./extracted --port 8080
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent

app = FastAPI(title="Knowledge Extraction Pipeline")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))

# Extracted dir is resolved at startup; default is ./extracted relative to project root
_extracted_dir: Path = _PROJECT_ROOT / "extracted"


def _load_services() -> dict[str, dict]:
    """Load all ServiceDoc JSON files from extracted/services/."""
    services_dir = _extracted_dir / "services"
    if not services_dir.exists():
        return {}
    result: dict[str, dict] = {}
    for f in sorted(services_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            result[data["name"]] = data
        except Exception:
            pass
    return result


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, q: Optional[str] = None):
    services = _load_services()
    if q:
        q_lower = q.lower()
        services = {k: v for k, v in services.items() if q_lower in k.lower()}
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "services": services, "q": q or ""},
    )


@app.get("/service/{name}", response_class=HTMLResponse)
async def service_detail(request: Request, name: str):
    services = _load_services()
    if name not in services:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    svc = services[name]
    # Annotate each dependency: is it also extracted?
    deps = [
        {"name": dep, "known": dep in services}
        for dep in svc.get("external_deps", [])
    ]
    return templates.TemplateResponse(
        "service.html",
        {"request": request, "svc": svc, "deps": deps, "all_services": list(services.keys())},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def main() -> None:
    global _extracted_dir
    parser = argparse.ArgumentParser(description="Knowledge Extraction Pipeline UI")
    parser.add_argument("--extracted-dir", default=str(_extracted_dir))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    _extracted_dir = Path(args.extracted_dir)
    uvicorn.run(
        "ui.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
