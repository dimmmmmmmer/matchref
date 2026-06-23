"""Conform-path routing helpers (pure; no Qt dependency)."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.conform_paths import assign_conform_path, conform_path_from_config


def test_edl_routes_to_edl_key() -> None:
    cfg = AppConfig()
    assign_conform_path(cfg, "/x/seq.EDL")
    assert cfg.get("conform_edl_path") == "/x/seq.EDL"
    assert cfg.get("conform_xml_path") == ""


def test_xml_routes_to_xml_key() -> None:
    cfg = AppConfig()
    assign_conform_path(cfg, "/x/seq.fcpxml")
    assert cfg.get("conform_xml_path") == "/x/seq.fcpxml"
    assert cfg.get("conform_edl_path") == ""


def test_empty_clears_both() -> None:
    cfg = AppConfig()
    cfg.set("conform_edl_path", "/old.edl")
    assign_conform_path(cfg, "   ")
    assert cfg.get("conform_edl_path") == ""
    assert cfg.get("conform_xml_path") == ""


def test_roundtrip_reads_back_assigned_path() -> None:
    cfg = AppConfig()
    assign_conform_path(cfg, "/x/seq.edl")
    assert conform_path_from_config(cfg) == "/x/seq.edl"
