"""Unified progress reporting system for the RC pipeline.

Provides a ProgressReporter that distributes ProgressEvents to multiple
backends (tqdm CLI, Python logging, PySide6 GUI signal stub).
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from tqdm import tqdm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class ProgressEvent:
    """Snapshot of progress state emitted to backends."""

    module_name: str
    operation_name: str
    progress_pct: float  # 0–100
    elapsed_sec: float
    eta_sec: float
    message: str
    current_file: Optional[str] = None
    file_index: Optional[int] = None
    file_total: Optional[int] = None


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------

class ProgressBackend(ABC):
    """Abstract base class for progress reporting destinations."""

    @abstractmethod
    def report(self, event: ProgressEvent) -> None:
        """Handle an incoming progress event."""

    @abstractmethod
    def start_operation(self, name: str, total_steps: int) -> None:
        """Signal that a new operation is starting."""

    @abstractmethod
    def update(self, increment: int = 1) -> None:
        """Advance the current operation by *increment* steps."""

    @abstractmethod
    def finish(self) -> None:
        """Mark the current operation as complete and clean up."""


# ---------------------------------------------------------------------------
# Tqdm CLI backend
# ---------------------------------------------------------------------------

class TqdmBackend(ProgressBackend):
    """Wraps *tqdm* to render a CLI progress bar.

    On each :meth:`report` call where ``current_file`` is set the exact
    file path is logged via the module logger at INFO level.
    """

    def __init__(self) -> None:
        self._bar: Optional[tqdm] = None
        self._operation_name: str = ""

    def report(self, event: ProgressEvent) -> None:
        if event.current_file:
            logger.info(
                "[%s] Processing: %s", event.operation_name, event.current_file
            )

        if self._bar is not None:
            self._bar.set_postfix_str(
                f"{event.progress_pct:.1f}% | "
                f"elapsed {event.elapsed_sec:.0f}s | "
                f"ETA {event.eta_sec:.0f}s"
                + (
                    f" | file {event.file_index}/{event.file_total}"
                    if event.file_index is not None and event.file_total is not None
                    else ""
                ),
                refresh=True,
            )

    def start_operation(self, name: str, total_steps: int) -> None:
        self.finish()  # close any previous bar
        self._operation_name = name
        self._bar = tqdm(total=total_steps, desc=name, unit="step")

    def update(self, increment: int = 1) -> None:
        if self._bar is not None:
            self._bar.update(increment)

    def finish(self) -> None:
        if self._bar is not None:
            self._bar.close()
            self._bar = None


# ---------------------------------------------------------------------------
# Python logging backend
# ---------------------------------------------------------------------------

class LogBackend(ProgressBackend):
    """Writes progress information to the Python logging system.

    Format::

        [{timestamp}] [{operation}] Processing: {current_file} \
(file {index}/{total}) — {message}
    """

    def __init__(self, log_level: int = logging.INFO) -> None:
        self._log_level = log_level
        self._operation_name: str = ""
        self._total_steps: int = 0
        self._completed: int = 0

    def report(self, event: ProgressEvent) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        file_info = ""
        if event.current_file:
            index_part = ""
            if event.file_index is not None and event.file_total is not None:
                index_part = f" (file {event.file_index}/{event.file_total})"
            file_info = f" Processing: {event.current_file}{index_part}"

        logger.log(
            self._log_level,
            "[%s] [%s]%s — %s",
            timestamp,
            event.operation_name,
            file_info,
            event.message,
        )

    def start_operation(self, name: str, total_steps: int) -> None:
        self._operation_name = name
        self._total_steps = total_steps
        self._completed = 0
        logger.log(
            self._log_level,
            "[%s] Starting operation: %s (%d steps)",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            name,
            total_steps,
        )

    def update(self, increment: int = 1) -> None:
        self._completed += increment
        logger.log(
            self._log_level,
            "[%s] [%s] Step %d/%d",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            self._operation_name,
            self._completed,
            self._total_steps,
        )

    def finish(self) -> None:
        logger.log(
            self._log_level,
            "[%s] Finished operation: %s",
            time.strftime("%Y-%m-%d %H:%M:%S"),
            self._operation_name,
        )


# ---------------------------------------------------------------------------
# PySide6 GUI signal stub
# ---------------------------------------------------------------------------

class SignalBackend(ProgressBackend):
    """Placeholder backend for PySide6 GUI integration.

    Stores a callback and invokes it with each :class:`ProgressEvent`.
    """

    def __init__(self, callback: Optional[Callable[[ProgressEvent], Any]] = None) -> None:
        self._callback = callback

    def report(self, event: ProgressEvent) -> None:
        if self._callback is not None:
            self._callback(event)

    def start_operation(self, name: str, total_steps: int) -> None:
        pass  # GUI wiring TBD

    def update(self, increment: int = 1) -> None:
        pass  # GUI wiring TBD

    def finish(self) -> None:
        pass  # GUI wiring TBD


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

class ProgressReporter:
    """Thread-safe aggregator that fans out progress to multiple backends.

    Typical usage::

        reporter = ProgressReporter(backends=[TqdmBackend(), LogBackend()])
        reporter.start_operation("Align", total_steps=500)
        for img in images:
            reporter.set_current_file(img)
            process(img)
            reporter.update()
        reporter.finish()
    """

    def __init__(self, backends: Optional[list[ProgressBackend]] = None) -> None:
        self._backends: list[ProgressBackend] = backends or []
        self._lock = threading.Lock()

        # Internal state for building events
        self._module_name: str = ""
        self._operation_name: str = ""
        self._total_steps: int = 0
        self._completed: int = 0
        self._current_file: Optional[str] = None
        self._start_time: float = 0.0

    # -- public API ---------------------------------------------------------

    def set_module_name(self, name: str) -> None:
        """Set the module name included in all subsequent events."""
        with self._lock:
            self._module_name = name

    def start_operation(self, name: str, total_steps: int) -> None:
        """Begin a new tracked operation with *total_steps* steps."""
        with self._lock:
            self._operation_name = name
            self._total_steps = total_steps
            self._completed = 0
            self._current_file = None
            self._start_time = time.monotonic()
            for backend in self._backends:
                backend.start_operation(name, total_steps)

    def set_current_file(self, path: str) -> None:
        """Record the file currently being processed."""
        with self._lock:
            self._current_file = path

    def update(self, increment: int = 1) -> None:
        """Advance progress by *increment* steps and notify backends."""
        with self._lock:
            self._completed += increment
            for backend in self._backends:
                backend.update(increment)

    def report(self, message: str = "") -> None:
        """Build a :class:`ProgressEvent` from internal state and fan out."""
        with self._lock:
            elapsed = time.monotonic() - self._start_time if self._start_time else 0.0
            if self._completed > 0 and self._total_steps > 0:
                rate = elapsed / self._completed
                remaining = self._total_steps - self._completed
                eta = rate * remaining
            else:
                eta = 0.0

            pct = (
                (self._completed / self._total_steps * 100.0)
                if self._total_steps > 0
                else 0.0
            )

            event = ProgressEvent(
                module_name=self._module_name,
                operation_name=self._operation_name,
                progress_pct=pct,
                elapsed_sec=elapsed,
                eta_sec=eta,
                message=message,
                current_file=self._current_file,
                file_index=self._completed if self._current_file else None,
                file_total=self._total_steps if self._current_file else None,
            )

            for backend in self._backends:
                backend.report(event)

    def finish(self) -> None:
        """Signal that the current operation is complete."""
        with self._lock:
            for backend in self._backends:
                backend.finish()
