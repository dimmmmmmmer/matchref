# MatchRef — Offline Reference Transform Match

[![CI](https://github.com/dimmmmmmmer/matchref/actions/workflows/ci.yml/badge.svg)](https://github.com/dimmmmmmmer/matchref/actions/workflows/ci.yml)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/8d6111c7164745968372545ab35ce8c2)](https://app.codacy.com/gh/dimmmmmmmer/matchref/dashboard)
[![Coverage](https://app.codacy.com/project/badge/Coverage/8d6111c7164745968372545ab35ce8c2)](https://app.codacy.com/gh/dimmmmmmmer/matchref/coverage)

A tool for **DaVinci Resolve Studio 20+** that rebuilds clip transforms
(Zoom, Pan, Tilt, Rotation) on your conformed timeline to match an **offline
reference** edit, writing the values into the **Edit Inspector**. It aligns each
shot to the reference with OpenCV (ECC + feature matching).

Typical use: the offline editor reframed shots, you conformed to the online
media, and you need those reframes back on the online timeline without redoing
them by hand.

## Requirements

- DaVinci Resolve **Studio** 20 or newer (Scripting API)
- Python 3 (the installer creates an isolated environment for you)

## Install

1. Download the **[latest release](https://github.com/dimmmmmmmer/matchref/releases/latest)**
   (under *Assets → Source code (zip)*) and unzip it.
2. Run the installer for your OS — it sets up a Python environment and copies
   MatchRef into Resolve's scripts folder (the first run takes a minute):

   | OS | Installer |
   |----|-----------|
   | **macOS** | double-click **`Install MatchRef.command`** |
   | **Windows** | double-click **`Install MatchRef (Windows).bat`** |
   | **Linux** | run **`Install MatchRef (Linux).sh`** (or `bash "Install MatchRef (Linux).sh"`) |

   > **macOS** may block a downloaded script the first time: if you see
   > *"unidentified developer"*, right-click the file → **Open** → **Open**.
   > **Windows** SmartScreen: click *More info → Run anyway*.

3. In DaVinci Resolve: **Workspace → Scripts → Utility → MatchRef**.

   Do **not** use *Scripts → matchref → main* — that path runs Resolve's bare
   Python without the dependencies and fails silently.

## Workflow

1. Open your **online** (conformed) timeline.
2. Render or locate the **offline reference** video.
3. *(Optional but recommended)* Have the conform **EDL or XML** ready — it makes
   frame mapping exact.
4. Mark the shots to process. In **Clips to process** choose how MatchRef finds
   them — by **clip color**, by **flag**, the current **timeline selection**, **all
   clips**, or a **whole track** — and pick the color. (Default: automatic —
   timeline selection, then clip color Purple, then all clips.)
   Don't mark the lock cut on the timeline.
5. Launch MatchRef, point it at the offline reference (and conform file), and
   run it. With Dry Run off it writes straight to the Edit Inspector.

MatchRef samples each shot (start / mid / end), aligns to the reference, and
applies the best transform — or keyframes a smooth reframe ramp. After writing,
it reads the values back to confirm Resolve accepted them.

## Clip selection

Choose this in the **Clips to process** panel of the GUI (or via
`clip_selection_mode` in config):

| Mode | Behavior |
|------|----------|
| Automatic (default) | timeline selection → **clip color** → flags → all filtered |
| By clip color | clips tagged with the chosen color |
| By flag | clips flagged with the chosen color |
| Selected in the timeline | the current `GetSelectedTimelineItems()` |
| All clips | every video clip except the lock cut |
| A whole video track | all clips on the chosen track |

## How frame mapping works

With an EDL/XML conform, record timecodes are mapped into the timeline hub for
exact offline lookup. Without one, MatchRef assumes the reference lines up with
the timeline by position (tunable via `offline_timeline_offset_frames`). FPS and
resolution come from the open Resolve timeline. See
[`docs/architecture.md`](docs/architecture.md) for details.

## Limitations

- Without EDL/XML, the reference must line up with the online timeline by record
  position; with EDL/XML, reel/source TC must match the conform events.
- Heavy grade, crop, or blow-out differences lower the match score.
- Transforms are written via the Edit Inspector only (not Fusion).

## License

Licensed under the [Apache License 2.0](LICENSE). Contributions welcome — see
[`CONTRIBUTING.md`](CONTRIBUTING.md).
