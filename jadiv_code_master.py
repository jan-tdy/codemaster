#!/usr/bin/env python3
"""Jadiv Code Master – a universal desktop app store.

Thin launcher: all real logic lives in the ``codemaster`` package next to
this file. Kept as the documented entry point (``python3
jadiv_code_master.py``, the desktop launcher's Exec=, CI's compile step)
so existing install instructions and self-update (which fetches this repo
and re-runs this same path) keep working.
"""

import sys

APP_NAME = "Jadiv Code Master"


def _report_missing_dependency(exc):
    """Report a startup dependency error through stderr and an available
    graphical notification."""
    message = (
        f"{APP_NAME} could not start: {exc}\n\n"
        "Install the required packages first:\n"
        "    sudo apt install python3-pyqt5 python3-requests\n"
        "or, if you prefer pip:\n"
        "    python3 -m pip install --user -r requirements.txt"
    )
    print(message, file=sys.stderr)
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
        return
    except Exception:
        pass
    import subprocess
    for cmd in (["zenity", "--error", "--title", APP_NAME, "--text", message],
                ["notify-send", "--urgency=critical", APP_NAME, message]):
        try:
            subprocess.run(cmd, check=False)
            return
        except FileNotFoundError:
            continue


if __name__ == "__main__":
    try:
        from codemaster.main_window import main
    except ImportError as exc:
        _report_missing_dependency(exc)
        sys.exit(1)
    main()
