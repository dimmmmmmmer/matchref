"""Configuration loading and persistence."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _PACKAGE_ROOT / "config" / "default_config.json"
USER_CONFIG_PATH = _PACKAGE_ROOT / "config" / "user_config.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class AppConfig:
    """Application settings backed by JSON files."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (
            USER_CONFIG_PATH if USER_CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
        )
        self._data: dict[str, Any] = {}
        self.reload()

    @property
    def path(self) -> Path:
        return self._path

    def reload(self) -> None:
        defaults = self._load_json(DEFAULT_CONFIG_PATH)
        if self._path != DEFAULT_CONFIG_PATH and self._path.exists():
            user = self._load_json(self._path)
            self._data = _deep_merge(defaults, user)
        else:
            self._data = defaults

    def save(self, path: Path | None = None) -> None:
        target = path or USER_CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
        self._path = target

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
