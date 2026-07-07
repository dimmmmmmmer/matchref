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
import traceback
from pathlib import Path


def _utility_dir() -> Path:
    return Path(__file__).resolve().parent


def _find_project_root() -> Path | None:
    """Ищем корень проекта (main.py + пакет matchref)."""
    utility = _utility_dir()
    candidates = [
        utility / "matchref",  # Utility/matchref/ — типичная копия репозитория
        utility.parent / "matchref",
        Path.home() / "Documents" / "matchref",
    ]
    env_root = os.environ.get("MATCHREF_ROOT", "").strip()
    if env_root:
        candidates.insert(0, Path(env_root))

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

        QApplication.instance() or QApplication([])
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


def _launch_via_venv(project: Path) -> bool:
    py = _venv_python(project)
    main_py = project / "main.py"
    if py is None or not main_py.is_file():
        return False
    subprocess.Popen(
        [str(py), str(main_py)],
        cwd=str(project),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"MatchRef: started with {py}")
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
    main()
