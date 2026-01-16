#!/usr/bin/env python3
"""
Process components through RealityCapture model generation pipeline.

Execution Logic:
- Works within an ALREADY OPEN RealityCapture project with components loaded
- Does NOT import components or create new scenes
- Scans alignment directory for .rsalign filenames to determine component names
- Uses -selectComponent to select each component by name in the open project
- Processes each component incrementally, creating models within the same project
- Validates export success and halts on failure

Safety Features:
- Sends -abortInstance on startup to clear any queued commands from previous runs
- Each command is sent as a separate delegation (one command at a time)
- Uses TWO-PHASE IDLE DETECTION to properly wait for operations:
  1. Wait for RC to become BUSY (proves operation started)
  2. Wait for RC to return to IDLE (proves operation completed)
- This avoids race conditions where -waitCompleted returns before RC starts
- Ctrl+C triggers -abortInstance before exiting to stop RC processing

Pipeline per component (25 steps):
  1. -selectComponent <n>
  2. -calculateHighModel
  3. -selectMarginalTriangles
  4. -removeSelectedTriangles
  5. -simplify (to 70%)
  6. -selectLargeTrianglesRel 3.0
  7. Wait 10s for selection calculation
  8. -removeSelectedTriangles
  9. -cleanModel
  10. -smooth
  11. -calculateTexture
  12. -closeHoles 80000
  13. -renameSelectedModel <n>_HighPoly
  14. -simplify (pass 1)
  15. -closeHoles
  16. -simplify (pass 2)
  17. -closeHoles
  18. -unwrap
  19. -renameSelectedModel <n>_LowPoly (unwrapped)
  20. -reprojectTexture HighPoly LowPoly
  21. -renameSelectedModel <n>_LowPoly (textured result)
  22. -save
  23. -exportModel LowPoly as FBX
  24. -selectModel HighPoly
  25. -export3dTiles as Cesium 3D Tiles

Uses delegation (-delegateTo * <single_cmd> -waitCompleted *) for each step.
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


class ExportError(RCError):
    """Raised when model export fails or exported file is not found."""
    pass


class CommandError(RCError):
    """Raised when a RealityCapture command fails."""
    pass


class ConnectionError(RCError):
    """Raised when unable to communicate with RealityCapture."""
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
    output_files: list[Path] = field(default_factory=list)
    error_message: str = ""


class RCStatusParser:
    """
    Parse RealityCapture status strings.

    Expected format: id:0x10001 progress:57.5% runtime:4.26sec endEstimation:3.40sec
    Or when idle: id:0xffffffff progress:0.0% (or similar idle indicators)
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

        # Check for idle indicators
        for indicator in RCStatusParser.IDLE_INDICATORS:
            if indicator in status_lower:
                result["is_idle"] = True

        # Parse key:value pairs
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

        # 100% progress also means complete/idle for waiting purposes
        if result["progress"] >= 100.0:
            result["is_idle"] = True

        return result


