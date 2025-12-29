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
  2. -calculateHighModel — Generate high detail mesh
  3. -selectMarginalTriangles + filter — Remove marginal triangles
  4. -simplify — Reduce to 70% triangle count (set in RC before running)
  5. -selectLargeTrianglesAbs 60 + filter — Remove triangles with edges > 60 units
  6. -selectLargestModelComponent + invert + filter — Keep only largest connected part
  7. -cleanModel — Fix geometry issues
  8. -smooth — Smooth surface
  9. -calculateTexture — Generate texture on high-poly
  10. -closeHoles 80000 — Close holes with max 80000 edges
  11. -renameSelectedModel — Rename to <n>_HighPoly
  12. -simplify — First simplification (uses RC settings or params.xml)
  13. -closeHoles — Close holes
  14. -simplify — Second simplification
  15. -closeHoles — Close holes
  16. -renameSelectedModel — Rename to <n>_LowPoly
  17. -unwrap — Unwrap low-poly model (required for texture reprojection)
  18. -reprojectTexture — Reproject texture from _HighPoly to _LowPoly
  19. -save — Save project
  20. -exportModel — Export _LowPoly as FBX
  21. -selectModel — Select _HighPoly model
  22. -export3dTiles — Export _HighPoly as Cesium 3D Tiles (.json)
  23. Validate exports exist — HALT if missing

