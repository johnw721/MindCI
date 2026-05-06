"""
Filesystem watcher that auto-converts .txt files dropped into RAW_DIR.

Uses watchdog with a short debounce so editor "atomic save" sequences (which
fire multiple events in quick succession) trigger only one convert run.

Wire from the CLI:
    python mindci.py watch
"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Timer
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import RAW_DIR

DEBOUNCE_SECONDS = 2.5


class _DebouncedTxtHandler(FileSystemEventHandler):
    """Coalesces .txt create/modify events; fires `on_settled` after quiet period."""

    def __init__(self, on_settled: Callable[[list[str]], None]) -> None:
        self._on_settled = on_settled
        self._timer: Timer | None = None
        self._pending: set[str] = set()

    def _trigger(self, path: str) -> None:
        if not path.lower().endswith(".txt"):
            return
        self._pending.add(path)
        if self._timer:
            self._timer.cancel()
        self._timer = Timer(DEBOUNCE_SECONDS, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        if not self._pending:
            return
        files = sorted(self._pending)
        self._pending.clear()
        try:
            self._on_settled(files)
        except Exception as e:  # noqa: BLE001 — keep the watcher alive on user-code errors
            print(f"  ! watcher callback raised: {e}")

    def on_created(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_modified(self, event):  # type: ignore[override]
        if not event.is_directory:
            self._trigger(event.src_path)


def watch(on_settled: Callable[[list[str]], None]) -> None:
    """Block until Ctrl-C, calling `on_settled(file_paths)` when raw/ quiets down."""
    raw = Path(RAW_DIR)
    raw.mkdir(parents=True, exist_ok=True)
    handler = _DebouncedTxtHandler(on_settled)
    observer = Observer()
    observer.schedule(handler, str(raw), recursive=False)
    observer.start()
    print(f"Watching {raw}/ — drop .txt files to auto-convert. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
