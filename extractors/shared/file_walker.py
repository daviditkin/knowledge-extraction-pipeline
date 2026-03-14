from __future__ import annotations

import fnmatch
import hashlib
import json
from pathlib import Path


class FileWalker:
    def __init__(
        self,
        root_dir: str | Path,
        include_patterns: list[str],
        exclude_patterns: list[str],
    ) -> None:
        self.root_dir = Path(root_dir)
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns

    def walk(self) -> list[Path]:
        results: list[Path] = []
        for pattern in self.include_patterns:
            for path in self.root_dir.glob(pattern):
                if path.is_file() and not self._is_excluded(path):
                    results.append(path)
        # Deduplicate while preserving order
        seen: set[Path] = set()
        unique: list[Path] = []
        for p in results:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def changed_files(self, cache_path: Path) -> list[Path]:
        cache: dict[str, str] = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())

        changed: list[Path] = []
        for path in self.walk():
            current_hash = self._sha256(path)
            if cache.get(str(path)) != current_hash:
                changed.append(path)
        return changed

    def update_hash_cache(self, paths: list[Path], cache_path: Path) -> None:
        cache: dict[str, str] = {}
        if cache_path.exists():
            cache = json.loads(cache_path.read_text())
        for path in paths:
            cache[str(path)] = self._sha256(path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2))

    def _is_excluded(self, path: Path) -> bool:
        rel = str(path.relative_to(self.root_dir))
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(rel, pattern):
                return True
        return False

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
