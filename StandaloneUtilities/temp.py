#!/usr/bin/env python3
"""
Simplify all components in an open RealityCapture project.

Execution Logic:
- Works within an ALREADY OPEN RealityCapture project with components loaded
- Scans alignment directory for .rsalign filenames to determine component names
- Uses -selectComponent to select each component by name
- Runs simplification and cleanup pipeline on each component

Safety Features:
- Sends -abortInstance on startup to clear any queued commands from previous runs
- Each command is sent as a separate delegation (one command at a time)
- Uses TWO-PHASE IDLE DETECTION to properly wait for operations
- Ctrl+C triggers -abortInstance before exiting

Pipeline per component (11 steps):
  1. -selectComponent <n>
  2. -simplify [params.xml]
  3. -selectLargeTrianglesRel 3.0
  4. -removeSelectedTriangles
  5. -selectLargestModelComponent
  6. -invertTrianglesSelection
  7. -removeSelectedTriangles
  8. -cleanModel
  9. -smooth
  10. -simplify [params.xml]
  11. -cleanModel

Uses delegation (-delegateTo * <single_cmd>) for each step with status polling.
"""

import subprocess
import sys
import signal
import time
import re
import atexit
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field


class RCError(Exception):
    """Base exception for RealityCapture errors."""
    pass


class AbortedError(RCError):
    """Raised when processing is aborted by user."""
    pass


@dataclass
class StepResult:
    """Result of a single pipeline step."""
    step_number: int
    step_name: str
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    success: bool
    final_status: str = ""
    error_message: str = ""


@dataclass
class ComponentResult:
    """Result of processing a single component."""
    component_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool = False
    steps: list[StepResult] = field(default_factory=list)
    error_message: str = ""


class RCStatusParser:
    """
    Parse RealityCapture status strings.

    Expected format: id:0x10001 progress:57.5% runtime:4.26sec endEstimation:3.40sec
    Or when idle: id:0xffffffff progress:0.0%
    """

    IDLE_INDICATORS = [
        "idle",
        "id:0xffffffff",
    ]

    @staticmethod
    def parse(status_text: Optional[str]) -> dict:
        """Parse status string into components."""
        result = {
            "raw": status_text or "",
            "id": "",
            "progress": 0.0,
            "progress_str": "",
            "runtime": "",
            "estimation": "",
            "is_idle": False,
        }

        if not status_text:
            result["is_idle"] = True
            return result

        status_lower = status_text.lower()

        for indicator in RCStatusParser.IDLE_INDICATORS:
            if indicator in status_lower:
                result["is_idle"] = True

        parts = status_text.split()
        for part in parts:
            if ':' not in part:
                continue
            key, value = part.split(':', 1)
            key_lower = key.lower()

            if key_lower == "id":
                result["id"] = value
            elif key_lower == "progress":
                result["progress_str"] = value
                match = re.search(r'(\d+(?:\.\d+)?)', value)
                if match:
                    result["progress"] = float(match.group(1))
            elif key_lower == "runtime":
                result["runtime"] = value
            elif key_lower in ("endestimation", "estimation"):
                result["estimation"] = value

        if result["progress"] >= 100.0:
            result["is_idle"] = True

        return result


