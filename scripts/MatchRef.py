#!/usr/bin/env python3
"""
Запуск MatchRef из меню DaVinci Resolve (Workspace → Scripts → Utility → MatchRef).

Положите ЭТОТ файл сюда:
  ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/MatchRef.py

Рядом должна лежать папка проекта (весь репозиторий):
  .../Utility/matchref/   ← main.py, matchref/, .venv/, config/
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def _utility_dir() -> Path | None:
    # Resolve's menu host exec()s the script TEXT, so __file__ is undefined
    # there — touching it raises NameError and the click silently does nothing.
    # Fall back to argv[0], then to the standard per-OS Utility folders.
    try:
        return Path(__file__).resolve().parent
    except NameError:
        pass
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0.lower().endswith("matchref.py"):
        path = Path(argv0).expanduser()
        if path.is_file():
            return path.resolve().parent
    home = Path.home()
    candidates = [
        home
        / "Library"
        / "Application Support"
        / "Blackmagic Design"
        / "DaVinci Resolve"
        / "Fusion"
        / "Scripts"
        / "Utility",
        home / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility",
    ]
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        candidates.insert(
            0,
            Path(appdata)
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Support"
            / "Fusion"
            / "Scripts"
            / "Utility",
        )
    for base in candidates:
        if (base / "matchref" / "main.py").is_file():
            return base
    return None


def _find_project_root() -> Path | None:
    """Ищем корень проекта (main.py + пакет matchref)."""
    candidates = []
    env_root = os.environ.get("MATCHREF_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root))
    utility = _utility_dir()
    if utility is not None:
        candidates.append(utility / "matchref")  # Utility/matchref/ — копия репозитория
        candidates.append(utility.parent / "matchref")
    candidates.append(Path.home() / "Documents" / "matchref")

    for root in candidates:
        if (root / "main.py").is_file() and (root / "matchref" / "__init__.py").is_file():
            return root.resolve()
    return None


def _venv_python(project: Path) -> Path | None:
    # POSIX venvs put the interpreter in .venv/bin; Windows venvs use .venv/Scripts
    # with a .exe suffix. Check both so the Windows installer's venv is actually
    # used instead of silently falling back to Resolve's bare Python (no cv2/numpy).
    candidates = (
        project / ".venv" / "bin" / "python",
        project / ".venv" / "bin" / "python3",
        project / ".venv" / "Scripts" / "python.exe",
        project / ".venv" / "Scripts" / "python3.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _show_error(title: str, message: str) -> None:
    text = f"{title}\n\n{message}"
    print(text, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is None:
            QApplication([])
        QMessageBox.critical(None, title, message)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(title, message)
            root.destroy()
        except Exception:
            pass


def _crash_log_tail(log_path: Path) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-15:])


def _launch_via_venv(project: Path) -> bool:
    py = _venv_python(project)
    main_py = project / "main.py"
    if py is None or not main_py.is_file():
        return False
    # The GUI runs detached; its output goes to launcher.log so a startup crash
    # is diagnosable instead of vanishing into DEVNULL. The child keeps its
    # inherited descriptor after our handle closes.
    log_path = project / "launcher.log"
    cmd = [os.fspath(py), os.fspath(main_py)]
    with open(log_path, "wb") as log:
        # pylint: disable=consider-using-with — detached child, must not be waited on
        proc = subprocess.Popen(
            cmd,
            cwd=str(project),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    time.sleep(1.5)
    if proc.poll() is not None:
        _show_error(
            "MatchRef — ошибка запуска",
            f"GUI завершился сразу после запуска (код {proc.returncode}).\n\n"
            f"Последние строки лога ({log_path}):\n{_crash_log_tail(log_path)}",
        )
        return True  # handled — do not fall back to Resolve's bare Python
    print(f"MatchRef: started with {py} (log: {log_path})")
    return True


def _launch_inprocess(project: Path) -> None:
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    from matchref.gui import run_gui

    run_gui()


def main() -> None:
    project = _find_project_root()
    if project is None:
        _show_error(
            "MatchRef",
            "Не найден проект MatchRef.\n\n"
            "Ожидается структура:\n"
            "  .../Fusion/Scripts/Utility/MatchRef.py  (этот файл)\n"
            "  .../Fusion/Scripts/Utility/matchref/main.py\n"
            "  .../Fusion/Scripts/Utility/matchref/.venv/\n\n"
            "Или задайте MATCHREF_ROOT=/path/to/matchref",
        )
        return

    if _launch_via_venv(project):
        return

    try:
        _launch_inprocess(project)
    except Exception as exc:
        _show_error(
            "MatchRef — ошибка запуска",
            f"{exc}\n\n"
            "Создайте venv в папке проекта:\n"
            f"  cd {project}\n"
            "  ./setup.sh\n\n"
            "Либо запустите снаружи:\n"
            "  source .venv/bin/activate && python main.py",
        )
        traceback.print_exc()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — the host console is hidden; surface everything
        _show_error("MatchRef — ошибка лаунчера", f"{exc}\n\n{traceback.format_exc()}")
