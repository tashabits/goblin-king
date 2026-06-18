from __future__ import annotations

import json

from goblin_king.api_settings import ApiSettings
from goblin_king.notebooks import GoblinKingNotebookClient


def test_api_settings_load_repository_env(tmp_path, monkeypatch) -> None:
    settings_path = tmp_path / "api.json"
    settings_path.write_text(
        json.dumps(
            {
                "repository": {
                    "enabled": False,
                    "url": None,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOBLIN_KING_REPOSITORY_ENABLED", "true")
    monkeypatch.setenv("GOBLIN_KING_REPOSITORY_URL", "http://repository:8000")

    settings = ApiSettings.from_path(settings_path)

    assert settings.repository.enabled is True
    assert settings.repository.url == "http://repository:8000"


def test_notebook_client_reads_repository_url_env(monkeypatch) -> None:
    monkeypatch.setenv("GOBLIN_KING_API_TOKEN", "token")
    monkeypatch.setenv("GOBLIN_KING_REPOSITORY_URL", "http://repository:8000/")

    client = GoblinKingNotebookClient(api_url="http://api:8000")

    assert client.repository_url == "http://repository:8000"
