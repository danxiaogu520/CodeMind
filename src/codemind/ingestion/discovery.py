"""Language-aware, ignore-aware source file discovery."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path

from pathspec import PathSpec

from codemind.domain.models import Language, SourceDocument

LANGUAGE_EXTENSIONS: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".rs": Language.RUST,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
}

DEFAULT_IGNORES = (
    ".git/",
    ".venv/",
    "venv/",
    "node_modules/",
    "target/",
    "dist/",
    "build/",
    "coverage/",
    "__pycache__/",
    "*.min.js",
    "*.map",
    "*.lock",
)

SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
)


class SourceDiscoverer:
    def __init__(self, *, max_file_bytes: int = 1_000_000) -> None:
        self._max_file_bytes = max_file_bytes

    async def discover(self, root: Path) -> list[SourceDocument]:
        return await asyncio.to_thread(self._discover_sync, root)

    def _discover_sync(self, root: Path) -> list[SourceDocument]:
        root = root.resolve()
        patterns: list[str] = list(DEFAULT_IGNORES)
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            patterns.extend(gitignore.read_text(encoding="utf-8", errors="ignore").splitlines())
        spec = PathSpec.from_lines("gitignore", patterns)
        documents: list[SourceDocument] = []

        for current, directory_names, file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            directory_names[:] = [
                name
                for name in directory_names
                if not (current_path / name).is_symlink()
                and not spec.match_file(
                    (current_path / name).relative_to(root).as_posix().rstrip("/") + "/"
                )
            ]
            for file_name in sorted(file_names):
                path = current_path / file_name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink() or spec.match_file(relative):
                    continue
                language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
                if language is None:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size == 0 or size > self._max_file_bytes:
                    continue
                content = path.read_bytes()
                if b"\x00" in content[:8192] or any(
                    pattern.search(content) for pattern in SECRET_PATTERNS
                ):
                    continue
                documents.append(
                    SourceDocument(
                        path=relative,
                        language=language,
                        content=content,
                        content_hash=hashlib.sha256(content).hexdigest(),
                    )
                )
        return sorted(documents, key=lambda document: document.path)
