"""Start Eosnox Labs on Windows (used by the Desktop and Start menu shortcuts).

Runs without a console window, keeps the laboratory data in the install
folder's "data" folder (never inside "app", which updates replace), and
writes what would have gone to the console into data\\logs\\eosnox.log.
"""

import os
import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
os.environ.setdefault("EOSNOX_DATA_DIR", str(ROOT / "data"))

logs = Path(os.environ["EOSNOX_DATA_DIR"]) / "logs"
logs.mkdir(parents=True, exist_ok=True)
log_file = logs / "eosnox.log"
if log_file.exists() and log_file.stat().st_size > 5_000_000:
    log_file.replace(logs / "eosnox.old.log")
sys.stdout = sys.stderr = open(log_file, "a", encoding="utf-8", buffering=1)

os.chdir(APP_DIR)
sys.path.insert(0, str(APP_DIR))

import ctypes

# One copy at a time: a second one could not receive from the H33 (port in use).
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\EosnoxLabsDesktop")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    ctypes.windll.user32.MessageBoxW(None, "Eosnox Labs is already open.", "Eosnox Labs", 0x40)
    sys.exit(0)

try:
    import desktop

    desktop.main()
except Exception:
    traceback.print_exc()
    ctypes.windll.user32.MessageBoxW(
        None,
        "Eosnox Labs could not start.\n\nDetails are in:\n{}".format(log_file),
        "Eosnox Labs",
        0x10,
    )