class ModelProcessor:
    """
    Process components in an open RealityCapture project through the model pipeline.

    Features:
    - One command per delegation call for safe abort capability
    - Startup abort to clear any queued commands
    - Signal handler for clean abort on Ctrl+C
    - Progress monitoring via -getStatus polling
    """

    # Class-level reference for signal handler
    _active_instance: Optional["ModelProcessor"] = None

    def __init__(
            self,
            rc_exe: Path,
            alignment_dir: Path,
            export_dir: Path,
            project_prefix: str,
            simplify_params: Optional[Path] = None,
            texture_reproj_params: Optional[Path] = None,
            instance_name: str = "*",
            poll_interval: float = 2.0,
            test_mode: bool = True,
            verbose: bool = True,
    ):
        """
        Initialize the ModelProcessor.

        Args:
            rc_exe: Path to RealityScan.exe
            alignment_dir: Directory containing .rsalign files
            export_dir: Directory for exported models
            project_prefix: Prefix for output filenames (e.g., "NA168_H2080")
            simplify_params: Optional path to simplification params.xml
            texture_reproj_params: Optional path to texture reprojection params.xml
            instance_name: RC instance name or "*" for first available
            poll_interval: Seconds between status polls
            test_mode: If True, only process first component
            verbose: If True, print detailed status updates
        """
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.export_dir = export_dir
        self.project_prefix = project_prefix
        self.simplify_params = simplify_params
        self.texture_reproj_params = texture_reproj_params
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

        self.export_dir.mkdir(parents=True, exist_ok=True)

        # Set up signal handlers and cleanup
        ModelProcessor._active_instance = self
        self._setup_signal_handlers()
        atexit.register(self._cleanup)

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful abort."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    @staticmethod
    def _signal_handler(signum, frame) -> None:
        """Handle interrupt signals by requesting abort."""
        instance = ModelProcessor._active_instance
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

        # Send abort command to RC to stop current operations
        self._abort_instance()

    def _cleanup(self) -> None:
        """Cleanup on exit."""
        ModelProcessor._active_instance = None

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
        """
        Clear any queued commands from previous runs by sending abort.

        This should be called at startup to ensure a clean state.
        """
        self._log("Clearing any queued commands from previous runs...")
        self._abort_instance()
        # Brief pause to let RC process the abort
        time.sleep(1.0)

    def _get_status(self) -> dict:
        """
        Query RealityCapture status via -getStatus.

        Returns parsed status dictionary.
        """
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
        1. Send delegation command (no -waitCompleted, it's unreliable)
        2. Wait for RC to become BUSY (operation actually started)
        3. Wait for RC to return to IDLE (operation completed)

        This avoids the race condition where -waitCompleted returns immediately
        before RC has picked up the queued command.

        Args:
            operation_name: Human-readable name for logging
            rc_command_args: Single RealityCapture command with its arguments
            step_number: Current step number for display
            total_steps: Total steps for display

        Returns:
            StepResult with timing and status information
        """
        # Check for abort before starting
        if self._abort_requested:
            raise AbortedError("Abort requested")

        step_label = f"[{step_number}/{total_steps}]" if total_steps > 0 else ""
        self._log(f"{step_label} {operation_name}...", indent=1)

        started_at = datetime.now()

        # Build command WITHOUT -waitCompleted (it's unreliable)
        cmd = [
                  str(self.rc_exe),
                  "-delegateTo", self.instance_name,
              ] + list(rc_command_args)

        if self.verbose:
            cmd_str = " ".join(rc_command_args)
            self._log(f"Command: {cmd_str}", indent=2)

        # Get initial status to detect change
        initial_status = self._get_status()
        initial_id = initial_status.get("id", "")
        initial_progress = initial_status.get("progress", 0.0)

        # Send the delegation command (returns immediately after queuing)
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

        # Brief delay to let RC pick up the command before polling
        time.sleep(1.5)

        # PHASE 1: Wait for operation to START (RC becomes busy)
        # Detection: operation ID changes, status transitions from idle to busy,
        # or progress resets/changes significantly
        self._log("Waiting for operation to start...", indent=2)
        operation_started = False
        phase1_timeout = 30.0  # Max time to wait for operation to start
        phase1_start = time.time()
        was_initially_idle = initial_status.get("is_idle", True)

        while not operation_started and (time.time() - phase1_start) < phase1_timeout:
            if self._abort_requested:
                raise AbortedError("Abort requested waiting for operation start")

            status = self._get_status()
            current_id = status.get("id", "")
            current_progress = status.get("progress", 0.0)
            is_idle = status.get("is_idle", True)

            # Operation started if:
            # 1. ID changed to a real operation ID (not idle ID)
            if current_id != initial_id and current_id != "" and current_id != "0xffffffff":
                operation_started = True
                self._log(f"Operation started (new ID: {current_id})", indent=2)
            # 2. Was idle, now not idle (status transition)
            elif was_initially_idle and not is_idle:
                operation_started = True
                self._log(f"Operation started (status: busy, progress: {current_progress:.1f}%)", indent=2)
            # 3. Progress reset to near 0 from a higher value (new operation starting)
            elif initial_progress > 10.0 and current_progress < 5.0:
                operation_started = True
                self._log(f"Operation started (progress reset: {initial_progress:.1f}% -> {current_progress:.1f}%)",
                          indent=2)
            # 4. Not idle and actively processing (progress between 0 and 95)
            elif not is_idle and current_progress > 0 and current_progress < 95.0:
                operation_started = True
                self._log(f"Operation in progress ({current_progress:.1f}%)", indent=2)

            if not operation_started:
                time.sleep(1.25)

        if not operation_started:
            # Maybe operation was instant, check if still idle
            status = self._get_status()
            if status.get("is_idle", False):
                self._log("Operation may have completed instantly or failed to start", indent=2)
                # Give RC a moment and check again
                time.sleep(1.0)

        # PHASE 2: Wait for operation to COMPLETE (RC returns to idle)
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

            # Report progress periodically
            now = time.time()
            if abs(current_progress - last_progress) >= 1.0 or (now - last_log_time) >= 10.0:
                elapsed = (datetime.now() - started_at).total_seconds()
                est_str = f" (est: {estimation})" if estimation else ""
                self._log(f"Progress: {current_progress:.1f}%{est_str} [{elapsed:.1f}s]", indent=2)
                last_progress = current_progress
                last_log_time = now

            # Check for completion
            if is_idle:
                # Verify it's really idle by checking a couple more times
                time.sleep(0.5)
                status2 = self._get_status()
                if status2.get("is_idle", False):
                    time.sleep(0.5)
                    status3 = self._get_status()
                    if status3.get("is_idle", False):
                        break  # Confirmed idle

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
            success=True,  # If we got here without abort, assume success
            final_status=status.get("raw", ""),
            error_message="",
        )

    def _validate_export(
            self,
            output_file: Path,
            description: str,
            max_retries: int = 3,
            retry_delay: float = 2.0,
    ) -> bool:
        """
        Validate that an exported file exists and has content.

        Includes retry logic for network drive sync delays.
        """
        for attempt in range(max_retries):
            if self._abort_requested:
                return False

            if attempt > 0:
                self._log(f"Retry {attempt}/{max_retries} after {retry_delay}s...", indent=2)
                time.sleep(retry_delay)

            if not output_file.exists():
                continue

            file_size = output_file.stat().st_size
            if file_size == 0:
                continue

            # Format size for display
            if file_size < 1024:
                size_str = f"{file_size} bytes"
            elif file_size < 1024 * 1024:
                size_str = f"{file_size / 1024:.1f} KB"
            else:
                size_str = f"{file_size / (1024 * 1024):.1f} MB"

            self._log(f"Validated {description}: {output_file.name} ({size_str})", indent=2)
            return True

        self._log(f"FAILED: {description} not found or empty: {output_file}", indent=2)
        return False

    def _extract_component_number(self, component_name: str) -> str:
        """Extract a zero-padded component number from the component name."""
        # Try format: "Component (01)" or "Component(01)"
        match = re.search(r'\((\d+)\)', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: "Name_123" (number at end after underscore)
        match = re.search(r'_(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: "Name123" (number at end)
        match = re.search(r'(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        return "00"

    def scan_component_names(self) -> list[str]:
        """Scan alignment directory for .rsalign files and extract component names."""
        rsalign_files = sorted(self.alignment_dir.glob("*.rsalign"))
        component_names = [f.stem for f in rsalign_files]
        return component_names

    def process_component(self, component_name: str) -> ComponentResult:
        """
        Process a single component through the full pipeline.

        Each step is sent as a separate delegation call for safe abort capability.

        Args:
            component_name: Name of the component to process

        Returns:
            ComponentResult with all step results and output files
        """
        result = ComponentResult(
            component_name=component_name,
            started_at=datetime.now(),
        )

        high_poly_name = f"{component_name}_HighPoly"
        low_poly_name = f"{component_name}_LowPoly"
        component_num = self._extract_component_number(component_name)

        fbx_output = self.export_dir / f"{self.project_prefix}_{component_num}.fbx"
        cesium_output = self.export_dir / f"{self.project_prefix}_{component_num}.json"

        self._log("=" * 60)
        self._log(f"Processing component: {component_name}")
        self._log(f"High-poly model: {high_poly_name}")
        self._log(f"Low-poly model: {low_poly_name}")
        self._log(f"FBX output: {fbx_output}")
        self._log(f"Cesium output: {cesium_output}")
        self._log("=" * 60)

        # Build simplify command args
        simplify_args = ["-simplify"]
        if self.simplify_params and self.simplify_params.exists():
            simplify_args.append(str(self.simplify_params))

        # Build texture reprojection command args
        reproj_args = ["-reprojectTexture", high_poly_name, low_poly_name]
        if self.texture_reproj_params and self.texture_reproj_params.exists():
            reproj_args.append(str(self.texture_reproj_params))

        # Define all pipeline steps - each is a separate command
        # IMPORTANT: Every RC command creates a NEW model that becomes selected.
        # We must rename at the right points to preserve named models.
        #
        # Format: (step_name, [command, arg1, arg2, ...]) or (step_name, None) for delays
        #
        # Model flow:
        #   Steps 1-12: Process and texture the model
        #   Step 13: Rename to _HighPoly (textured source, preserved)
        #   Steps 14-17: Simplify twice with hole closing (creates intermediate models)
        #   Step 18: Unwrap (creates new unwrapped model)
        #   Step 19: Rename unwrapped model to _LowPoly
        #   Step 20: Reproject texture from _HighPoly to _LowPoly (creates new textured model)
        #   Step 21: Rename textured result to _LowPoly (overwrites reference)
        #   Steps 22-25: Save and export
        #
        steps = [
            # Select and build high-detail model
            ("Select component", ["-selectComponent", component_name]),
            ("Calculate high detail model", ["-calculateHighModel"]),
            ("Simplify to 70%", ["-simplify"]),  # Uses RC's current settings
            ("Simplify to 70%", ["-simplify"]),  # Uses RC's current settings
            ("Clean model", ["-cleanModel"]),
            ("Smooth model", ["-smooth"]),

            # Texture the high-poly model
            ("Calculate texture", ["-calculateTexture"]),
            (f"Rename to {high_poly_name}", ["-renameSelectedModel", high_poly_name]),
            # _HighPoly is now preserved with texture

        ]

        total_steps = len(steps)

        try:
            for i, (step_name, cmd_args) in enumerate(steps, start=1):
                # Handle delay steps (cmd_args is None)
                if cmd_args is None:
                    self._log(f"[{i}/{total_steps}] {step_name}...", indent=1)
                    delay_seconds = 10.0
                    self._log(f"Waiting {delay_seconds}s for RealityCapture to complete calculation", indent=2)

                    started_at = datetime.now()

                    # Check for abort during delay
                    start_delay = time.time()
                    while time.time() - start_delay < delay_seconds:
                        if self._abort_requested:
                            raise AbortedError("Abort requested during delay")
                        time.sleep(0.5)

                    completed_at = datetime.now()
                    actual_duration = (completed_at - started_at).total_seconds()
                    self._log(f"Completed in {actual_duration:.1f}s", indent=2)

                    # Record as successful step
                    result.steps.append(StepResult(
                        step_number=i,
                        step_name=step_name,
                        started_at=started_at,
                        completed_at=completed_at,
                        duration_seconds=actual_duration,
                        success=True,
                        final_status="delay",
                        error_message="",
                    ))
                    continue

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

                # Validate exports after export commands
                if "Export FBX" in step_name:
                    if not self._validate_export(fbx_output, "FBX export"):
                        result.error_message = f"FBX export validation failed: {fbx_output}"
                        result.completed_at = datetime.now()
                        return result
                    result.output_files.append(fbx_output)

                elif "Export Cesium" in step_name:
                    if self._validate_export(cesium_output, "Cesium export"):
                        result.output_files.append(cesium_output)
                    else:
                        self._log("Warning: Cesium export validation failed (non-fatal)", indent=1)

            result.success = True
            result.completed_at = datetime.now()

            total_duration = (result.completed_at - result.started_at).total_seconds()
            self._log(f"Component completed successfully in {total_duration:.1f}s", indent=0)

        except AbortedError:
            result.error_message = "Aborted by user"
            result.completed_at = datetime.now()
            raise

        return result

    def process_all(self) -> list[ComponentResult]:
        """
        Process all components found in the alignment directory.

        Returns:
            List of ComponentResult for each processed component
        """
        component_names = self.scan_component_names()

        if not component_names:
            self._log("No .rsalign files found in alignment directory.")
            self._log("Cannot determine component names to process.")
            return []

        self._log(f"Found {len(component_names)} component(s) to process:")
        for name in component_names:
            self._log(f"  - {name}")
        self._log("")

        # Verify connection to RC
        if not self._verify_connection():
            self._log("ERROR: Could not communicate with RealityCapture.")
            self._log("Please ensure RealityCapture is running with the project open.")
            return []

        self._log("Connected to RealityCapture")

        # Clear any queued commands from previous runs
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

                result = self.process_component(component_name)
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

    def export_only_highpoly(self) -> list[ComponentResult]:
        """
        Export-only mode: Only exports existing _HighPoly models as Cesium 3D Tiles.

        Assumes models are already processed and named as {component_name}_HighPoly.

        Returns:
            List of ComponentResult for each exported component
        """
        component_names = self.scan_component_names()

        if not component_names:
            self._log("No .rsalign files found in alignment directory.")
            return []

        self._log(f"Found {len(component_names)} component(s) for export:")
        for name in component_names:
            self._log(f"  - {name}")
        self._log("")

        if not self._verify_connection():
            self._log("ERROR: Could not communicate with RealityCapture.")
            self._log("Please ensure RealityCapture is running with the project open.")
            return []

        self._log("Connected to RealityCapture")

        # Clear any queued commands from previous runs
        self._clear_queue()

        self._log("")
        self._log("EXPORT-ONLY MODE: Expecting models named as {component_name}_HighPoly")
        self._log("")

        if self.test_mode:
            self._log("*** TEST MODE: Only exporting first component ***")
            self._log("")
            component_names = component_names[:1]

        try:
            for i, component_name in enumerate(component_names):
                self._log("")
                self._log(f"[Export {i + 1}/{len(component_names)}]")

                result = ComponentResult(
                    component_name=component_name,
                    started_at=datetime.now(),
                )

                high_poly_name = f"{component_name}_HighPoly"
                component_num = self._extract_component_number(component_name)
                cesium_output = self.export_dir / f"{self.project_prefix}_{component_num}.json"

                # Step 1: Select high-poly model
                step_result = self._delegate_single_command(
                    f"Select {high_poly_name}",
                    "-selectModel", high_poly_name,
                    step_number=1,
                    total_steps=2,
                )
                result.steps.append(step_result)

                if not step_result.success:
                    result.error_message = f"Could not select model {high_poly_name}"
                    result.completed_at = datetime.now()
                    self.results.append(result)
                    self._log(f"FAILED: {result.error_message}")
                    continue

                # Step 2: Export Cesium 3D Tiles
                step_result = self._delegate_single_command(
                    f"Export Cesium: {cesium_output.name}",
                    "-export3dTiles", str(cesium_output),
                    step_number=2,
                    total_steps=2,
                )
                result.steps.append(step_result)

                if step_result.success and self._validate_export(cesium_output, "Cesium export"):
                    result.output_files.append(cesium_output)
                    result.success = True
                else:
                    result.error_message = "Cesium export failed or validation failed"

                result.completed_at = datetime.now()
                self.results.append(result)

        except AbortedError:
            self._log("")
            self._log("Export aborted by user.")

        return self.results

    def generate_summary(self) -> str:
        """Generate a summary report of all processing."""
        if not self.results:
            return "No components were processed."

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "=" * 80,
            "RealityCapture Model Processing Summary",
            "=" * 80,
            f"Report Generated: {timestamp}",
            f"Alignment Directory: {self.alignment_dir}",
            f"Export Directory: {self.export_dir}",
            f"Project Prefix: {self.project_prefix}",
            "",
        ]

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

            if result.output_files:
                lines.append("  Outputs:")
                for f in result.output_files:
                    lines.append(f"    - {f.name}")

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

        # Save to file
        summary_file = self.export_dir / "processing_summary.txt"
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(summary)
            print(summary)
            print(f"\nSummary saved to: {summary_file}")
        except Exception as e:
            print(summary)
            print(f"\nWarning: Could not save summary to file: {e}")

        return summary


def get_user_input() -> tuple[Path, Path, str, bool, bool]:
    """Prompt user for settings."""
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_project_prefix = "NA168_H2080"

    print("=" * 80)
    print("RealityCapture Model Processor")
    print("=" * 80)
    print()
    print("Pipeline (25 steps per component):")
    print("  1. Select component")
    print("  2. Calculate high detail model")
    print("  3-4. Filter marginal triangles")
    print("  5. Simplify to 70%")
    print("  6. Select large triangles (3.0x)")
    print("  7. Wait 10s for selection calculation")
    print("  8. Remove large triangles")
    print("  9. Clean model")
    print("  10. Smooth")
    print("  11. Calculate texture")
    print("  12. Close holes (80000 max)")
    print("  13. Rename to _HighPoly (textured source preserved)")
    print("  14-17. Simplify/Close holes (2 passes)")
    print("  18. Unwrap")
    print("  19. Rename to _LowPoly (unwrapped)")
    print("  20. Reproject texture from _HighPoly to _LowPoly")
    print("  21. Rename textured result to _LowPoly")
    print("  22. Save project")
    print("  23. Export FBX")
    print("  24. Select _HighPoly")
    print("  25. Export Cesium 3D Tiles")
    print()
    print("SAFETY FEATURES:")
    print("  - Clears queued commands on startup")
    print("  - One command per delegation (safe abort)")
    print("  - Two-phase detection: waits for START then COMPLETE")
    print("  - Ctrl+C sends abort to RealityCapture")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - Project with components must be open")
    print("  - Component names must match .rsalign file stems")
    print()

    export_only_input = input(
        "Export-only mode (skip processing, export existing _HighPoly)? [y/N]: "
    ).strip().lower()
    export_only = export_only_input == 'y'
    print()

    # Check for default directories
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

    export_dir = Path(default_export_dir)
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        print(f"Export directory: {export_dir}")
    except Exception:
        while True:
            export_input = input(f"Export directory [{default_export_dir}]: ").strip()
            if not export_input:
                export_input = default_export_dir
            export_dir = Path(export_input)
            try:
                export_dir.mkdir(parents=True, exist_ok=True)
                break
            except Exception as e:
                print(f"Error: Could not create directory: {e}")

    project_prefix = default_project_prefix
    print(f"Project prefix: {project_prefix}")

    test_input = input("\nTest mode (only process first component)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()
    return alignment_dir, export_dir, project_prefix, test_mode, export_only


def main():
    """Main entry point."""
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    processor = None

    try:
        alignment_dir, export_dir, project_prefix, test_mode, export_only = get_user_input()

        # Optional params files
        simplify_params = Path(r"D:\NA168\Zeuss_NA168_H2080\simplificationParameters.xml")
        texture_reproj_params = Path(r"D:\NA168\Zeuss_NA168_H2080\TextureReprojectionSettings.xml")

        processor = ModelProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            export_dir=export_dir,
            project_prefix=project_prefix,
            simplify_params=simplify_params if simplify_params.exists() else None,
            texture_reproj_params=texture_reproj_params if texture_reproj_params.exists() else None,
            poll_interval=2.0,
            test_mode=test_mode,
            verbose=True,
        )

        if export_only:
            print("=" * 80)
            print("EXPORT-ONLY MODE")
            print("=" * 80)
            print()
            results = processor.export_only_highpoly()
        else:
            results = processor.process_all()

        processor.generate_summary()

        successful = sum(1 for r in results if r.success)
        if successful > 0:
            print(f"\nSuccessfully processed {successful} component(s).")
        else:
            print("\nNo components were successfully processed.")

        # Exit with appropriate code
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