#!/usr/bin/env python3
"""
Process .rsalign component files through RealityCapture model generation pipeline.

For each .rsalign file:
1. Import component
2. Select component
3. Set reconstruction region
4. Calculate normal model
5. Select large triangles -> filter
6. Select largest connected part -> invert -> filter
7. Close holes
8. Smooth peaks
9. Generate texture (on high-poly)
10. Rename to preserve textured high-poly
11. Simplify by percentage (creates new model)
12. Close holes
13. Simplify by percentage
14. Close holes
15. Rename final model
16. Unwrap
17. Reproject texture from high-poly to final
18. Export model

Uses delegation to communicate with a RUNNING RealityCapture instance.
Does NOT launch a new instance.
"""

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


class ModelProcessor:
    """
    Process .rsalign components through the model generation pipeline.

    Delegates all commands to an already-running RealityCapture instance.
    """

    def __init__(
            self,
            rc_exe: Path,
            import_dir: Path,
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
            import_dir: Directory containing .rsalign files
            export_dir: Directory where models will be exported
            simplify_params: Path to simplification params XML (percentage-based)
            texture_reproj_params: Optional path to texture reprojection params XML
            poll_interval: Seconds between status checks
            test_mode: If True, only process the first .rsalign file
        """
        self.rc_exe = rc_exe
        self.import_dir = import_dir
        self.export_dir = export_dir
        self.simplify_params = simplify_params
        self.texture_reproj_params = texture_reproj_params
        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.process_log: list[dict[str, str]] = []

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.import_dir.exists():
            raise FileNotFoundError(f"Import directory not found: {self.import_dir}")

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

        Uses waitCompleted first, then verifies with status polling.

        Args:
            operation_name: Name of operation for logging
            timeout: Maximum seconds to wait
        """
        print(f"    Waiting for {operation_name}...", end=" ", flush=True)

        # Use built-in wait mechanism first
        self._wait_completed()

        # Brief delay for RC to update status
        time.sleep(0.5)

        # Quick check - if already idle, we're done
        status = self._get_status()
        if self._is_idle(status):
            print("done")
            return

        # Poll for idle state with timeout
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

    def scan_rsalign_files(self) -> list[Path]:
        """
        Scan import directory for .rsalign files.

        Returns:
            List of .rsalign file paths, sorted alphabetically
        """
        files = sorted(self.import_dir.glob("*.rsalign"))
        return files

    def process_component(self, rsalign_file: Path, output_name: str) -> bool:
        """
        Process a single .rsalign component through the full pipeline.

        Pipeline:
        1. Import component
        2. Select maximal component
        3. Set reconstruction region auto
        4. Calculate normal model
        5. Select large triangles -> filter
        6. Select largest connected part -> invert -> filter
        7. Close holes
        8. Smooth
        9. Calculate texture (high-poly)
        10. Rename to HighPoly
        11. Simplify (percentage-based via params.xml) - creates new model
        12. Close holes
        13. Simplify again
        14. Close holes
        15. Rename to LowPoly
        16. Unwrap
        17. Reproject texture from HighPoly to LowPoly
        18. Select LowPoly and export

        Args:
            rsalign_file: Path to .rsalign file
            output_name: Base name for output file

        Returns:
            True if processing succeeded
        """
        print(f"\n{'=' * 60}")
        print(f"Processing: {rsalign_file.name}")
        print(f"{'=' * 60}")

        high_poly_name = f"{output_name}_HighPoly"
        low_poly_name = f"{output_name}_LowPoly"

        try:
            # 1. Import component
            print("\n  [1/18] Importing component...")
            self._run_command("import", "-importComponent", str(rsalign_file))

            # 2. Select maximal component
            print("\n  [2/18] Selecting maximal component...")
            self._run_command("select component", "-selectMaximalComponent")

            # 3. Set reconstruction region automatically
            print("\n  [3/18] Setting reconstruction region...")
            self._run_command("set region", "-setReconstructionRegionAuto")

            # 4. Calculate normal model
            print("\n  [4/18] Calculating normal model...")
            self._run_command("model calculation", "-calculateNormalModel")

            # 5. Select large triangles and filter
            print("\n  [5/18] Selecting large triangles...")
            self._run_command("select large triangles", "-selectLargeTrianglesRel", "2.0")

            print("         Filtering selection...")
            self._run_command("filter", "-removeSelectedTriangles")

            # 6. Select largest connected part, invert, filter
            print("\n  [6/18] Selecting largest connected component...")
            self._run_command("select largest", "-selectLargestModelComponent")

            print("         Inverting selection...")
            self._run_command("invert selection", "-invertTrianglesSelection")

            print("         Filtering selection...")
            self._run_command("filter", "-removeSelectedTriangles")

            # 7. Close holes
            print("\n  [7/18] Closing holes...")
            self._run_command("close holes", "-closeHoles")

            # 8. Smooth
            print("\n  [8/18] Smoothing...")
            self._run_command("smooth", "-smooth")

            # 9. Calculate texture on high-poly
            print("\n  [9/18] Calculating texture (high-poly)...")
            self._run_command("texture", "-calculateTexture")

            # 10. Rename current model to preserve it as HighPoly
            print(f"\n  [10/18] Renaming to {high_poly_name}...")
            self._run_command("rename high-poly", "-renameSelectedModel", high_poly_name)

            # 11. First simplification (percentage-based via params.xml)
            # This creates a NEW model (the selected model becomes the simplified one)
            print("\n  [11/18] Simplifying (first pass, percentage-based)...")
            self._run_command("simplify", "-simplify", str(self.simplify_params))

            # 12. Close holes
            print("\n  [12/18] Closing holes...")
            self._run_command("close holes", "-closeHoles")

            # 13. Second simplification
            print("\n  [13/18] Simplifying (second pass, percentage-based)...")
            self._run_command("simplify", "-simplify", str(self.simplify_params))

            # 14. Close holes
            print("\n  [14/18] Closing holes...")
            self._run_command("close holes", "-closeHoles")

            # 15. Rename to LowPoly
            print(f"\n  [15/18] Renaming to {low_poly_name}...")
            self._run_command("rename low-poly", "-renameSelectedModel", low_poly_name)

            # 16. Unwrap the low-poly model
            print("\n  [16/18] Unwrapping...")
            self._run_command("unwrap", "-unwrap")

            # 17. Reproject texture from HighPoly to LowPoly
            print(f"\n  [17/18] Reprojecting texture from {high_poly_name} to {low_poly_name}...")
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

            # 18. Select LowPoly and export
            print("\n  [18/18] Exporting model...")
            output_file = self.export_dir / f"{output_name}.obj"

            # Select the low-poly model and export it
            self._run_command("select model", "-selectModel", low_poly_name)
            self._run_command("export", "-exportSelectedModel", str(output_file))

            # Verify export
            if output_file.exists():
                print(f"\n  Successfully exported: {output_file.name}")
                return True
            else:
                print(f"\n  Export file not found: {output_file}")
                return False

        except Exception as e:
            print(f"\n  Error processing {rsalign_file.name}: {e}")
            return False

    def process_all(self) -> list[Path]:
        """
        Process all .rsalign files in the import directory.

        If test_mode is True, only processes the first file.

        Returns:
            List of successfully exported model paths
        """
        rsalign_files = self.scan_rsalign_files()

        if not rsalign_files:
            print("No .rsalign files found in import directory.")
            return []

        print(f"Found {len(rsalign_files)} .rsalign file(s):")
        for f in rsalign_files:
            print(f"  - {f.name}")
        print()

        # Verify RealityCapture is running by checking status
        status = self._get_status()
        if not status:
            print("Error: Could not communicate with RealityCapture.")
            print("Please ensure RealityCapture is already running.")
            print("This script does NOT start a new instance.")
            return []

        print(f"Connected to RealityCapture. Status: {status}")

        if self.test_mode:
            print("\n*** TEST MODE: Only processing first file ***\n")
            rsalign_files = rsalign_files[:1]

        exported_models: list[Path] = []

        for i, rsalign_file in enumerate(rsalign_files):
            output_name = rsalign_file.stem

            print(f"\n[{i + 1}/{len(rsalign_files)}] Processing {rsalign_file.name}...")

            # Clear scene before each component for isolation
            print("  Clearing scene for new component...")
            self._run_command("new scene", "-newScene")

            success = self.process_component(rsalign_file, output_name)

            if success:
                output_file = self.export_dir / f"{output_name}.obj"
                exported_models.append(output_file)
                self.process_log.append({
                    "input": rsalign_file.name,
                    "output": output_file.name,
                    "status": "success",
                })
            else:
                self.process_log.append({
                    "input": rsalign_file.name,
                    "output": "",
                    "status": "failed",
                })

        return exported_models

    def generate_summary(self) -> None:
        """
        Generate and save a summary of processing.
        """
        if not self.process_log:
            print("\nNo files were processed.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_file = self.export_dir / "processing_summary.txt"

        successful = sum(1 for entry in self.process_log if entry['status'] == 'success')
        failed = sum(1 for entry in self.process_log if entry['status'] == 'failed')

        summary_lines = [
            "=" * 80,
            "RealityCapture Model Processing Summary",
            "=" * 80,
            f"Processing Date/Time: {timestamp}",
            f"Import Directory: {self.import_dir}",
            f"Export Directory: {self.export_dir}",
            f"Simplification Params: {self.simplify_params}",
            f"Total Processed: {len(self.process_log)}",
            f"Successful: {successful}",
            f"Failed: {failed}",
            "",
            "-" * 80,
            "Processing Details:",
            "-" * 80,
            f"{'Input File':<40} {'Output File':<30} {'Status':<10}",
            "-" * 80,
        ]

        for entry in self.process_log:
            summary_lines.append(
                f"{entry['input']:<40} {entry['output']:<30} {entry['status']:<10}"
            )

        summary_lines.extend([
            "-" * 80,
            "",
            "Processing completed.",
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
        Tuple of (import_dir, export_dir, simplify_params, texture_reproj_params, test_mode)
    """
    default_import_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_simplify_params = r"D:\NA168\simplificationParameters.xml"
    default_texture_reproj_params = r"D:\NA168\TextureReprojectionSettings.xml"

    print("=" * 80)
    print("RealityCapture Model Processor")
    print("=" * 80)
    print()
    print("This script processes .rsalign components through the model pipeline.")
    print()
    print("IMPORTANT: RealityCapture must already be running.")
    print("This script delegates commands to the running instance.")
    print()

    # Import directory
    while True:
        import_input = input(f"Import directory for .rsalign files [{default_import_dir}]: ").strip()
        if not import_input:
            import_input = default_import_dir
        import_dir = Path(import_input)
        if import_dir.exists():
            break
        else:
            print(f"Error: Directory not found: {import_dir}")
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
    test_input = input("Test mode (only process first file)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()
    return import_dir, export_dir, simplify_params, texture_reproj_params, test_mode


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
        import_dir, export_dir, simplify_params, texture_reproj_params, test_mode = get_user_input()

        processor = ModelProcessor(
            rc_exe=rc_exe,
            import_dir=import_dir,
            export_dir=export_dir,
            simplify_params=simplify_params,
            texture_reproj_params=texture_reproj_params,
            poll_interval=2.0,
            test_mode=test_mode,
        )

        exported = processor.process_all()

        processor.generate_summary()

        if not exported:
            print("\nNo models were exported.")

    except KeyboardInterrupt:
        print("\n\nProcessing cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()