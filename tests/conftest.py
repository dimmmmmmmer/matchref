"""Shared test fixtures."""

from __future__ import annotations

import pytest

import matchref.config as config_mod


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Keep tests hermetic: AppConfig() must load defaults, never the developer's
    config/user_config.json (which would otherwise leak real paths/settings and
    make tests behave differently locally vs CI, where no user_config exists).
    """
    monkeypatch.setattr(config_mod, "USER_CONFIG_PATH", tmp_path / "user_config.json")
