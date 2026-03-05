"""
RealityScan delegation client for sending commands to a running instance.

Wraps the RealityScan CLI delegation interface (``-delegateTo``,
``-getStatus``, ``-waitCompleted``, ``-abortInstance``) into a
high-level Python API with two-phase idle detection, progress
reporting, and file-stability polling.

Designed for long-running photogrammetry pipelines where a single
operation may take 10+ hours and where race conditions between
command dispatch and status polling must be handled explicitly.

Based on the delegation patterns in
``StandaloneUtilities/ModelGenerator.py``, extracted for reuse.
"""

from __future__ import annotations

import glob
import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from modules.rc_common.rc_status import RCStatusParser

_log = logging.getLogger(__name__)


class RCDelegationClient:
    """Send commands to a running RealityScan instance via delegation.

    Parameters
    ----------
    rc_exe:
        Path to the RealityScan executable.
    instance_name:
        Instance name to delegate to.  ``"*"`` targets the first
        available instance.
    poll_interval:
        Default seconds between status polls during wait loops.
    logger:
        Optional :class:`logging.Logger`.  Falls back to the module-level
        logger when ``None``.
    """

    # How long to wait for RC to pick up a delegated command before we
    # declare a timeout in :meth:`wait_idle_two_phase` phase 1.
    _PICKUP_TIMEOUT: float = 30.0

    # Number of consecutive idle confirmations required before we
    # declare an operation truly complete (phase 2 triple-verify).
    _IDLE_CONFIRM_COUNT: int = 3
    _IDLE_CONFIRM_DELAY: float = 0.5

    # Grace period after completion to allow file I/O to settle.
    _COMPLETION_GRACE: float = 3.0

    def __init__(
        self,
        rc_exe: Path | str,
        instance_name: str = "*",
        poll_interval: float = 2.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.rc_exe = Path(rc_exe)
        self.instance_name = instance_name
        self.poll_interval = poll_interval
        self._log = logger or _log
        self._parser = RCStatusParser()

        # Optional progress callback:
        #   on_progress(operation_name, percent, elapsed_sec, eta_sec)
        self.on_progress: Optional[Callable[[str, float, float, float], None]] = None

    # ------------------------------------------------------------------ #
    # Low-level delegation helpers
    # ------------------------------------------------------------------ #

    def _run(
        self,
        args: list[str],
        *,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess and return the result.

        All output is captured as text.  On any
        :class:`subprocess.SubprocessError` a synthetic
        :class:`~subprocess.CompletedProcess` with *returncode=-1* is
        returned so that callers never need to catch exceptions.
        """
        cmd = [str(self.rc_exe)] + args
        cmd_str = " ".join(cmd)
        self._log.debug("Running: %s", cmd_str)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result
        except subprocess.SubprocessError as exc:
            self._log.warning("Subprocess error for '%s': %s", cmd_str, exc)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-1,
                stdout="",
                stderr=str(exc),
            )

    # ------------------------------------------------------------------ #
    # Public command methods
    # ------------------------------------------------------------------ #

    def delegate(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Send an arbitrary command via ``-delegateTo``.

        Returns the :class:`~subprocess.CompletedProcess` from the
        delegation dispatch (which typically returns immediately).
        """
        cmd_args = ["-delegateTo", self.instance_name] + list(args)
        return self._run(cmd_args)

    def get_status(self) -> dict:
        """Query ``-getStatus`` and return a parsed status dict.

        The dict keys are documented in :meth:`RCStatusParser.parse`.
        On communication failure the defaults dict (idle, zero
        progress) is returned.
        """
        result = self._run(
            ["-getStatus", self.instance_name],
            timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            lines = [
                ln.strip()
                for ln in result.stdout.splitlines()
                if ln.strip()
            ]
            status_text = lines[-1] if lines else ""
            return self._parser.parse(status_text)
        return self._parser.parse(None)

    def wait_completed(self) -> subprocess.CompletedProcess[str]:
        """Block until the instance signals completion (``-waitCompleted``)."""
        return self._run(["-waitCompleted", self.instance_name])

    def abort_instance(self) -> None:
        """Send ``-abortInstance`` to cancel queued/running operations."""
        self._log.info("Sending -abortInstance to '%s'", self.instance_name)
        self._run(["-abortInstance", self.instance_name], timeout=10)

    def verify_connection(self) -> bool:
        """Return ``True`` if we can reach the running instance."""
        try:
            status = self.get_status()
            return bool(status.get("raw"))
        except Exception:
            return False

    def clear_queue(self) -> None:
        """Abort any in-flight or queued commands and wait briefly."""
        self._log.info("Clearing command queue for '%s'", self.instance_name)
        self.abort_instance()
        time.sleep(1.0)

    def get_revision(self) -> int:
        """Return the current revision counter from ``-getStatus``."""
        return self.get_status().get("rev", 0)

    # ------------------------------------------------------------------ #
    # Compound operations
    # ------------------------------------------------------------------ #

    def run_quick(
        self,
        operation_name: str,
        *args: str,
    ) -> subprocess.CompletedProcess[str]:
        """Delegate a command and immediately ``-waitCompleted``.

        Suitable for fast commands that do not need progress monitoring
        (e.g. ``-selectComponent``, ``-renameSelectedModel``).
        """
        self._log.info("[%s] delegating: %s", operation_name, " ".join(args))
        self.delegate(*args)
        result = self.wait_completed()
        self._log.info("[%s] completed (rc=%d)", operation_name, result.returncode)
        return result

    # ------------------------------------------------------------------ #
    # Two-phase idle wait (the critical method)
    # ------------------------------------------------------------------ #

    def wait_idle_two_phase(
        self,
        operation_name: str,
        timeout: Optional[float] = None,
    ) -> dict:
        """Wait for a delegated operation to start **and** finish.

        This is the primary synchronisation primitive for long-running
        operations (model calculation, texturing, export, etc.).

        **Phase 1 -- pickup detection** (hard 30 s timeout):
            Poll until the instance transitions from idle to busy.
            Detection heuristics:

            * The operation *id* changes to a new value.
            * An idle-to-busy transition is observed.
            * Progress resets toward 0 from a higher value.

            If none of these triggers fire within 30 s a
            :class:`TimeoutError` is raised -- the command was likely
            never picked up.

        **Phase 2 -- completion wait** (no hard timeout by default):
            Poll until the instance returns to idle.  The idle state is
            *triple-verified* (three consecutive idle readings with
            0.5 s gaps) to avoid false positives caused by transient
            status artefacts.

            After confirmed idle a 3 s grace period is observed to let
            file writes settle.

            Progress is reported via :attr:`on_progress` on every
            >= 1 percentage-point change **or** every 10 s, whichever
            comes first.

        Parameters
        ----------
        operation_name:
            Human-readable label used in log messages and progress
            callbacks.
        timeout:
            Optional *total* timeout in seconds covering both phases.
            ``None`` means no overall limit (the operation may run for
            10+ hours).  The 30 s pickup timeout in phase 1 always
            applies independently.

        Returns
        -------
        dict
            The final parsed status dict at the moment idle was
            confirmed.

        Raises
        ------
        TimeoutError
            If the pickup timeout (30 s) or the optional *timeout* is
            exceeded.
        """
        wall_start = time.time()

        # Snapshot before the operation is expected to start.
        initial_status = self.get_status()
        initial_id = initial_status.get("id", "")
        initial_progress = initial_status.get("progress", 0.0)
        was_idle = initial_status.get("is_idle", True)

        time.sleep(1.5)

        # ---- Phase 1: wait for pickup --------------------------------
        self._log.info(
            "[%s] Phase 1 -- waiting for operation start", operation_name
        )
        pickup_deadline = time.time() + self._PICKUP_TIMEOUT
        started = False

        while not started:
            if time.time() > pickup_deadline:
                raise TimeoutError(
                    f"[{operation_name}] Operation was not picked up within "
                    f"{self._PICKUP_TIMEOUT:.0f}s"
                )
            if timeout is not None and (time.time() - wall_start) > timeout:
                raise TimeoutError(
                    f"[{operation_name}] Overall timeout ({timeout:.0f}s) "
                    f"exceeded during pickup phase"
                )

            status = self.get_status()
            cur_id = status.get("id", "")
            cur_progress = status.get("progress", 0.0)
            is_idle = status.get("is_idle", True)

            # Heuristic 1: operation ID changed to a real (non-idle) value
            if (
                cur_id != initial_id
                and cur_id != ""
                and cur_id.lower() != "0xffffffff"
            ):
                started = True
                self._log.info(
                    "[%s] Operation started (new id: %s)", operation_name, cur_id
                )
            # Heuristic 2: transitioned from idle to busy
            elif was_idle and not is_idle:
                started = True
                self._log.info(
                    "[%s] Operation started (idle -> busy, progress=%.1f%%)",
                    operation_name,
                    cur_progress,
                )
            # Heuristic 3: progress reset from a higher value
            elif initial_progress > 10.0 and cur_progress < 5.0:
                started = True
                self._log.info(
                    "[%s] Operation started (progress reset %.1f%% -> %.1f%%)",
                    operation_name,
                    initial_progress,
                    cur_progress,
                )

            if not started:
                time.sleep(1.25)

        # ---- Phase 2: wait for completion ----------------------------
        self._log.info(
            "[%s] Phase 2 -- waiting for operation completion", operation_name
        )
        last_reported_pct = -1.0
        last_report_time = time.time()

        while True:
            if timeout is not None and (time.time() - wall_start) > timeout:
                raise TimeoutError(
                    f"[{operation_name}] Overall timeout ({timeout:.0f}s) "
                    f"exceeded during completion phase"
                )

            status = self.get_status()
            cur_progress = status.get("progress", 0.0)
            estimation = status.get("estimation", 0.0)
            is_idle = status.get("is_idle", False)

            # -- progress reporting ------------------------------------
            now = time.time()
            elapsed = now - wall_start
            pct_changed = abs(cur_progress - last_reported_pct) >= 1.0
            time_elapsed = (now - last_report_time) >= 10.0

            if pct_changed or time_elapsed:
                self._log.info(
                    "[%s] %.1f%% (elapsed=%.1fs, eta=%.1fs)",
                    operation_name,
                    cur_progress,
                    elapsed,
                    estimation,
                )
                if self.on_progress is not None:
                    self.on_progress(
                        operation_name, cur_progress, elapsed, estimation
                    )
                last_reported_pct = cur_progress
                last_report_time = now

            # -- idle triple-verify ------------------------------------
            if is_idle:
                confirmed = True
                for _ in range(self._IDLE_CONFIRM_COUNT - 1):
                    time.sleep(self._IDLE_CONFIRM_DELAY)
                    verify = self.get_status()
                    if not verify.get("is_idle", False):
                        confirmed = False
                        break
                if confirmed:
                    final_status = self.get_status()
                    elapsed = time.time() - wall_start
                    self._log.info(
                        "[%s] Completed in %.1fs", operation_name, elapsed
                    )
                    # Grace period for file writes to flush.
                    time.sleep(self._COMPLETION_GRACE)
                    return final_status

            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------ #
    # File stability polling
    # ------------------------------------------------------------------ #

    def wait_for_stable_files(
        self,
        directory: Path | str,
        pattern: str,
        min_stable_sec: float = 10.0,
        timeout: float = 900.0,
    ) -> list[Path]:
        """Poll *directory* until the matching file count stabilises.

        Returns the list of matching paths once the count has not
        changed for *min_stable_sec* consecutive seconds, or raises
        :class:`TimeoutError` after *timeout* seconds.

        Parameters
        ----------
        directory:
            Directory to watch.
        pattern:
            Glob pattern relative to *directory* (e.g. ``"*.fbx"``).
        min_stable_sec:
            How long the file count must remain unchanged to be
            considered stable.
        timeout:
            Maximum wall-clock seconds to wait.
        """
        directory = Path(directory)
        full_pattern = str(directory / pattern)
        deadline = time.time() + timeout
        last_count: Optional[int] = None
        stable_since: Optional[float] = None

        self._log.info(
            "Waiting for stable files in %s matching '%s' "
            "(stable=%ss, timeout=%ss)",
            directory,
            pattern,
            min_stable_sec,
            timeout,
        )

        while time.time() < deadline:
            current_files = sorted(glob.glob(full_pattern))
            current_count = len(current_files)

            if current_count != last_count:
                last_count = current_count
                stable_since = time.time()
                self._log.debug(
                    "File count changed to %d, resetting stability timer",
                    current_count,
                )
            elif stable_since is not None:
                stable_elapsed = time.time() - stable_since
                if stable_elapsed >= min_stable_sec:
                    self._log.info(
                        "File count stable at %d for %.1fs",
                        current_count,
                        stable_elapsed,
                    )
                    return [Path(f) for f in current_files]

            time.sleep(self.poll_interval)

        raise TimeoutError(
            f"File count in {directory} did not stabilise within {timeout:.0f}s "
            f"(last count: {last_count})"
        )
