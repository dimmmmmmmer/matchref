"""Route a single conform file to the EDL/XML config keys (no GUI dependency)."""

from __future__ import annotations

from pathlib import Path

from matchref.config import AppConfig


def assign_conform_path(config: AppConfig, path: str) -> None:
    """Route a single conform file to the EDL or XML config key by extension."""
    path = str(path).strip()
    config.set("conform_edl_path", "")
    config.set("conform_xml_path", "")
    if not path:
        return
    ext = Path(path).suffix.lower()
    if ext == ".edl":
        config.set("conform_edl_path", path)
    else:
        config.set("conform_xml_path", path)


def conform_path_from_config(config: AppConfig) -> str:
    edl = str(config.get("conform_edl_path", "")).strip()
    xml = str(config.get("conform_xml_path", "")).strip()
    return edl or xml
