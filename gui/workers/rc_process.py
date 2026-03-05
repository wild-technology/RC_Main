"""Threaded wrapper for RealityScan subprocess execution."""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

_log = logging.getLogger(__name__)


class RCProcessSignals(QObject):
    """Signals from RealityScan process execution."""
    output_line = Signal(str)       # stdout/stderr line
    command_sent = Signal(str)      # RC command string
    process_finished = Signal(int)  # return code
    error = Signal(str)             # error message


class RCProcess:
    """Manages a RealityScan subprocess with signal-based output.

    Wraps subprocess.run in a thread so the GUI stays responsive.
    """

    def __init__(self, rc_exe: str) -> None:
        self.rc_exe = rc_exe
        self.signals = RCProcessSignals()
        self._current_process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def execute_async(self, *args: str, timeout: int | None = None) -> None:
        """Run an RC command asynchronously in a background thread."""
        cmd = [self.rc_exe] + list(args)
        cmd_str = " ".join(cmd)
        self.signals.command_sent.emit(cmd_str)
        _log.info("Executing: %s", cmd_str)

        def _run():
            try:
                self._current_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                # Read stdout line by line
                if self._current_process.stdout:
                    for line in self._current_process.stdout:
                        line = line.rstrip()
                        if line:
                            self.signals.output_line.emit(line)

                self._current_process.wait(timeout=timeout)
                rc = self._current_process.returncode
                self.signals.process_finished.emit(rc)

            except subprocess.TimeoutExpired:
                self.signals.error.emit(f"Process timed out after {timeout}s")
                if self._current_process:
                    self._current_process.kill()
                self.signals.process_finished.emit(-1)

            except Exception as e:
                self.signals.error.emit(str(e))
                self.signals.process_finished.emit(-1)

            finally:
                self._current_process = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def kill(self) -> None:
        """Kill the current RC process if running."""
        if self._current_process:
            try:
                self._current_process.kill()
                _log.warning("RC process killed")
            except Exception as e:
                _log.error("Failed to kill RC process: %s", e)

    @property
    def is_running(self) -> bool:
        return self._current_process is not None
