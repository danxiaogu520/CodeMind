from pathlib import Path

import pytest
from pydantic import ValidationError

from codemind.config import Settings


def test_settings_use_safe_local_defaults() -> None:
    settings = Settings()

    assert settings.api_host == "127.0.0.1"
    assert settings.repository_work_dir == Path("data/repositories")
    assert settings.allowed_repository_roots == []
    assert settings.embedding_provider == "hash"
    assert settings.llm_provider == "template"


def test_settings_reject_invalid_port() -> None:
    with pytest.raises(ValidationError):
        Settings(api_port=70_000)
