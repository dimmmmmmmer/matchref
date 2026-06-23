# MatchRef — architecture

Module map of the `matchref/` package. The pipeline is: pick clips → map each
sample frame to the offline reference → align (ECC/features) → refine to Edit
parameters → decide → write to the Edit Inspector.

```
matchref/
  config.py             — load/save JSON config (defaults + user overrides)
  models.py             — result dataclasses (sample / clip / report)
  pipeline.py           — analyze→apply orchestration with status callbacks
  gui.py                — PySide6 GUI (analysis runs on a background QThread)

  resolve_api.py        — connect to Resolve, read fps/resolution, list items
  timeline_context.py   — the Resolve timeline as the hub (fps, raster size)
  selection.py          — clip selection (selected / clip color / flags / all)
  clip_filter.py        — filter target clips and skip the lock cut
  clip_metadata.py      — reel / source frame from Resolve

  timecode.py           — SMPTE ↔ frames (drop / non-drop)
  timebase.py           — single hub timebase; conform origin → hub
  fps.py / fps_check.py — fps normalization and validation vs the timeline
  conform_edl.py        — CMX 3600 EDL parsing
  conform_xml.py        — FCPXML / Premiere XMEML parsing
  conform_index.py      — locate the offline frame by reel + source TC
  lock_cut_align.py     — auto-detect the lock-cut origin on the hub

  media_probe.py        — file fps/metadata via OpenCV
  frame_read.py         — read a frame by index (msec / frames / refine seek)
  frame_provider.py     — online/offline frame access + VideoCapture LRU cache

  alignment.py          — ECC / feature matching, affine decomposition
  precision_align.py    — pyramid ECC, phase correlation, Resolve-Edit refine
  refine_strategies.py  — refine order (zoom / position / rotation)
  overlay_crop.py       — overlay crop / ECC mask
  reframe_detect.py     — detect a reframe (pan/tilt) from the warp
  match_quality.py      — match-quality thresholds and gates
  transform_analysis.py — clip analysis: setup → per-sample match → decide
  transform_convert.py  — warp → Edit Inspector parameters
  clip_edit_transform.py— read/simulate a clip's current Edit transform
  edit_match_mode.py / edit_quantize.py — absolute/delta mode + value quantization
  edit_apply.py         — write Pan/Tilt/Zoom/Rotation to the Edit Inspector
                          (with read-back verification)

  debug_frames.py       — comparison frames written to the debug folder
  logging_report.py     — logging and the final report
  extensions.py         — stubs (Perspective / Lens / Dynamic Sampling)
```

## Offline frame mapping

| Source | When it is used |
|--------|-----------------|
| **Hub** | The Resolve timeline is the single scale: clip `GetStart()` + local frame. |
| **EDL / XML** | Conform record TC is shifted into the hub (origin from the XML `<timecode>` or the first cut). |
| **Lock cut** | Same hub frame → frame in the reference file (reference at 00:00:00:00 ⇒ hub 120 = offline 120). |
| **Fallback** | No conform: hub frame maps directly to offline (+ `offline_timeline_offset_frames`). |

Supports CMX 3600 EDL (`* FROM CLIP NAME`, `* SOURCE FILE`) and basic
FCPXML / Premiere XMEML. FPS and resolution come only from the open Resolve
timeline; they are not set in config.

## Match analysis (transform_analysis.py)

`_analyze_single` is a thin driver over staged helpers:

- `_begin_clip` — validate the clip, read its baseline Edit, build the
  loop-invariant `_ClipContext` (canvas, sample points, thresholds).
- `_load_sample` — resolve the offline mapping and decode the online/offline
  frame pair into a `_SampleFrames` bundle.
- `_process_sample` — align (ECC/features), optionally refine to Edit
  parameters, and accept/reject one sample.
- `_should_stop_after` — confident early-exit (won't flatten an animated reframe).
- `_finalize_clip` — ECC-consensus rescue and the clip-level best/quality decision.
