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
  1. -selectComponent <n> — Select component by name
  2. -calculateNormalModel — Generate mesh
  3. -selectLargeTrianglesRel + filter — Remove oversized triangles
  4. -selectLargestModelComponent + invert + filter — Keep largest connected mesh
  5. -cleanModel — Fix geometry issues
  6. -smooth — Smooth surface
  7. -calculateTexture + rename — Generate texture, save as <n>_Textured
  8. -simplify 80% — First simplification pass
  9. -closeHoles — Close holes
  10. -simplify 80% — Second simplification pass
  11. -closeHoles — Close holes
  12. -renameSelectedModel — Rename to <n>_Model
  13. -reprojectTexture — Reproject texture from _Textured to _Model
  14. -save — Save project
  15. -exportModel — Export as FBX
  16. Validate export exists — HALT if missing

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
        """Send delegation command to running RealityCapture instance."""
        cmd = [str(self.rc_exe), "-delegateTo", "*"] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _get_status(self) -> Optional[str]:
        """Query RealityCapture status."""
        cmd = [str(self.rc_exe), "-getStatus", "*"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None

    def _wait_completed(self) -> subprocess.CompletedProcess:
        """Wait for current process to complete."""
        cmd = [str(self.rc_exe), "-waitCompleted", "*"]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _parse_status(self, status: Optional[str]) -> dict:
        """Parse status string into components."""
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
        """Check if RealityCapture is idle."""
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
        """Wait until RealityCapture reports idle status."""
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
        """Run a command and wait for completion."""
        self._delegate(*args)
        self._wait_until_idle(operation_name)
        return True

    def _validate_export(self, output_file: Path, component_name: str) -> None:
        """Validate that the exported model file exists and has content."""
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
        """Extract the component number from the component name."""
        import re

        # Try format: "Component (01)"
        match = re.search(r'\((\d+)\)', component_name)
        if match:
            num = int(match.group(1))
            return f"{num:02d}"

        # Try format: "Name_123" (number at end after underscore)
        match = re.search(r'_(\d+)$', component_name)
        if match:
            num = int(match.group(1))
            return f"{num:02d}"

        return "00"

    def scan_component_names(self) -> list[str]:
        """Scan alignment directory for .rsalign files and extract component names."""
        rsalign_files = sorted(self.alignment_dir.glob("*.rsalign"))
        component_names = [f.stem for f in rsalign_files]
        return component_names

    def process_component(self, component_name: str) -> Path:
        """
        Process a single component through the full pipeline.

        Pipeline:
        1. Select component
        2. Calculate normal model
        3. Select large triangles -> filter
        4. Select largest connected part -> invert -> filter
        5. Clean model
        6. Smooth
        7. Calculate texture (and rename to preserve as texture source)
        8. Simplify 80%
        9. Close holes
        10. Simplify 80%
        11. Close holes
        12. Rename simplified model
        13. Reproject texture from original to simplified
        14. Save project
        15. Export FBX
        """
        print(f"\n{'=' * 60}")
        print(f"Processing component: {component_name}")
        print(f"{'=' * 60}")

        # Model names
        textured_model_name = f"{component_name}_Textured"  # High-poly with texture
        final_model_name = f"{component_name}_Model"  # Final simplified model
        component_num = self._extract_component_number(component_name)
        output_file = self.export_dir / f"{self.project_prefix}_{component_num}.fbx"

        # 1. Select the component by name
        print("\n  [1/15] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # 2. Calculate normal model
        print("\n  [2/15] Calculating normal model...")
        self._run_command("model calculation", "-calculateNormalModel")

        # 3. Select large triangles and filter
        print("\n  [3/15] Filtering large triangles...")
        self._run_command("select large triangles", "-selectLargeTrianglesRel", "2.0")
        self._run_command("filter", "-removeSelectedTriangles")

        # 4. Keep largest connected part
        print("\n  [4/15] Keeping largest connected part...")
        self._run_command("select largest", "-selectLargestModelComponent")
        self._run_command("invert selection", "-invertTrianglesSelection")
        self._run_command("filter", "-removeSelectedTriangles")

        # 5. Clean model
        print("\n  [5/15] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 6. Smooth
        print("\n  [6/15] Smoothing...")
        self._run_command("smooth", "-smooth")

        # 7. Calculate texture and rename to preserve as texture source
        print("\n  [7/15] Calculating texture...")
        self._run_command("texture", "-calculateTexture")
        print(f"         Renaming textured model to {textured_model_name}...")
        self._run_command("rename textured", "-renameSelectedModel", textured_model_name)

        # 8. Simplify by 80% (keep 20%)
        print("\n  [8/15] Simplifying (80% reduction, pass 1)...")
        self._run_command("simplify", "-simplify", "20%")

        # 9. Close holes
        print("\n  [9/15] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 10. Simplify by 80% again
        print("\n  [10/15] Simplifying (80% reduction, pass 2)...")
        self._run_command("simplify", "-simplify", "20%")

        # 11. Close holes
        print("\n  [11/15] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 12. Rename simplified model
        print(f"\n  [12/15] Renaming simplified model to {final_model_name}...")
        self._run_command("rename model", "-renameSelectedModel", final_model_name)

        # 13. Reproject texture from high-poly textured model to simplified model
        print(f"\n  [13/15] Reprojecting texture from {textured_model_name} to {final_model_name}...")
        self._run_command("reproject texture", "-reprojectTexture", textured_model_name, final_model_name)

        # 14. Save project
        print("\n  [14/15] Saving project...")
        self._run_command("save", "-save")

        # 15. Export as FBX
        print(f"\n  [15/15] Exporting FBX as {output_file.name}...")
        self._run_command("export", "-exportModel", final_model_name, str(output_file))

        self._validate_export(output_file, component_name)

        return output_file

    def process_all(self) -> list[Path]:
        """Process all components found in the alignment directory."""
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
        """Generate and save a summary of processing."""
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
            f"Export Format: FBX",
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
    """Prompt user for settings."""
    # Defaults
    default_alignment_dir = r"C:\Users\jonat\OneDrive\Desktop\NA165_H2060\Topaz\alignments"
    default_export_dir = r"C:\Users\jonat\OneDrive\Desktop\NA165_H2060\Topaz\models"
    default_project_prefix = "NA165_H2080_"

    print("=" * 80)
    print("RealityCapture Model Processor - FBX Export")
    print("=" * 80)
    print()
    print("Pipeline: Select -> Normal Model -> Filter Large Triangles ->")
    print("          Keep Largest Part -> Clean Model -> Smooth -> Texture ->")
    print("          Simplify 80% -> Close Holes -> Simplify 80% -> Close Holes ->")
    print("          Rename -> Reproject Texture -> Save -> Export FBX")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - A project with the components must be open")
    print("  - Component names must match the .rsalign file stems")
    print()

    # Use defaults automatically if paths exist
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
    return alignment_dir, export_dir, project_prefix, test_mode


def main():
    """Main entry point."""
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