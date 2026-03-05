"""Background worker for running pipeline modules in a separate thread."""
from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from module_base.rc_module import RCModule
from modules.rc_common.progress import ProgressBackend, ProgressEvent, ProgressReporter

_log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals emitted by PipelineWorker."""
    started = Signal(str)           # step_name
    progress = Signal(str, float)   # step_name, percent
    log_message = Signal(str, str)  # level ('info'|'warning'|'error'), message
    finished = Signal(str, dict)    # step_name, result dict
    error = Signal(str, str)        # step_name, error message


class GUIProgressBackend(ProgressBackend):
    """Progress backend that emits Qt signals."""

    def __init__(self, signals: WorkerSignals, step_name: str) -> None:
        self._signals = signals
        self._step_name = step_name

    def start(self, operation_name: str, total_steps: int | None = None) -> None:
        self._signals.log_message.emit("info", f"[{self._step_name}] Starting: {operation_name}")

    def report(self, event: ProgressEvent) -> None:
        self._signals.progress.emit(self._step_name, event.progress_pct)
        if event.message:
            self._signals.log_message.emit("info", f"[{self._step_name}] {event.message}")

    def finish(self) -> None:
        pass


class PipelineWorker(QRunnable):
    """Runs a single pipeline module in a background thread."""

    def __init__(self, step_name: str, module: RCModule) -> None:
        super().__init__()
        self.step_name = step_name
        self.module = module
        self.signals = WorkerSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        self.signals.started.emit(self.step_name)
        try:
            # Set up progress reporter
            backend = GUIProgressBackend(self.signals, self.step_name)
            reporter = ProgressReporter([backend])
            if hasattr(self.module, 'set_progress_reporter'):
                self.module.set_progress_reporter(reporter)

            # Validate
            ok, msg = self.module.validate_parameters()
            if not ok:
                self.signals.error.emit(self.step_name, f"Validation failed: {msg}")
                return

            self.signals.log_message.emit("info", f"Running module: {self.step_name}")

            # Run
            result = self.module.run()
            self.module.finish()

            if result is None:
                self.signals.error.emit(self.step_name, "Module returned None")
                return

            if isinstance(result, dict) and result.get('Success') is False:
                error_msg = result.get('Error', 'Module reported failure')
                self.signals.error.emit(self.step_name, error_msg)
                return

            self.signals.finished.emit(self.step_name, result if isinstance(result, dict) else {"Success": True})

        except Exception as e:
            tb = traceback.format_exc()
            _log.error("Worker error in %s: %s\n%s", self.step_name, e, tb)
            self.signals.error.emit(self.step_name, str(e))