Export-Only Mode:
  - Assumes models already processed and named as {component_name}_HighPoly
  - Only exports _HighPoly models as Cesium 3D Tiles
  - Skips all processing steps

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
            simplify_params: Optional[Path] = None,
            poll_interval: float = 2.0,
            test_mode: bool = True,
    ):
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.export_dir = export_dir
        self.project_prefix = project_prefix
        self.simplify_params = simplify_params
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

    def _wait_until_idle(self, operation_name: str = "operation", timeout: float = 360000.0) -> None:
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

    def process_component(self, component_name: str, simplify_params: Optional[Path] = None) -> Path:
        """
        Process a single component through the full pipeline.

        Pipeline:
        1. Select component
        2. Calculate high detail model
        3. Select marginal triangles -> filter
        4. Simplify to 70% (uses RC current settings - must be configured in RC)
        5. Select large triangles (absolute 60 units) -> filter
        6. Select largest component -> invert -> filter (keep only largest part)
        7. Clean model
        8. Smooth
        9. Calculate texture on high-poly
        10. Close holes (80000 max edges)
        11. Rename to _HighPoly (textured source model - preserved)
        12. Simplify (pass 1) - creates new model
        13. Close holes
        14. Simplify (pass 2)
        15. Close holes
        16. Rename to _LowPoly
        17. Unwrap _LowPoly (required for texture reprojection)
        18. Reproject texture from _HighPoly to _LowPoly
        19. Save project
        20. Export _LowPoly as FBX
        21. Select _HighPoly model
        22. Export _HighPoly as Cesium 3D Tiles (.json)
        """
        print(f"\n{'=' * 60}")
        print(f"Processing component: {component_name}")
        print(f"{'=' * 60}")

        # Model names
        high_poly_name = f"{component_name}_HighPoly"
        low_poly_name = f"{component_name}_LowPoly"
        component_num = self._extract_component_number(component_name)
        output_file = self.export_dir / f"{self.project_prefix}_{component_num}.fbx"

        # 1. Select the component by name
        print("\n  [1/23] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # 2. Calculate high detail model
        print("\n  [2/23] Calculating high detail model...")
        self._run_command("model calculation", "-calculateHighModel")

        # 3. Select marginal triangles and filter
        print("\n  [3/23] Filtering marginal triangles...")
        self._run_command("select marginal triangles", "-selectMarginalTriangles")
        self._run_command("filter", "-removeSelectedTriangles")

        # 4. Simplify to 70% (keep 70% of triangles)
        # NOTE: Configure RC simplification settings to keep 70% before running
        print("\n  [4/23] Simplifying to 70% (using RC current settings)...")
        self._run_command("simplify", "-simplify")

        # 5. Select large triangles and filter (absolute threshold 60 units)
        print("\n  [5/23] Filtering large triangles (>60 units)...")
        self._run_command("select large triangles", "-selectLargeTrianglesAbs", "60")
        self._run_command("filter", "-removeSelectedTriangles")

        # 6. Keep largest connected part
        print("\n  [6/23] Keeping largest connected part...")
        self._run_command("select largest", "-selectLargestModelComponent")
        self._run_command("invert selection", "-invertTrianglesSelection")
        self._run_command("filter", "-removeSelectedTriangles")

        # 7. Clean model
        print("\n  [7/23] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 8. Smooth
        print("\n  [8/23] Smoothing...")
        self._run_command("smooth", "-smooth")

        # 9. Calculate texture on high-poly model
        print("\n  [9/23] Calculating texture...")
        self._run_command("texture", "-calculateTexture")

        # 10. Close holes (max 80000 edges)
        print("\n  [10/23] Closing holes (max 80000 edges)...")
        self._run_command("close holes", "-closeHoles", "80000")

        # 11. Rename to preserve as high-poly textured source
        print(f"\n  [11/23] Renaming to {high_poly_name}...")
        self._run_command("rename", "-renameSelectedModel", high_poly_name)

        # 12. Simplify (pass 1) - simplify creates a new model from the selected one
        print("\n  [12/23] Simplifying (pass 1)...")
        if simplify_params and simplify_params.exists():
            self._run_command("simplify", "-simplify", str(simplify_params))
        else:
            self._run_command("simplify", "-simplify")

        # 13. Close holes
        print("\n  [13/23] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 14. Simplify (pass 2)
        print("\n  [14/23] Simplifying (pass 2)...")
        if simplify_params and simplify_params.exists():
            self._run_command("simplify", "-simplify", str(simplify_params))
        else:
            self._run_command("simplify", "-simplify")

        # 15. Close holes
        print("\n  [15/23] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 16. Rename simplified model to low-poly
        print(f"\n  [16/23] Renaming to {low_poly_name}...")
        self._run_command("rename", "-renameSelectedModel", low_poly_name)

        # 17. Unwrap the low-poly model (required before texture reprojection)
        print(f"\n  [17/23] Unwrapping {low_poly_name}...")
        self._run_command("unwrap", "-unwrap")

        # 18. Reproject texture from high-poly to low-poly
        print(f"\n  [18/23] Reprojecting texture...")
        print(f"           Source (textured): {high_poly_name}")
        print(f"           Target (simplified): {low_poly_name}")
        self._run_command("reproject texture", "-reprojectTexture", high_poly_name, low_poly_name)

        # 19. Save project
        print("\n  [19/23] Saving project...")
        self._run_command("save", "-save")

        # 20. Export low-poly as FBX
        print(f"\n  [20/23] Exporting low-poly FBX as {output_file.name}...")
        self._run_command("export", "-exportModel", low_poly_name, str(output_file))

        self._validate_export(output_file, component_name)

        # 21. Select high-poly model for Cesium export
        cesium_file = self.export_dir / f"{self.project_prefix}_{component_num}.json"
        print(f"\n  [21/23] Selecting {high_poly_name} for Cesium export...")
        self._run_command("select model", "-selectModel", high_poly_name)

        # 22. Export high-poly as Cesium 3D Tiles
        print(f"\n  [22/23] Exporting high-poly Cesium 3D Tiles as {cesium_file.name}...")
        self._run_command("export cesium", "-export3dTiles", str(cesium_file))

        # 23. Validate Cesium export
        # NOTE: RealityCapture may prepend "tileset_" to the filename
        print(f"\n  [23/23] Validating Cesium export...")

        # Check both possible filenames (with and without tileset_ prefix)
        cesium_file_with_prefix = self.export_dir / f"tileset_{self.project_prefix}_{component_num}.json"

        actual_cesium_file = None
        if cesium_file.exists() and cesium_file.stat().st_size > 0:
            actual_cesium_file = cesium_file
        elif cesium_file_with_prefix.exists() and cesium_file_with_prefix.stat().st_size > 0:
            actual_cesium_file = cesium_file_with_prefix

        if actual_cesium_file:
            size = actual_cesium_file.stat().st_size
            if size < 1024:
                size_str = f"{size} bytes"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            print(f"    Cesium export validated: {actual_cesium_file.name} ({size_str})")
        else:
            print(f"    Warning: Cesium export may have failed")
            print(f"    Checked: {cesium_file.name}")
            print(f"    Checked: {cesium_file_with_prefix.name}")

        return output_file

    def export_only_highpoly(self) -> list[Path]:
        """
        Export only mode: Assumes models are already calculated and named correctly.
        Only exports _HighPoly models as Cesium 3D Tiles.
        """
        component_names = self.scan_component_names()

        if not component_names:
            print("No .rsalign files found in alignment directory.")
            return []

        print(f"Found {len(component_names)} component(s) for export:")
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
        print("IMPORTANT: Ensure models are named as: {component_name}_HighPoly")
        print()

        if self.test_mode:
            print("*** TEST MODE: Only exporting first component ***\n")
            component_names = component_names[:1]

        exported_models: list[Path] = []

        for i, component_name in enumerate(component_names):
            print(f"\n[{i + 1}/{len(component_names)}] Exporting component: {component_name}")

            high_poly_name = f"{component_name}_HighPoly"
            component_num = self._extract_component_number(component_name)
            cesium_file = self.export_dir / f"{self.project_prefix}_{component_num}.json"

            try:
                # Select high-poly model
                print(f"  [1/2] Selecting {high_poly_name}...")
                self._run_command("select model", "-selectModel", high_poly_name)

                # Export as Cesium 3D Tiles
                print(f"  [2/2] Exporting Cesium 3D Tiles as {cesium_file.name}...")
                self._run_command("export cesium", "-export3dTiles", str(cesium_file))

                # Validate export - check both possible filenames
                cesium_file_with_prefix = self.export_dir / f"tileset_{self.project_prefix}_{component_num}.json"

                actual_cesium_file = None
                if cesium_file.exists() and cesium_file.stat().st_size > 0:
                    actual_cesium_file = cesium_file
                elif cesium_file_with_prefix.exists() and cesium_file_with_prefix.stat().st_size > 0:
                    actual_cesium_file = cesium_file_with_prefix

                if actual_cesium_file:
                    size = actual_cesium_file.stat().st_size
                    if size < 1024:
                        size_str = f"{size} bytes"
                    elif size < 1024 * 1024:
                        size_str = f"{size / 1024:.1f} KB"
                    else:
                        size_str = f"{size / (1024 * 1024):.1f} MB"
                    print(f"    Export validated: {actual_cesium_file.name} ({size_str})")
                    exported_models.append(actual_cesium_file)

                    self.process_log.append({
                        "component": component_name,
                        "output": actual_cesium_file.name,
                        "status": "success",
                    })
                else:
                    raise ExportError(f"Export failed: {cesium_file.name}")

            except Exception as e:
                print(f"    Error exporting {component_name}: {e}")
                self.process_log.append({
                    "component": component_name,
                    "output": f"{self.project_prefix}_{component_num}.json",
                    "status": "FAILED",
                })
                self.generate_summary()
                raise

        return exported_models

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
                output_file = self.process_component(component_name, self.simplify_params)
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
            f"Export Formats: FBX (low-poly), Cesium 3D Tiles (high-poly)",
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


def get_user_input() -> tuple[Path, Path, str, bool, bool]:
    """Prompt user for settings."""
    # Defaults
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_project_prefix = "NA168_H2080"

    print("=" * 80)
    print("RealityCapture Model Processor")
    print("=" * 80)
    print()
    print("Processing Pipeline:")
    print("  Select -> High Detail Model -> Filter Marginal ->")
    print("  Simplify 70% -> Filter Large Triangles (>60 units) ->")
    print("  Keep Largest Part -> Clean -> Smooth -> Texture ->")
    print("  Close Holes (80000) -> Rename _HighPoly -> Simplify -> Close Holes ->")
    print("  Simplify -> Close Holes -> Rename _LowPoly -> Unwrap ->")
    print("  Reproject Texture -> Save -> Export FBX (low-poly) ->")
    print("  Export Cesium (high-poly)")
    print()
    print("Export-Only Mode:")
    print("  Select _HighPoly -> Export Cesium 3D Tiles")
    print()
    print("NOTE: Simplification uses RC's current settings or a params.xml file.")
    print("      Configure RC simplification to keep 70% before running step 4.")
    print("      Configure RC simplification to keep 30% before running steps 12 & 14.")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - A project with the components must be open")
    print("  - Component names must match the .rsalign file stems")
    print()

    # Export-only mode option
    export_only_input = input(
        "Export-only mode (skip processing, only export existing _HighPoly models)? [y/N]: ").strip().lower()
    export_only = export_only_input == 'y'
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
    return alignment_dir, export_dir, project_prefix, test_mode, export_only


def main():
    """Main entry point."""
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    try:
        alignment_dir, export_dir, project_prefix, test_mode, export_only = get_user_input()

        processor = ModelProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            export_dir=export_dir,
            project_prefix=project_prefix,
            poll_interval=2.0,
            test_mode=test_mode,
        )

        if export_only:
            print("=" * 80)
            print("EXPORT-ONLY MODE: Exporting _HighPoly models as Cesium 3D Tiles")
            print("=" * 80)
            print()
            exported = processor.export_only_highpoly()
        else:
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