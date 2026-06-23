"""DaVinci Resolve Scripting API helpers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from matchref.config import AppConfig


def connect_resolve() -> Any:
    """Connect to Resolve; supports in-app and external launcher."""
    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        dvr_script = None

    resolve = None
    if dvr_script is not None:
        resolve = dvr_script.scriptapp("Resolve")

    if resolve is None:
        for module_name in ("DaVinciResolveScript",):
            try:
                import importlib

                module = importlib.import_module(module_name)
                resolve = module.scriptapp("Resolve")
                break
            except Exception:
                continue

    if resolve is None:
        _inject_resolve_module_paths()
        try:
            import DaVinciResolveScript as dvr_script

            resolve = dvr_script.scriptapp("Resolve")
        except Exception as exc:
            raise RuntimeError(
                "Cannot connect to DaVinci Resolve. Run this tool inside Resolve "
                "or set RESOLVE_SCRIPT_API/DAVINCI_RESOLVE_SCRIPT_LIB."
            ) from exc

    if resolve is None:
        raise RuntimeError("DaVinci Resolve is not running or scripting is disabled.")
    return resolve


def _inject_resolve_module_paths() -> None:
    candidates = [
        os.environ.get("RESOLVE_SCRIPT_API", ""),
        os.environ.get("DAVINCI_RESOLVE_SCRIPT_LIB", ""),
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
        "/opt/resolve/Developer/Scripting/Modules",
        "C:/ProgramData/Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting/Modules",
    ]
    for path in candidates:
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


@dataclass
class TimelineMediaInfo:
    fps: float = 0.0
    width: int = 0
    height: int = 0
    drop_frame: bool = False


def _read_setting(container: Any, key: str) -> str | None:
    if container is None:
        return None
    try:
        getter = getattr(container, "GetSetting", None)
        if not callable(getter):
            return None
        value = getter(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    except Exception:
        return None


def get_timeline_media_info(timeline: Any) -> TimelineMediaInfo:
    """Read playback FPS and resolution from the current Resolve timeline."""
    from matchref.fps import normalize_fps

    info = TimelineMediaInfo()
    if timeline is None:
        return info

    fps_keys = (
        "timelineFrameRate",
        "timelinePlaybackFrameRate",
        "frameRate",
        "TimelineFrameRate",
    )
    for source in (timeline, _project_from_timeline(timeline)):
        if info.fps > 0:
            break
        for key in fps_keys:
            raw = _read_setting(source, key)
            if raw:
                try:
                    info.fps = normalize_fps(float(raw))
                    break
                except ValueError:
                    continue

    for key, attr in (
        ("timelineResolutionWidth", "width"),
        ("timelineResolutionHeight", "height"),
    ):
        for source in (timeline, _project_from_timeline(timeline)):
            raw = _read_setting(source, key)
            if raw:
                try:
                    setattr(info, attr, int(float(raw)))
                    break
                except (TypeError, ValueError):
                    continue

    for source in (timeline, _project_from_timeline(timeline)):
        raw = _read_setting(source, "timelineDropFrameTimecode")
        if raw is not None:
            info.drop_frame = raw.lower() in ("1", "true", "yes")
            break

    return info


def _project_from_timeline(timeline: Any) -> Any | None:
    try:
        getter = getattr(timeline, "GetProject", None)
        if callable(getter):
            return getter()
    except Exception:
        pass
    return None


def get_project_context(resolve: Any) -> tuple[Any, Any, Any]:
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    if project is None:
        raise RuntimeError("No project is open.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No timeline is open.")
    return project_manager, project, timeline


def iter_video_timeline_items(timeline: Any) -> list[Any]:
    items: list[Any] = []
    try:
        track_count = int(timeline.GetTrackCount("video"))
    except Exception:
        track_count = 0
    for track_index in range(1, track_count + 1):
        try:
            track_items = timeline.GetItemListInTrack("video", track_index)
        except Exception:
            continue
        if not track_items:
            continue
        if isinstance(track_items, dict):
            for item in track_items.values():
                if item is not None:
                    items.append(item)
        elif isinstance(track_items, list):
            items.extend([i for i in track_items if i is not None])
    return items


def _try_get_selected_items(timeline: Any) -> list[Any]:
    getter = getattr(timeline, "GetSelectedTimelineItems", None)
    if callable(getter):
        try:
            selected = getter()
            if selected:
                if isinstance(selected, dict):
                    return [v for v in selected.values() if v is not None]
                if isinstance(selected, list):
                    return [i for i in selected if i is not None]
        except Exception:
            pass
    return []


def _normalize_color_name(color: str) -> str:
    return str(color).strip().lower()


def _items_with_flag(items: list[Any], color: str) -> list[Any]:
    flagged: list[Any] = []
    target = _normalize_color_name(color)
    for item in items:
        try:
            flags = item.GetFlagList() or []
        except Exception:
            flags = []
        if any(_normalize_color_name(f) == target for f in flags):
            flagged.append(item)
    return flagged


def _items_with_clip_color(items: list[Any], color: str) -> list[Any]:
    """Timeline clip color (Edit page right-click → Clip Color), not flags."""
    colored: list[Any] = []
    target = _normalize_color_name(color)
    for item in items:
        try:
            clip_color = item.GetClipColor() or ""
        except Exception:
            clip_color = ""
        if clip_color and _normalize_color_name(clip_color) == target:
            colored.append(item)
    return colored


def get_target_clips(timeline: Any, config: AppConfig) -> list[Any]:
    """See matchref.selection.get_target_clips_with_source for modes."""
    from matchref.selection import get_target_clips_with_source

    clips, _source = get_target_clips_with_source(timeline, config)
    return clips


def timeline_item_info(item: Any) -> dict[str, Any]:
    name = "Unnamed"
    try:
        name = item.GetName() or name
    except Exception:
        pass
    track_type, track_index = "video", 1
    try:
        track_type, track_index = item.GetTrackTypeAndIndex()
    except Exception:
        pass
    duration = 1
    try:
        duration = int(item.GetDuration())
    except Exception:
        pass
    unique_id = ""
    try:
        unique_id = item.GetUniqueId() or ""
    except Exception:
        pass
    return {
        "name": name,
        "track_type": track_type,
        "track_index": track_index,
        "duration": max(1, duration),
        "unique_id": unique_id,
    }
