from __future__ import annotations

from pathlib import Path

import pytest

from codemind.domain.models import Language, SourceType
from codemind.ingestion.discovery import SourceDiscoverer
from codemind.ingestion.sources import RepositorySourceError, SafeRepositorySource

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "repos" / "sample_python"


async def test_discovery_respects_gitignore_and_detects_language() -> None:
    documents = await SourceDiscoverer().discover(FIXTURE_ROOT)

    assert [document.path for document in documents] == ["src/auth.py", "src/tokens.py"]
    assert all(document.language is Language.PYTHON for document in documents)
    assert all(len(document.content_hash) == 64 for document in documents)


async def test_local_source_creates_isolated_snapshot(tmp_path: Path) -> None:
    source = SafeRepositorySource(tmp_path / "work", [FIXTURE_ROOT.parent])

    prepared = await source.prepare("repo-1", SourceType.LOCAL, str(FIXTURE_ROOT), None)

    assert prepared.root != FIXTURE_ROOT
    assert (prepared.root / "src" / "auth.py").is_file()
    assert len(prepared.commit_sha) == 64
    await source.cleanup(prepared)
    assert not prepared.root.exists()


async def test_local_source_rejects_path_outside_allowed_root(tmp_path: Path) -> None:
    source = SafeRepositorySource(tmp_path / "work", [tmp_path / "allowed"])

    with pytest.raises(RepositorySourceError, match="outside allowed roots"):
        await source.prepare("repo-1", SourceType.LOCAL, str(FIXTURE_ROOT), None)


async def test_git_source_rejects_private_address_and_embedded_credentials(
    tmp_path: Path,
) -> None:
    source = SafeRepositorySource(tmp_path / "work", [])

    with pytest.raises(RepositorySourceError, match="non-public"):
        await source.prepare("repo-1", SourceType.GIT, "http://127.0.0.1/repo.git", None)
    with pytest.raises(RepositorySourceError, match="Credentials"):
        await source.prepare(
            "repo-2", SourceType.GIT, "https://user:secret@example.com/repo.git", None
        )


async def test_discovery_does_not_follow_symlinks_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "safe.py").write_text("def safe(): pass\n", encoding="utf-8")
    secret = tmp_path / "secret.py"
    secret.write_text("SECRET = 'do-not-index'\n", encoding="utf-8")
    (root / "linked.py").symlink_to(secret)

    documents = await SourceDiscoverer().discover(root)

    assert [item.path for item in documents] == ["safe.py"]
