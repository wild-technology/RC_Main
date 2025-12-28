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

Per-component workflow:
  1. -selectComponent <name> — Select component by name
  2. -calculateNormalModel — Generate mesh (no region restriction)
  3. -selectLargeTrianglesRel + filter — Remove oversized triangles
  4. -selectLargestModelComponent + invert + filter — Keep largest connected mesh
  5. -cleanModel — Fix geometry issues
  6. -smooth — Smooth surface
  7. -calculateTexture — Generate texture
  8. -renameSelectedModel — Name as <name>_Model
  9. -save — Save project
  10. -selectModel + -exportModel — Export as FBX for Unreal Engine
  11. Validate export exists — HALT if missing

Uses delegation (-delegateTo *) to communicate with running RealityCapture instance.
"""

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


class ExportError(Exception):
    """Raised when model export fails or exported file is not found."""
    pass


class ModelProcessor:
    """
    Process components in an open RealityCapture project through the model pipeline.

    Delegates all commands to an already-running RealityCapture instance.
    Works with components already loaded in the project.
    """

    def __init__(
            self,
            rc_exe: Path,
            alignment_dir: Path,
            export_dir: Path,
            project_prefix: str,
            poll_interval: float = 2.0,
            test_mode: bool = True,
    ):
        """
        Initialize the model processor.

        Args:
            rc_exe: Path to RealityScan.exe (for delegation commands only)
            alignment_dir: Directory containing .rsalign files (used to derive component names)
            export_dir: Directory where models will be exported
            project_prefix: Prefix for FBX export filenames (e.g., "NA165_H2060")
            poll_interval: Seconds between status checks
            test_mode: If True, only process the first component
        """
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.export_dir = export_dir
        self.project_prefix = project_prefix
        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.process_log: list[dict[str, str]] = []

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.alignment_dir.exists():
            raise FileNotFoundError(f"Alignment directory not found: {self.alignment_dir}")

        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _delegate(self, *args: str) -> subprocess.CompletedProcess:
        """
        Send delegation command to running RealityCapture instance.

        Uses: RealityScan.exe -delegateTo * <commands>

        Does NOT start a new instance. Requires RC to already be running.

        Args:
            *args: Command arguments to delegate

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-delegateTo", "*"] + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    def _get_status(self) -> Optional[str]:
        """
        Query RealityCapture status.

        Uses: RealityScan.exe -getStatus *

        Returns:
            Status string or None if query failed
        """
        cmd = [str(self.rc_exe), "-getStatus", "*"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None

    def _wait_completed(self) -> subprocess.CompletedProcess:
        """
        Wait for current process to complete.

        Uses: RealityScan.exe -waitCompleted *

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-waitCompleted", "*"]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    def _parse_status(self, status: Optional[str]) -> dict:
        """
        Parse status string into components.

        Returns:
            Dictionary with parsed values
        """
        result = {}
        if not status:
            return result

        parts = status.split()
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                result[key] = value

        return result

    def _is_idle(self, status: Optional[str] = None) -> bool:
        """
        Check if RealityCapture is idle.

        Args:
            status: Optional pre-fetched status string

        Returns:
            True if idle, False if busy
        """
        if status is None:
            status = self._get_status()
        if not status:
            return False

        parsed = self._parse_status(status)
        status_lower = status.lower()

        if "idle" in status_lower:
            return True

        progress = parsed.get('progress', '')
        if progress in ('100.0%', '100%'):
            return True

        op_id = parsed.get('id', '')
        if op_id == '0xffffffff' and progress in ('0.0%', '0%'):
            return True

        return False

    def _wait_until_idle(self, operation_name: str = "operation", timeout: float = 33600.0) -> None:
        """
        Wait until RealityCapture reports idle status.

        Args:
            operation_name: Name of operation for logging
            timeout: Maximum seconds to wait
        """
        print(f"    Waiting for {operation_name}...", end=" ", flush=True)

        self._wait_completed()
        time.sleep(0.5)

        status = self._get_status()
        if self._is_idle(status):
            print("done")
            return

        start_time = time.time()
        last_progress = None

        while time.time() - start_time < timeout:
            status = self._get_status()
            parsed = self._parse_status(status)
            progress = parsed.get('progress', '')

            if progress and progress != last_progress:
                print(f"{progress}", end=" ", flush=True)
                last_progress = progress

            if self._is_idle(status):
                elapsed = time.time() - start_time
                print(f"done ({elapsed:.1f}s)")
                return

            time.sleep(self.poll_interval)

        print(f"timeout after {timeout}s")
        raise TimeoutError(f"Operation '{operation_name}' timed out after {timeout} seconds")

    def _run_command(self, operation_name: str, *args: str) -> bool:
        """
        Run a command and wait for completion.

        Args:
            operation_name: Name for logging
            *args: Command arguments

        Returns:
            True if command completed successfully
        """
        self._delegate(*args)
        self._wait_until_idle(operation_name)
        return True

    def _keep_largest_part(self) -> None:
        """
        Select largest model component, invert selection, filter out smaller parts.
        """
        print("         Selecting largest component...")
        self._run_command("select largest", "-selectLargestModelComponent")

        print("         Inverting selection...")
        self._run_command("invert selection", "-invertTrianglesSelection")

        print("         Filtering selection...")
        self._run_command("filter", "-removeSelectedTriangles")

    def _validate_export(self, output_file: Path, component_name: str) -> None:
        """
        Validate that the exported model file exists and has content.

        Args:
            output_file: Path to the expected export file
            component_name: Name of the component being processed

        Raises:
            ExportError: If the file doesn't exist or is empty
        """
        if not output_file.exists():
            raise ExportError(
                f"Export FAILED for component '{component_name}': "
                f"Output file not found at {output_file}"
            )

        file_size = output_file.stat().st_size
        if file_size == 0:
            raise ExportError(
                f"Export FAILED for component '{component_name}': "
                f"Output file is empty (0 bytes) at {output_file}"
            )

        if file_size < 1024:
            size_str = f"{file_size} bytes"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"

        print(f"    Export validated: {output_file.name} ({size_str})")

    def _extract_component_number(self, component_name: str) -> str:
        """
        Extract the component number from the component name.

        Expects component names like "Component (01)", "Component (02)", etc.
        Returns zero-padded 2-digit number.

        Args:
            component_name: Name of the component

        Returns:
            Zero-padded component number (e.g., "01", "02")
        """
        import re
        match = re.search(r'\((\d+)\)', component_name)
        if match:
            num = int(match.group(1))
            return f"{num:02d}"
        return "00"

    def scan_component_names(self) -> list[str]:
        """
        Scan alignment directory for .rsalign files and extract component names.

        The component name in RC is typically the stem of the .rsalign filename.

        Returns:
            List of component names, sorted alphabetically
        """
        rsalign_files = sorted(self.alignment_dir.glob("*.rsalign"))
        component_names = [f.stem for f in rsalign_files]
        return component_names

    def process_component(self, component_name: str) -> Path:
        """
        Process a single component through the full pipeline.

        The component must already exist in the open RealityCapture project.

        Args:
            component_name: Name of the component in RC (matches .rsalign stem)

        Returns:
            Path to the exported model file

        Raises:
            ExportError: If export fails or file is not created
        """
        print(f"\n{'=' * 60}")
        print(f"Processing component: {component_name}")
        print(f"{'=' * 60}")

        model_name = f"{component_name}_Model"
        component_num = self._extract_component_number(component_name)
        output_file = self.export_dir / f"{self.project_prefix}_{component_num}.fbx"

        # 1. Select the component by name
        print("\n  [1/10] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # 2. Calculate normal model (no region restriction)
        print("\n  [2/10] Calculating normal model...")
        self._run_command("model calculation", "-calculateNormalModel")

        # 3. Select large triangles and filter
        print("\n  [3/10] Filtering large triangles...")
        self._run_command("select large triangles", "-selectLargeTrianglesRel", "2.0")
        self._run_command("filter", "-removeSelectedTriangles")

        # 4. Keep largest connected part
        print("\n  [4/10] Keeping largest connected part...")
        self._keep_largest_part()

        # 5. Clean model
        print("\n  [5/10] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 6. Smooth
        print("\n  [6/10] Smoothing...")
        self._run_command("smooth", "-smooth")

        # 7. Calculate texture
        print("\n  [7/10] Calculating texture...")
        self._run_command("texture", "-calculateTexture")

        # 8. Rename model
        print(f"\n  [8/10] Renaming to {model_name}...")
        self._run_command("rename model", "-renameSelectedModel", model_name)

        # 9. Save project
        print("\n  [9/10] Saving project...")
        self._run_command("save", "-save")

        # 10. Export as FBX for Unreal Engine
        print(f"\n  [10/10] Exporting FBX for Unreal Engine as {output_file.name}...")
        self._run_command("export", "-selectModel", model_name)
        self._run_command("export", "-exportModel", "fbx", "unrealEngine", str(output_file))

        self._validate_export(output_file, component_name)

        return output_file

    def process_all(self) -> list[Path]:
        """
        Process all components found in the alignment directory.

        Components must already be loaded in the open RealityCapture project.
        If test_mode is True, only processes the first component.

        Processing STOPS if any component fails to export.

        Returns:
            List of successfully exported model paths

        Raises:
            ExportError: If any export fails (processing halts)
        """
        component_names = self.scan_component_names()

        if not component_names:
            print("No .rsalign files found in alignment directory.")
            print("Cannot determine component names to process.")
            return []

        print(f"Found {len(component_names)} component(s) to process:")
        for name in component_names:
            print(f"  - {name}")
        print()

        status = self._get_status()
        if not status:
            print("Error: Could not communicate with RealityCapture.")
            print("Please ensure RealityCapture is already running with the project open.")
            return []

        print(f"Connected to RealityCapture. Status: {status}")
        print()
        print("IMPORTANT: Ensure the project with these components is already open in RC.")
        print()

        if self.test_mode:
            print("*** TEST MODE: Only processing first component ***\n")
            component_names = component_names[:1]

        exported_models: list[Path] = []

        for i, component_name in enumerate(component_names):
            print(f"\n[{i + 1}/{len(component_names)}] Processing component: {component_name}")

            try:
                output_file = self.process_component(component_name)
                exported_models.append(output_file)
                component_num = self._extract_component_number(component_name)
                self.process_log.append({
                    "component": component_name,
                    "output": f"{self.project_prefix}_{component_num}.fbx",
                    "status": "success",
                })

            except ExportError as e:
                component_num = self._extract_component_number(component_name)
                self.process_log.append({
                    "component": component_name,
                    "output": f"{self.project_prefix}_{component_num}.fbx",
                    "status": "FAILED",
                })

                self.generate_summary()

                print(f"\n{'=' * 60}")
                print("FATAL ERROR: Export failed. Processing halted.")
                print(f"{'=' * 60}")
                raise

        return exported_models

    def generate_summary(self) -> None:
        """
        Generate and save a summary of processing.
        """
        if not self.process_log:
            print("\nNo components were processed.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_file = self.export_dir / "processing_summary.txt"

        successful = sum(1 for entry in self.process_log if entry['status'] == 'success')
        failed = sum(1 for entry in self.process_log if entry['status'] == 'FAILED')

        summary_lines = [
            "=" * 80,
            "RealityCapture Model Processing Summary",
            "=" * 80,
            f"Processing Date/Time: {timestamp}",
            f"Alignment Directory: {self.alignment_dir}",
            f"Export Directory: {self.export_dir}",
            f"Project Prefix: {self.project_prefix}",
            f"Export Format: FBX for Unreal Engine",
            f"Total Processed: {len(self.process_log)}",
            f"Successful: {successful}",
            f"Failed: {failed}",
            "",
        ]

        if failed > 0:
            summary_lines.append("*** PROCESSING HALTED DUE TO EXPORT FAILURE ***")
            summary_lines.append("")

        summary_lines.extend([
            "-" * 80,
            "Processing Details:",
            "-" * 80,
            f"{'Component Name':<40} {'Output File':<30} {'Status':<10}",
            "-" * 80,
        ])

        for entry in self.process_log:
            summary_lines.append(
                f"{entry['component']:<40} {entry['output']:<30} {entry['status']:<10}"
            )

        summary_lines.extend([
            "-" * 80,
            "",
            "Processing completed." if failed == 0 else "Processing incomplete due to error.",
            "=" * 80,
        ])

        summary_text = "\n".join(summary_lines)

        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(summary_text)

        print(f"\n{summary_text}")
        print(f"\nSummary saved to: {summary_file}")


def get_user_input() -> tuple[Path, Path, str, bool]:
    """
    Prompt user for settings.

    Returns:
        Tuple of (alignment_dir, export_dir, project_prefix, test_mode)
    """
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"C:\Users\jonat\OneDrive\Desktop\NA165_H2060\models"
    default_project_prefix = "NA165_H2060"

    print("=" * 80)
    print("RealityCapture Model Processor - FBX Export for Unreal Engine")
    print("=" * 80)
    print()
    print("This script processes components already loaded in an open RC project.")
    print("It uses .rsalign filenames in the alignment directory to identify")
    print("which components to process by name.")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - A project with the components must be open")
    print("  - Component names must match the .rsalign file stems")
    print()
    print("NOTE: Processing will STOP if any export fails.")
    print()

    while True:
        align_input = input(f"Alignment directory (.rsalign files) [{default_alignment_dir}]: ").strip()
        if not align_input:
            align_input = default_alignment_dir
        alignment_dir = Path(align_input)
        if alignment_dir.exists():
            break
        else:
            print(f"Error: Directory not found: {alignment_dir}")
            print()

    while True:
        export_input = input(f"Export directory for FBX models [{default_export_dir}]: ").strip()
        if not export_input:
            export_input = default_export_dir
        export_dir = Path(export_input)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            break
        except Exception as e:
            print(f"Error: Could not create directory: {e}")
            print()

    prefix_input = input(f"Project prefix for FBX filenames [{default_project_prefix}]: ").strip()
    if not prefix_input:
        prefix_input = default_project_prefix
    project_prefix = prefix_input

    test_input = input("Test mode (only process first component)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()
    return alignment_dir, export_dir, project_prefix, test_mode


def main():
    """
    Main entry point.
    """
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    try:
        alignment_dir, export_dir, project_prefix, test_mode = get_user_input()

        processor = ModelProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            export_dir=export_dir,
            project_prefix=project_prefix,
            poll_interval=2.0,
            test_mode=test_mode,
        )

        exported = processor.process_all()

        processor.generate_summary()

        if exported:
            print(f"\nSuccessfully exported {len(exported)} FBX model(s).")
        else:
            print("\nNo models were exported.")

    except ExportError as e:
        print(f"\nExport Error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n\nProcessing cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()