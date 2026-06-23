# MatchRef — Offline Reference Transform Match

A tool for **DaVinci Resolve Studio 20+** that rebuilds clip transforms
(Zoom, Pan, Tilt, Rotation) on your conformed timeline to match an **offline
reference** edit, writing the values into the **Edit Inspector**. It aligns each
shot to the reference with OpenCV (ECC + feature matching).

Typical use: the offline editor reframed shots, you conformed to the online
media, and you need those reframes back on the online timeline without redoing
them by hand.

## Requirements

- DaVinci Resolve **Studio** 20 or newer (Scripting API)
- macOS with Python 3 (the installer creates an isolated environment)

## Install (macOS)

1. Download this project (green **Code → Download ZIP** on GitHub) and unzip it.
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

<details>
<summary>Manual install</summary>

```bash
cd /path/to/matchref
./setup.sh                 # create .venv and install dependencies (macOS/Linux)
./install_resolve.sh       # copy into the Resolve Scripts/Utility folder
```

Run the GUI outside Resolve for debugging:

```bash
source .venv/bin/activate
python main.py
```
</details>

## Workflow

1. Open your **online** (conformed) timeline.
2. Render or locate the **offline reference** video.
3. *(Optional but recommended)* Have the conform **EDL or XML** ready — it makes
   frame mapping exact.
4. Flag the shots to process: right-click each online clip →
   **Clip Color → Purple**. Don't color the lock cut on the timeline.
5. Launch MatchRef, point it at the offline reference (and conform file), and
   run it. With Dry Run off it writes straight to the Edit Inspector.

MatchRef samples each shot (start / mid / end), aligns to the reference, and
applies the best transform — or keyframes a smooth reframe ramp. After writing,
it reads the values back to confirm Resolve accepted them.

## Clip selection

Set `clip_selection_mode` in `config/user_config.json`:

| Mode | Behavior |
|------|----------|
| `auto` (default) | Selected API → **Clip Color** → flags → all filtered |
| `clip_color` | Clips with `selection_clip_color` (Purple, …) |
| `selected` | Only `GetSelectedTimelineItems()` |
| `all_filtered` | All video clips except the lock cut |
| `track` | All clips on `video_track_index` |

## Configuration

- Defaults: `config/default_config.json`
- Your overrides: `config/user_config.json` (written by the GUI)
- **Full reference for every key:** [`docs/config.md`](docs/config.md)

Common ones: `dry_run` (analyze without writing), `ecc_threshold` (match
strictness), `input_scaling` (`fit`/`fill`/`stretch` — must match your project's
Mismatched-Resolution setting), `edit_round_mode` (`nearest`/`up`).

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

## Development

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q          # unit tests (no Resolve required)
ruff check .       # lint
mypy               # type check
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest on every push/PR.
Optional local hooks: `pre-commit install`. Module map:
[`docs/architecture.md`](docs/architecture.md).

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
