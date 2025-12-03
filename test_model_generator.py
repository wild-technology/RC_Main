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
  2. -setReconstructionRegionAuto — Define reconstruction bounds
  3. -calculateNormalModel — Generate mesh
  4. -selectLargeTrianglesRel + filter — Remove oversized triangles
  5. -selectLargestModelComponent + invert + filter — Keep largest connected mesh
  6. -cleanModel — Fix geometry issues
  7. -smooth — Smooth surface
  8. -calculateTexture — Generate texture on high-poly
  9. -renameSelectedModel — Preserve as <name>_HighPoly
  10. -simplify (XML) — First percentage reduction
  11. Keep largest part + -cleanModel
  12. -simplify — Second percentage reduction
  13. Keep largest part + -cleanModel
  14. -renameSelectedModel — Name as <name>_LowPoly
  15. -unwrap — UV unwrap
  16. -reprojectTexture — Transfer texture from HighPoly to LowPoly
  17. -selectModel + -exportSelectedModel — Export final .obj
  18. Validate export exists — HALT if missing

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
            simplify_params: Path,
            texture_reproj_params: Optional[Path] = None,
            poll_interval: float = 2.0,
            test_mode: bool = True,
    ):
        """
        Initialize the model processor.

        Args:
            rc_exe: Path to RealityScan.exe (for delegation commands only)
            alignment_dir: Directory containing .rsalign files (used to derive component names)
            export_dir: Directory where models will be exported
            simplify_params: Path to simplification params XML (percentage-based)
            texture_reproj_params: Optional path to texture reprojection params XML
            poll_interval: Seconds between status checks
            test_mode: If True, only process the first component
        """
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.export_dir = export_dir
        self.simplify_params = simplify_params
        self.texture_reproj_params = texture_reproj_params
        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.process_log: list[dict[str, str]] = []

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.alignment_dir.exists():
            raise FileNotFoundError(f"Alignment directory not found: {self.alignment_dir}")

        if not self.simplify_params.exists():
            raise FileNotFoundError(f"Simplification params not found: {self.simplify_params}")

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

    def _wait_until_idle(self, operation_name: str = "operation", timeout: float = 3600.0) -> None:
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

        # Report file size
        if file_size < 1024:
            size_str = f"{file_size} bytes"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"

        print(f"    Export validated: {output_file.name} ({size_str})")

    def _get_obj_stats(self, obj_path: Path) -> dict:
        """
        Parse an OBJ file to get vertex and triangle counts.

        Args:
            obj_path: Path to the OBJ file

        Returns:
            Dictionary with 'vertices' and 'triangles' counts
        """
        vertices = 0
        faces = 0

        try:
            with open(obj_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if line.startswith('v '):
                        vertices += 1
                    elif line.startswith('f '):
                        faces += 1
        except Exception as e:
            print(f"    Warning: Could not parse OBJ stats: {e}")
            return {"vertices": 0, "triangles": 0}

        return {"vertices": vertices, "triangles": faces}

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

        high_poly_name = f"{component_name}_HighPoly"
        low_poly_name = f"{component_name}_LowPoly"
        output_file = self.export_dir / f"{component_name}.obj"

        # 1. Select the component by name
        print("\n  [1/20] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # 2. Set reconstruction region automatically
        print("\n  [2/20] Setting reconstruction region...")
        self._run_command("set region", "-setReconstructionRegionAuto")

        # 3. Calculate normal model
        print("\n  [3/20] Calculating normal model...")
        self._run_command("model calculation", "-calculateNormalModel")

        # 4. Select large triangles and filter
        print("\n  [4/20] Filtering large triangles...")
        self._run_command("select large triangles", "-selectLargeTrianglesRel", "2.0")
        self._run_command("filter", "-removeSelectedTriangles")

        # 5. Keep largest connected part
        print("\n  [5/20] Keeping largest connected part...")
        self._keep_largest_part()

        # 6. Clean model
        print("\n  [6/20] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 7. Smooth
        print("\n  [7/20] Smoothing...")
        self._run_command("smooth", "-smooth")

        # 8. Calculate texture on high-poly
        print("\n  [8/20] Calculating texture (high-poly)...")
        self._run_command("texture", "-calculateTexture")

        # 9. Rename to preserve as HighPoly
        print(f"\n  [9/20] Renaming to {high_poly_name}...")
        self._run_command("rename high-poly", "-renameSelectedModel", high_poly_name)

        # 10. First simplification (percentage-based via params.xml)
        print("\n  [10/20] Simplifying (first pass)...")
        self._run_command("simplify", "-simplify", str(self.simplify_params))

        # 11. Keep largest part after first simplification
        print("\n  [11/20] Keeping largest part...")
        self._keep_largest_part()

        # 12. Clean model
        print("\n  [12/20] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 13. Second simplification
        print("\n  [13/20] Simplifying (second pass)...")
        self._run_command("simplify", "-simplify", str(self.simplify_params))

        # 14. Keep largest part after second simplification
        print("\n  [14/20] Keeping largest part...")
        self._keep_largest_part()

        # 15. Clean model
        print("\n  [15/20] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 16. Rename to LowPoly
        print(f"\n  [16/20] Renaming to {low_poly_name}...")
        self._run_command("rename low-poly", "-renameSelectedModel", low_poly_name)

        # 17. Unwrap the low-poly model
        print("\n  [17/20] Unwrapping...")
        self._run_command("unwrap", "-unwrap")

        # 18. Reproject texture from HighPoly to LowPoly
        print(f"\n  [18/20] Reprojecting texture from {high_poly_name} to {low_poly_name}...")
        if self.texture_reproj_params and self.texture_reproj_params.exists():
            self._run_command(
                "reproject texture",
                "-reprojectTexture", high_poly_name, low_poly_name, str(self.texture_reproj_params)
            )
        else:
            self._run_command(
                "reproject texture",
                "-reprojectTexture", high_poly_name, low_poly_name
            )

        # 19. Select LowPoly model
        print(f"\n  [19/20] Selecting {low_poly_name}...")
        self._run_command("select model", "-selectModel", low_poly_name)

        # 20. Export
        print("\n  [20/20] Exporting model...")
        self._run_command("export", "-exportSelectedModel", str(output_file))

        # Validate export - raises ExportError if failed
        self._validate_export(output_file, component_name)

        # Get and display model statistics
        stats = self._get_obj_stats(output_file)
        print(f"    Model stats: {stats['vertices']:,} vertices, {stats['triangles']:,} triangles")

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

        # Verify RealityCapture is running
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
                self.process_log.append({
                    "component": component_name,
                    "output": output_file.name,
                    "status": "success",
                })

                # Save project after each successful component
                print("\n  Saving project...")
                self._run_command("save", "-save")

            except ExportError as e:
                # Log the failure
                self.process_log.append({
                    "component": component_name,
                    "output": "",
                    "status": "FAILED",
                })

                # Generate partial summary before halting
                self.generate_summary()

                # Re-raise to halt processing
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
            f"Simplification Params: {self.simplify_params}",
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


def get_user_input() -> tuple[Path, Path, Path, Optional[Path], bool]:
    """
    Prompt user for settings.

    Returns:
        Tuple of (alignment_dir, export_dir, simplify_params, texture_reproj_params, test_mode)
    """
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_simplify_params = r"D:\NA168\simplificationParameters.xml"
    default_texture_reproj_params = r"D:\NA168\TextureReprojectionSettings.xml"

    print("=" * 80)
    print("RealityCapture Model Processor")
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

    # Alignment directory (to derive component names)
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

    # Export directory
    while True:
        export_input = input(f"Export directory for models [{default_export_dir}]: ").strip()
        if not export_input:
            export_input = default_export_dir
        export_dir = Path(export_input)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            break
        except Exception as e:
            print(f"Error: Could not create directory: {e}")
            print()

    # Simplification parameters XML
    while True:
        simplify_input = input(f"Simplification params XML [{default_simplify_params}]: ").strip()
        if not simplify_input:
            simplify_input = default_simplify_params
        simplify_params = Path(simplify_input)
        if simplify_params.exists():
            break
        else:
            print(f"Error: File not found: {simplify_params}")
            print()

    # Texture reprojection parameters XML (optional)
    texture_reproj_input = input(
        f"Texture reprojection params XML (optional) [{default_texture_reproj_params}]: ").strip()
    if not texture_reproj_input:
        texture_reproj_input = default_texture_reproj_params
    texture_reproj_params = Path(texture_reproj_input)
    if not texture_reproj_params.exists():
        print(f"Note: Texture reprojection params not found, will use defaults.")
        texture_reproj_params = None

    # Test mode
    test_input = input("Test mode (only process first component)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()
    return alignment_dir, export_dir, simplify_params, texture_reproj_params, test_mode


def main():
    """
    Main entry point.
    """
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    try:
        alignment_dir, export_dir, simplify_params, texture_reproj_params, test_mode = get_user_input()

        processor = ModelProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            export_dir=export_dir,
            simplify_params=simplify_params,
            texture_reproj_params=texture_reproj_params,
            poll_interval=2.0,
            test_mode=test_mode,
        )

        exported = processor.process_all()

        processor.generate_summary()

        if exported:
            print(f"\nSuccessfully exported {len(exported)} model(s).")
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