class SimplifyProcessor:
    """
    Run simplification on all components in an open RealityCapture project.

    Features:
    - One command per delegation call for safe abort capability
    - Startup abort to clear any queued commands
    - Signal handler for clean abort on Ctrl+C
    - Progress monitoring via -getStatus polling
    """

    _active_instance: Optional["SimplifyProcessor"] = None

    def __init__(
            self,
            rc_exe: Path,
            alignment_dir: Path,
            simplify_params: Optional[Path] = None,
            instance_name: str = "*",
            poll_interval: float = 2.0,
            test_mode: bool = True,
            verbose: bool = True,
    ):
        """
        Initialize the SimplifyProcessor.

        Args:
            rc_exe: Path to RealityScan.exe
            alignment_dir: Directory containing .rsalign files
            simplify_params: Optional path to simplification params.xml
            instance_name: RC instance name or "*" for first available
            poll_interval: Seconds between status polls
            test_mode: If True, only process first component
            verbose: If True, print detailed status updates
        """
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.simplify_params = simplify_params
        self.instance_name = instance_name
        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.verbose = verbose

        self.results: list[ComponentResult] = []
        self._status_parser = RCStatusParser()
        self._abort_requested = False

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.alignment_dir.exists():
            raise FileNotFoundError(f"Alignment directory not found: {self.alignment_dir}")

        SimplifyProcessor._active_instance = self
        self._setup_signal_handlers()
        atexit.register(self._cleanup)

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful abort."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    @staticmethod
    def _signal_handler(signum, frame) -> None:
        """Handle interrupt signals by requesting abort."""
        instance = SimplifyProcessor._active_instance
        if instance is not None:
            instance._log("")
            instance._log("=" * 60)
            instance._log("INTERRUPT RECEIVED - Aborting RealityCapture operations...")
            instance._log("=" * 60)
            instance._request_abort()
            raise AbortedError("Processing aborted by user")

    def _request_abort(self) -> None:
        """Request abort and send abort command to RC."""
        self._abort_requested = True
        self._abort_instance()

    def _cleanup(self) -> None:
        """Cleanup on exit."""
        SimplifyProcessor._active_instance = None

    def _log(self, message: str, indent: int = 0) -> None:
        """Print a timestamped log message."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = "  " * indent
        print(f"[{timestamp}] {prefix}{message}")

    def _abort_instance(self) -> None:
        """Send abort command to RealityCapture to stop current operations."""
        self._log("Sending -abortInstance to RealityCapture...", indent=1)
        try:
            cmd = [str(self.rc_exe), "-abortInstance", self.instance_name]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            self._log("Abort command sent", indent=1)
        except subprocess.TimeoutExpired:
            self._log("Warning: Abort command timed out", indent=1)
        except Exception as e:
            self._log(f"Warning: Failed to send abort: {e}", indent=1)

    def _clear_queue(self) -> None:
        """Clear any queued commands from previous runs by sending abort."""
        self._log("Clearing any queued commands from previous runs...")
        self._abort_instance()
        time.sleep(1.0)

    def _get_status(self) -> dict:
        """Query RealityCapture status via -getStatus."""
        try:
            cmd = [str(self.rc_exe), "-getStatus", self.instance_name]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0 and result.stdout:
                lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
                status_text = lines[-1] if lines else ""
                return self._status_parser.parse(status_text)

            return self._status_parser.parse(None)

        except subprocess.TimeoutExpired:
            return self._status_parser.parse(None)
        except Exception:
            return self._status_parser.parse(None)

    def _verify_connection(self) -> bool:
        """Verify we can communicate with RealityCapture."""
        status = self._get_status()
        return bool(status.get("raw"))

    def _delegate_single_command(
            self,
            operation_name: str,
            *rc_command_args: str,
            step_number: int = 0,
            total_steps: int = 0,
    ) -> StepResult:
        """
        Execute a SINGLE command via delegation and wait for completion.

        Uses two-phase idle detection:
        1. Send delegation command
        2. Wait for RC to become BUSY (operation started)
        3. Wait for RC to return to IDLE (operation completed)

        Args:
            operation_name: Human-readable name for logging
            rc_command_args: Single RealityCapture command with its arguments
            step_number: Current step number for display
            total_steps: Total steps for display

        Returns:
            StepResult with timing and status information
        """
        if self._abort_requested:
            raise AbortedError("Abort requested")

        step_label = f"[{step_number}/{total_steps}]" if total_steps > 0 else ""
        self._log(f"{step_label} {operation_name}...", indent=1)

        started_at = datetime.now()

        cmd = [
            str(self.rc_exe),
            "-delegateTo", self.instance_name,
        ] + list(rc_command_args)

        if self.verbose:
            cmd_str = " ".join(rc_command_args)
            self._log(f"Command: {cmd_str}", indent=2)

        initial_status = self._get_status()
        initial_id = initial_status.get("id", "")
        initial_progress = initial_status.get("progress", 0.0)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                self._log(f"Warning: Delegation returned {result.returncode}", indent=2)
        except subprocess.TimeoutExpired:
            self._log("Warning: Delegation command timed out", indent=2)
        except Exception as e:
            self._log(f"Warning: Delegation failed: {e}", indent=2)

        time.sleep(1.5)

        # PHASE 1: Wait for operation to START
        self._log("Waiting for operation to start...", indent=2)
        operation_started = False
        phase1_timeout = 10.0
        phase1_start = time.time()
        was_initially_idle = initial_status.get("is_idle", True)

        while not operation_started and (time.time() - phase1_start) < phase1_timeout:
            if self._abort_requested:
                raise AbortedError("Abort requested waiting for operation start")

            status = self._get_status()
            current_id = status.get("id", "")
            current_progress = status.get("progress", 0.0)
            is_idle = status.get("is_idle", True)

            if current_id != initial_id and current_id != "" and current_id != "0xffffffff":
                operation_started = True
                self._log(f"Operation started (new ID: {current_id})", indent=2)
            elif was_initially_idle and not is_idle:
                operation_started = True
                self._log(f"Operation started (status: busy, progress: {current_progress:.1f}%)", indent=2)
            elif initial_progress > 10.0 and current_progress < 5.0:
                operation_started = True
                self._log(f"Operation started (progress reset)", indent=2)
            elif not is_idle and 0 < current_progress < 95.0:
                operation_started = True
                self._log(f"Operation in progress ({current_progress:.1f}%)", indent=2)

            if not operation_started:
                time.sleep(1.25)

        if not operation_started:
            status = self._get_status()
            if status.get("is_idle", False):
                self._log("Operation may have completed instantly or failed to start", indent=2)
                time.sleep(1.0)

        # PHASE 2: Wait for operation to COMPLETE
        self._log("Waiting for operation to complete...", indent=2)
        last_progress = -1.0
        last_log_time = time.time()

        while True:
            if self._abort_requested:
                raise AbortedError("Abort requested during operation")

            status = self._get_status()
            current_progress = status.get("progress", 0.0)
            is_idle = status.get("is_idle", False)
            estimation = status.get("estimation", "")

            now = time.time()
            if abs(current_progress - last_progress) >= 1.0 or (now - last_log_time) >= 10.0:
                elapsed = (datetime.now() - started_at).total_seconds()
                est_str = f" (est: {estimation})" if estimation else ""
                self._log(f"Progress: {current_progress:.1f}%{est_str} [{elapsed:.1f}s]", indent=2)
                last_progress = current_progress
                last_log_time = now

            if is_idle:
                time.sleep(0.5)
                status2 = self._get_status()
                if status2.get("is_idle", False):
                    time.sleep(0.5)
                    status3 = self._get_status()
                    if status3.get("is_idle", False):
                        break

            time.sleep(self.poll_interval)

        completed_at = datetime.now()
        duration = (completed_at - started_at).total_seconds()

        self._log(f"Completed in {duration:.1f}s", indent=2)

        return StepResult(
            step_number=step_number,
            step_name=operation_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            success=True,
            final_status=status.get("raw", ""),
            error_message="",
        )

    def scan_component_names(self) -> list[str]:
        """Scan alignment directory for .rsalign files and extract component names."""
        rsalign_files = sorted(self.alignment_dir.glob("*.rsalign"))
        component_names = [f.stem for f in rsalign_files]
        return component_names

    def simplify_component(self, component_name: str) -> ComponentResult:
        """
        Run simplification on a single component.

        Args:
            component_name: Name of the component to simplify

        Returns:
            ComponentResult with step results
        """
        result = ComponentResult(
            component_name=component_name,
            started_at=datetime.now(),
        )

        self._log("=" * 60)
        self._log(f"Simplifying component: {component_name}")
        self._log("=" * 60)

        # Build simplify command args
        simplify_args = ["-simplify"]
        if self.simplify_params and self.simplify_params.exists():
            simplify_args.append(str(self.simplify_params))
            self._log(f"Using parameters: {self.simplify_params.name}", indent=1)

        steps = [
            ("Select component", ["-selectComponent", component_name]),
            ("Simplify model", simplify_args),
            ("Select large triangles (3.0x)", ["-selectLargeTrianglesRel", "13.0"]),
            ("Filter large triangles", ["-removeSelectedTriangles"]),
            ("Select largest component", ["-selectLargestModelComponent"]),
            ("Invert selection", ["-invertTrianglesSelection"]),
            ("Filter non-largest components", ["-removeSelectedTriangles"]),
            ("Clean model", ["-cleanModel"]),
            ("Smooth model", ["-smooth"]),
            ("Simplify model (pass 2)", simplify_args),
            ("Clean model (pass 2)", ["-cleanModel"]),
        ]

        total_steps = len(steps)

        try:
            for i, (step_name, cmd_args) in enumerate(steps, start=1):
                step_result = self._delegate_single_command(
                    step_name,
                    *cmd_args,
                    step_number=i,
                    total_steps=total_steps,
                )
                result.steps.append(step_result)

                if not step_result.success:
                    result.error_message = f"Step {i} ({step_name}) failed: {step_result.error_message}"
                    result.completed_at = datetime.now()
                    return result

            result.success = True
            result.completed_at = datetime.now()

            total_duration = (result.completed_at - result.started_at).total_seconds()
            self._log(f"Component completed in {total_duration:.1f}s", indent=0)

        except AbortedError:
            result.error_message = "Aborted by user"
            result.completed_at = datetime.now()
            raise

        return result

    def process_all(self) -> list[ComponentResult]:
        """
        Simplify all components found in the alignment directory.

        Returns:
            List of ComponentResult for each processed component
        """
        component_names = self.scan_component_names()

        if not component_names:
            self._log("No .rsalign files found in alignment directory.")
            self._log("Cannot determine component names to process.")
            return []

        self._log(f"Found {len(component_names)} component(s) to simplify:")
        for name in component_names:
            self._log(f"  - {name}")
        self._log("")

        if not self._verify_connection():
            self._log("ERROR: Could not communicate with RealityCapture.")
            self._log("Please ensure RealityCapture is running with the project open.")
            return []

        self._log("Connected to RealityCapture")
        self._clear_queue()
        self._log("")

        if self.test_mode:
            self._log("*** TEST MODE: Only processing first component ***")
            self._log("")
            component_names = component_names[:1]

        try:
            for i, component_name in enumerate(component_names):
                self._log("")
                self._log(f"[Component {i + 1}/{len(component_names)}]")

                result = self.simplify_component(component_name)
                self.results.append(result)

                if not result.success:
                    self._log("")
                    self._log("=" * 60)
                    self._log("FATAL ERROR: Processing failed. Halting.")
                    self._log(f"Error: {result.error_message}")
                    self._log("=" * 60)
                    break

        except AbortedError:
            self._log("")
            self._log("Processing aborted by user.")

        return self.results

    def generate_summary(self) -> str:
        """Generate a summary report of all processing."""
        if not self.results:
            return "No components were processed."

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "=" * 80,
            "RealityCapture Simplification Summary",
            "=" * 80,
            f"Report Generated: {timestamp}",
            f"Alignment Directory: {self.alignment_dir}",
            "",
        ]

        if self.simplify_params and self.simplify_params.exists():
            lines.append(f"Simplification Parameters: {self.simplify_params}")
            lines.append("")

        successful = sum(1 for r in self.results if r.success)
        failed = len(self.results) - successful

        lines.extend([
            f"Total Components: {len(self.results)}",
            f"Successful: {successful}",
            f"Failed: {failed}",
            "",
        ])

        if failed > 0:
            lines.append("*** PROCESSING INCOMPLETE - ERRORS OCCURRED ***")
            lines.append("")

        lines.append("-" * 80)
        lines.append("Component Details:")
        lines.append("-" * 80)

        for result in self.results:
            status = "SUCCESS" if result.success else "FAILED"
            duration = ""
            if result.completed_at:
                dur_sec = (result.completed_at - result.started_at).total_seconds()
                duration = f" ({dur_sec:.1f}s)"

            lines.append(f"\n{result.component_name}: {status}{duration}")

            if result.error_message:
                lines.append(f"  Error: {result.error_message}")

            if self.verbose and result.steps:
                lines.append("  Steps:")
                for step in result.steps:
                    step_status = "OK" if step.success else "FAIL"
                    lines.append(f"    [{step_status}] {step.step_name} ({step.duration_seconds:.1f}s)")

        lines.extend([
            "",
            "-" * 80,
            f"Report completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80,
        ])

        summary = "\n".join(lines)
        print(summary)
        return summary


def get_user_input() -> tuple[Path, Optional[Path], bool]:
    """Prompt user for settings."""
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_simplify_params = r"D:\NA168\Zeuss_NA168_H2080\simplificationParameters.xml"

    print("=" * 80)
    print("RealityCapture Simplify All Components")
    print("=" * 80)
    print()
    print("Pipeline per component (11 steps):")
    print("  1. Select component")
    print("  2. Simplify model")
    print("  3. Select large triangles (3.0x)")
    print("  4. Filter large triangles")
    print("  5. Select largest component")
    print("  6. Invert selection")
    print("  7. Filter non-largest components")
    print("  8. Clean model")
    print("  9. Smooth model")
    print("  10. Simplify model (pass 2)")
    print("  11. Clean model (pass 2)")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - Project with components must be open")
    print("  - Component names must match .rsalign file stems")
    print()

    # Alignment directory
    alignment_dir = Path(default_alignment_dir)
    if alignment_dir.exists():
        print(f"Alignment directory: {alignment_dir}")
    else:
        while True:
            align_input = input(f"Alignment directory [{default_alignment_dir}]: ").strip()
            if not align_input:
                align_input = default_alignment_dir
            alignment_dir = Path(align_input)
            if alignment_dir.exists():
                break
            print(f"Error: Directory not found: {alignment_dir}")

    # Simplification parameters
    simplify_params = Path(default_simplify_params)
    if simplify_params.exists():
        print(f"Simplification parameters: {simplify_params}")
    else:
        params_input = input(f"Simplification params XML (Enter to skip) [{default_simplify_params}]: ").strip()
        if params_input:
            simplify_params = Path(params_input)
            if not simplify_params.exists():
                print(f"Warning: File not found, proceeding without parameters")
                simplify_params = None
        else:
            simplify_params = None

    test_input = input("\nTest mode (only process first component)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()
    return alignment_dir, simplify_params, test_mode


def main():
    """Main entry point."""
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    processor = None

    try:
        alignment_dir, simplify_params, test_mode = get_user_input()

        processor = SimplifyProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            simplify_params=simplify_params,
            poll_interval=2.0,
            test_mode=test_mode,
            verbose=True,
        )

        results = processor.process_all()
        processor.generate_summary()

        successful = sum(1 for r in results if r.success)
        if successful > 0:
            print(f"\nSuccessfully simplified {successful} component(s).")
        else:
            print("\nNo components were successfully processed.")

        if results and all(r.success for r in results):
            sys.exit(0)
        else:
            sys.exit(2)

    except AbortedError:
        print("\nProcessing was aborted.")
        if processor:
            processor.generate_summary()
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupt received.")
        if processor:
            processor._request_abort()
            processor.generate_summary()
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        if processor:
            processor._request_abort()
        sys.exit(1)


if __name__ == "__main__":
    main()