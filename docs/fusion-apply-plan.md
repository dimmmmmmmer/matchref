# Fusion apply backend — plan & validation checklist

**Status: prototype.** The value math is implemented and unit-tested; the live
Fusion graph construction needs validation inside DaVinci Resolve.

## Why

The Edit Inspector is **affine-only** (Zoom / Pan / Tilt / Rotation) and its
per-frame keyframes depend on nudging the playhead and writing `SetProperty` —
which is fragile (we already guard against collapsed ramps in `edit_apply.py`).

A **Fusion Transform node** keyframes cleanly and is the same node graph we need
for **perspective** later (swap/add a CornerPin or Perspective node). So one
Fusion backend covers two of the requested features:

1. Reliable keyframes for **animated** clips (this prototype).
2. **Perspective** match (next step — homography → CornerPin).

Lens distortion is deferred (estimation is the hard part; see roadmap).

## Design

```
clip ──► Fusion comp ──► Transform node (keyframed) ──► output
```

- `should_use_fusion(result, config)` — gate: `apply_via_fusion` on **and** the
  clip is animated with ≥2 ok samples. Default off, so nothing changes until a
  user opts in.
- `build_keyframe_spec(result, config, size)` *(pure, tested)* — one
  `(clip_local_frame, FusionTransform)` per ok sample, ordered by frame.
- `fusion_transform_values(resolved, size)` *(pure, tested)* — converts a resolved
  Edit transform (px Pan/Tilt, Zoom, Rotation) to Fusion Transform inputs:
  - `Size`  = zoom (1.0 = 100%)
  - `Center.X` = 0.5 + pan / width
  - `Center.Y` = 0.5 + tilt / height   *(Fusion Y is bottom-up)*
  - `Angle` = rotation°
- `FusionTransformApplier.apply_animated(item, result)` *(PROTOTYPE, Resolve only)*
  — gets/creates the clip's Fusion comp, adds a `Transform` tool, and writes the
  spec as keyframes.

## What to validate in Resolve

1. **Comp/node creation API.** Confirm `item.GetFusionCompCount()` /
   `GetFusionCompByIndex(1)` / `AddFusionComp()` and `comp.AddTool("Transform")`
   are the right calls on the target Resolve build.
2. **Keyframe insertion.** `node.SetInput(attr, value, frame)` writes a value at a
   frame on many builds, but proper spline keyframes may need the Fusion
   `BezierSpline`/`AddPoint` path. This is the main thing to iterate on.
3. **Sign conventions.** Confirm the direction of `Center.Y` (tilt) and `Angle`;
   flip in `fusion_transform_values` if a known reframe goes the wrong way.
4. **Coexistence with the Edit transform.** Decide whether the Fusion comp should
   neutralise / replace the clip's Edit-page transform to avoid double-applying.

## Roadmap after validation

- Perspective: solve a homography (ECC `MOTION_HOMOGRAPHY`, already available) and
  map its four mapped corners to a CornerPin node's `TopLeft…BottomRight` inputs.
- Surface `apply_via_fusion` in the GUI once the prototype is confirmed.
