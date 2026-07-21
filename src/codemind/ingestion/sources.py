"""Local and Git repository snapshot provider."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from codemind.domain.models import PreparedRepository, SourceType


class RepositorySourceError(ValueError):
    """Raised when a repository source is invalid or unsafe."""


class SafeRepositorySource:
    def __init__(
        self,
        work_dir: Path,
        allowed_local_roots: list[Path],
        *,
        git_timeout_seconds: float = 120,
        git_allowed_hosts: list[str] | None = None,
    ) -> None:
        self._work_dir = work_dir.resolve()
        self._allowed_local_roots = [root.resolve() for root in allowed_local_roots]
        self._git_timeout_seconds = git_timeout_seconds
        self._git_allowed_hosts = {
            host.lower()
            for host in (git_allowed_hosts or ["github.com", "gitlab.com", "bitbucket.org"])
        }

    async def prepare(
        self, repository_id: str, source_type: SourceType, source_uri: str, ref: str | None
    ) -> PreparedRepository:
        destination = self._work_dir / repository_id / uuid4().hex
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_type is SourceType.LOCAL:
            return await self._prepare_local(source_uri, destination)
        if source_type is SourceType.GIT:
            return await self._prepare_git(source_uri, ref, destination)
        raise RepositorySourceError(f"Unsupported source type: {source_type}")

    async def cleanup(self, prepared: PreparedRepository) -> None:
        if prepared.cleanup_required and prepared.root.is_relative_to(self._work_dir):
            await asyncio.to_thread(shutil.rmtree, prepared.root, True)

    async def _prepare_local(self, source_uri: str, destination: Path) -> PreparedRepository:
        return await asyncio.to_thread(self._copy_local, source_uri, destination)

    def _copy_local(self, source_uri: str, destination: Path) -> PreparedRepository:
        source = Path(source_uri).expanduser().resolve()
        if not source.is_dir():
            raise RepositorySourceError("Local repository path is not a directory.")
        if not self._allowed_local_roots:
            raise RepositorySourceError("Local repository imports are disabled.")
        if not any(source.is_relative_to(root) for root in self._allowed_local_roots):
            raise RepositorySourceError("Local repository path is outside allowed roots.")
        if source.is_relative_to(self._work_dir):
            raise RepositorySourceError("Repository work directory cannot be imported recursively.")

        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "target"),
        )
        commit_sha = self._tree_hash(destination)
        return PreparedRepository(destination, commit_sha, cleanup_required=True)

    async def _prepare_git(
        self, source_uri: str, ref: str | None, destination: Path
    ) -> PreparedRepository:
        await asyncio.to_thread(self._validate_git_url, source_uri)
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "clone",
            "--depth",
            "1",
            "--no-tags",
            "--single-branch",
        ]
        if ref:
            command.extend(["--branch", ref])
        command.extend(["--", source_uri, str(destination)])
        environment = {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        }
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
            )
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._git_timeout_seconds
            )
        except TimeoutError as exc:
            await asyncio.to_thread(shutil.rmtree, destination, True)
            raise RepositorySourceError("Git clone timed out.") from exc
        if process.returncode != 0:
            await asyncio.to_thread(shutil.rmtree, destination, True)
            message = stderr.decode("utf-8", errors="replace")[-400:]
            raise RepositorySourceError(f"Git clone failed: {message}")

        commit_sha = await self._git_revision(destination)
        await asyncio.to_thread(shutil.rmtree, destination / ".git", True)
        return PreparedRepository(destination, commit_sha, cleanup_required=True)

    async def _git_revision(self, root: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            "rev-parse",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            raise RepositorySourceError("Unable to resolve cloned commit.")
        return stdout.decode().strip()

    def _validate_git_url(self, source_uri: str) -> None:
        parsed = urlparse(source_uri)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise RepositorySourceError("Only HTTP(S) Git URLs are supported.")
        if parsed.username or parsed.password:
            raise RepositorySourceError("Credentials must not be embedded in Git URLs.")
        try:
            addresses = socket.getaddrinfo(
                parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise RepositorySourceError("Git hostname cannot be resolved.") from exc
        allow_proxy_address = parsed.hostname.lower() in self._git_allowed_hosts
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global and not allow_proxy_address:
                raise RepositorySourceError("Git URL resolves to a non-public address.")

    @staticmethod
    def _tree_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()
