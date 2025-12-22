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

Per-component workflow (steps vary based on user options):
  1. -selectComponent <n> — Select component by name
  2. -setReconstructionRegionAuto — Set reconstruction region to cover point cloud
  3. -scaleReconstructionRegion 2 2 2 center factor — Double the region size
  4. -calculateHighModel — Generate high-detail mesh
  5. -selectMarginalTriangles + filter — Remove marginal triangles
  6. -selectLargeTrianglesRel + filter — Remove oversized triangles
  7. -selectLargestModelComponent + invert + filter — Keep largest connected mesh
  8. -cleanModel — Fix geometry issues
  9. -smooth — Smooth surface
  10. -closeHoles 50000 — Close large holes (up to 50000 edges)
  11. -closeHoles 5000 — Close smaller holes (up to 5000 edges)
  12. -cleanModel — Second clean pass after hole closing
  13. -calculateTexture — Generate texture on model

  If simplify enabled:
    14. -renameSelectedModel — Rename to <n>_HighPoly
    15. -simplify — First simplification pass
    16. -closeHoles — Close holes
    17. -simplify — Second simplification pass
    18. -closeHoles — Close holes
    19. -renameSelectedModel — Rename to <n>_LowPoly
    20. -unwrap — Unwrap low-poly model
    21. -reprojectTexture — Reproject texture from _HighPoly to _LowPoly

  22. -save — Save project

  If export_fbx enabled:
    23. -exportModel — Export as FBX (LowPoly if simplified, else original)

  If export_cesium enabled:
    24. -selectModel — Select HighPoly (if simplified)
    25. -export3dTiles — Export as Cesium 3D Tiles

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
            enable_simplify: bool = True,
            export_fbx: bool = True,
            export_cesium: bool = True,
            reconstruction_region_params: Optional[Path] = None,
            model_params: Optional[Path] = None,
            texture_params: Optional[Path] = None,
            simplify_params: Optional[Path] = None,
            smooth_params: Optional[Path] = None,
            unwrap_params: Optional[Path] = None,
            reprojection_params: Optional[Path] = None,
            fbx_export_params: Optional[Path] = None,
            cesium_export_params: Optional[Path] = None,
            poll_interval: float = 2.0,
            test_mode: bool = True,
    ):
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.export_dir = export_dir
        self.project_prefix = project_prefix
        self.enable_simplify = enable_simplify
        self.export_fbx = export_fbx
        self.export_cesium = export_cesium

        # Parameter file paths (stubs for future use)
        self.reconstruction_region_params = reconstruction_region_params
        self.model_params = model_params
        self.texture_params = texture_params
        self.simplify_params = simplify_params
        self.smooth_params = smooth_params
        self.unwrap_params = unwrap_params
        self.reprojection_params = reprojection_params
        self.fbx_export_params = fbx_export_params
        self.cesium_export_params = cesium_export_params

        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.process_log: list[dict[str, str]] = []
        self.used_export_names: dict[str, int] = {}

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.alignment_dir.exists():
            raise FileNotFoundError(f"Alignment directory not found: {self.alignment_dir}")

        # Validate that at least one export option is enabled if processing will occur
        if not self.export_fbx and not self.export_cesium:
            print("Warning: No export formats selected. Models will be generated but not exported.")

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

    def _run_command_with_optional_params(
            self,
            operation_name: str,
            command: str,
            params_file: Optional[Path] = None,
            extra_args: Optional[list[str]] = None
    ) -> bool:
        """Run a command with optional params.xml file."""
        args = [command]
        if extra_args:
            args.extend(extra_args)
        if params_file and params_file.exists():
            args.append(str(params_file))
            print(f"      (using params: {params_file.name})")
        self._delegate(*args)
        self._wait_until_idle(operation_name)
        return True

    def _validate_export(self, output_file: Path, component_name: str, export_type: str = "FBX") -> None:
        """Validate that the exported model file exists and has content."""
        if not output_file.exists():
            raise ExportError(
                f"{export_type} export FAILED for component '{component_name}': "
                f"Output file not found at {output_file}"
            )

        file_size = output_file.stat().st_size
        if file_size == 0:
            raise ExportError(
                f"{export_type} export FAILED for component '{component_name}': "
                f"Output file is empty (0 bytes) at {output_file}"
            )

        if file_size < 1024:
            size_str = f"{file_size} bytes"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.1f} KB"
        else:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"

        print(f"    {export_type} export validated: {output_file.name} ({size_str})")

    def _extract_component_number(self, component_name: str) -> str:
        """Extract the component number from the component name."""
        import re

        # Try format: "Component (01)" - number in parentheses
        match = re.search(r'\((\d+)\)', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: "Component 01" or "Component 1" - space then number at end
        match = re.search(r'\s+(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: "Name_123" - underscore then number at end
        match = re.search(r'_(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: "Name-123" - hyphen then number at end
        match = re.search(r'-(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        # Try format: any trailing number
        match = re.search(r'(\d+)$', component_name)
        if match:
            return f"{int(match.group(1)):02d}"

        return "00"

    def _get_collision_suffix(self, count: int) -> str:
        """
        Generate collision suffix from count.

        count=0 -> "" (no suffix for first use)
        count=1 -> "a"
        count=2 -> "b"
        ...
        count=26 -> "z"
        count=27 -> "aa"
        count=28 -> "ab"
        """
        if count == 0:
            return ""

        count -= 1  # Convert to 0-indexed for letter calculation
        result = []
        while count >= 0:
            result.append(chr(ord('a') + (count % 26)))
            count = count // 26 - 1
        return ''.join(reversed(result))

    def _get_unique_export_name(self, component_name: str) -> str:
        """
        Generate a unique export base name, appending suffix if collision detected.

        Returns the base name without extension (e.g., "NA168_H2080_01" or "NA168_H2080_01a").
        """
        component_num = self._extract_component_number(component_name)
        base_name = f"{self.project_prefix}_{component_num}"

        collision_count = self.used_export_names.get(base_name, 0)
        suffix = self._get_collision_suffix(collision_count)
        unique_name = f"{base_name}{suffix}"

        self.used_export_names[base_name] = collision_count + 1

        if suffix:
            print(f"    Note: Name collision detected, using suffix '{suffix}' -> {unique_name}")

        return unique_name

    def scan_component_names(self) -> list[str]:
        """Scan alignment directory for .rsalign files and extract component names."""
        rsalign_files = sorted(self.alignment_dir.glob("*.rsalign"))
        component_names = [f.stem for f in rsalign_files]
        return component_names

    def process_component(self, component_name: str) -> dict[str, Optional[Path]]:
        """
        Process a single component through the full pipeline.

        Returns:
            Dict with keys 'fbx' and 'cesium', values are Path or None.
        """
        print(f"\n{'=' * 60}")
        print(f"Processing component: {component_name}")
        print(f"{'=' * 60}")

        # Determine model names based on simplification setting
        if self.enable_simplify:
            high_poly_name = f"{component_name}_HighPoly"
            low_poly_name = f"{component_name}_LowPoly"
            fbx_model_name = low_poly_name
            cesium_model_name = high_poly_name
        else:
            # No simplification: single model, use component name
            high_poly_name = None
            low_poly_name = None
            fbx_model_name = f"{component_name}_Model"
            cesium_model_name = fbx_model_name

        export_base_name = self._get_unique_export_name(component_name)
        fbx_file = self.export_dir / f"{export_base_name}.fbx" if self.export_fbx else None
        cesium_file = self.export_dir / f"{export_base_name}.json" if self.export_cesium else None

        step = 0

        # --- RECONSTRUCTION SETUP ---

        # Step: Select the component by name
        step += 1
        print(f"\n  [{step}] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # Step: Set reconstruction region automatically
        step += 1
        print(f"\n  [{step}] Setting reconstruction region (auto)...")
        self._run_command("set reconstruction region", "-setReconstructionRegionAuto")

        # Step: Scale reconstruction region 2x from center
        step += 1
        print(f"\n  [{step}] Scaling reconstruction region 2x...")
        self._run_command(
            "scale reconstruction region",
            "-scaleReconstructionRegion", "2", "2", "2", "center", "factor"
        )

        # --- MODEL GENERATION ---

        # Step: Calculate high-detail model
        step += 1
        print(f"\n  [{step}] Calculating high-detail model...")
        self._run_command_with_optional_params(
            "model calculation",
            "-calculateHighModel",
            self.model_params
        )

        # --- MESH CLEANUP ---

        # Step: Select and remove marginal triangles
        step += 1
        print(f"\n  [{step}] Filtering marginal triangles...")
        self._run_command("select marginal triangles", "-selectMarginalTriangles")
        self._run_command("filter", "-removeSelectedTriangles")

        # Step: Select and remove large triangles
        step += 1
        print(f"\n  [{step}] Filtering large triangles...")
        self._run_command("select large triangles", "-selectLargeTrianglesRel", "2.0")
        self._run_command("filter", "-removeSelectedTriangles")

        # Step: Keep largest connected part
        step += 1
        print(f"\n  [{step}] Keeping largest connected part...")
        self._run_command("select largest", "-selectLargestModelComponent")
        self._run_command("invert selection", "-invertTrianglesSelection")
        self._run_command("filter", "-removeSelectedTriangles")

        # Step: Clean model
        step += 1
        print(f"\n  [{step}] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # Step: Smooth
        step += 1
        print(f"\n  [{step}] Smoothing...")
        self._run_command_with_optional_params(
            "smooth",
            "-smooth",
            self.smooth_params
        )

        # Step: Close holes (large - 50000 edges max)
        step += 1
        print(f"\n  [{step}] Closing holes (max 50000 edges)...")
        self._run_command("close holes", "-closeHoles", "50000")

        # Step: Close holes (small - 5000 edges max)
        step += 1
        print(f"\n  [{step}] Closing holes (max 5000 edges)...")
        self._run_command("close holes", "-closeHoles", "5000")

        # Step: Clean model (second pass after hole closing)
        step += 1
        print(f"\n  [{step}] Cleaning model (post hole-closing)...")
        self._run_command("clean model", "-cleanModel")

        # Step: Calculate texture
        step += 1
        print(f"\n  [{step}] Calculating texture...")
        self._run_command_with_optional_params(
            "texture",
            "-calculateTexture",
            self.texture_params
        )

        # --- SIMPLIFICATION (if enabled) ---

        if self.enable_simplify:
            # Step: Rename to preserve as high-poly textured source
            step += 1
            print(f"\n  [{step}] Renaming to {high_poly_name}...")
            self._run_command("rename", "-renameSelectedModel", high_poly_name)

            # Step: Simplify (pass 1)
            step += 1
            print(f"\n  [{step}] Simplifying (pass 1)...")
            self._run_command_with_optional_params(
                "simplify",
                "-simplify",
                self.simplify_params
            )

            # Step: Close holes
            step += 1
            print(f"\n  [{step}] Closing holes...")
            self._run_command("close holes", "-closeHoles")

            # Step: Simplify (pass 2)
            step += 1
            print(f"\n  [{step}] Simplifying (pass 2)...")
            self._run_command_with_optional_params(
                "simplify",
                "-simplify",
                self.simplify_params
            )

            # Step: Close holes
            step += 1
            print(f"\n  [{step}] Closing holes...")
            self._run_command("close holes", "-closeHoles")

            # Step: Rename simplified model to low-poly
            step += 1
            print(f"\n  [{step}] Renaming to {low_poly_name}...")
            self._run_command("rename", "-renameSelectedModel", low_poly_name)

            # Step: Unwrap the low-poly model
            step += 1
            print(f"\n  [{step}] Unwrapping {low_poly_name}...")
            self._run_command_with_optional_params(
                "unwrap",
                "-unwrap",
                self.unwrap_params
            )

            # Step: Reproject texture from high-poly to low-poly
            step += 1
            print(f"\n  [{step}] Reprojecting texture...")
            print(f"           Source (textured): {high_poly_name}")
            print(f"           Target (simplified): {low_poly_name}")
            if self.reprojection_params and self.reprojection_params.exists():
                print(f"      (using params: {self.reprojection_params.name})")
                self._run_command(
                    "reproject texture",
                    "-reprojectTexture", high_poly_name, low_poly_name,
                    str(self.reprojection_params)
                )
            else:
                self._run_command(
                    "reproject texture",
                    "-reprojectTexture", high_poly_name, low_poly_name
                )
        else:
            # No simplification: rename model for consistency
            step += 1
            print(f"\n  [{step}] Renaming to {fbx_model_name}...")
            self._run_command("rename", "-renameSelectedModel", fbx_model_name)

        # --- SAVE PROJECT ---

        step += 1
        print(f"\n  [{step}] Saving project...")
        self._run_command("save", "-save")

        # --- EXPORTS ---

        result = {"fbx": None, "cesium": None}

        if self.export_fbx:
            step += 1
            print(f"\n  [{step}] Exporting FBX as {fbx_file.name}...")
            if self.fbx_export_params and self.fbx_export_params.exists():
                print(f"      (using params: {self.fbx_export_params.name})")
                self._run_command(
                    "export FBX",
                    "-exportModel", fbx_model_name, str(fbx_file),
                    str(self.fbx_export_params)
                )
            else:
                self._run_command(
                    "export FBX",
                    "-exportModel", fbx_model_name, str(fbx_file)
                )
            self._validate_export(fbx_file, component_name, "FBX")
            result["fbx"] = fbx_file

        if self.export_cesium:
            # If simplified, select the high-poly model for Cesium export
            if self.enable_simplify:
                step += 1
                print(f"\n  [{step}] Selecting {cesium_model_name} for Cesium export...")
                self._run_command("select model", "-selectModel", cesium_model_name)

            step += 1
            print(f"\n  [{step}] Exporting Cesium 3D Tiles as {cesium_file.name}...")
            if self.cesium_export_params and self.cesium_export_params.exists():
                print(f"      (using params: {self.cesium_export_params.name})")
                self._run_command(
                    "export Cesium",
                    "-export3dTiles", str(cesium_file),
                    str(self.cesium_export_params)
                )
            else:
                self._run_command(
                    "export Cesium",
                    "-export3dTiles", str(cesium_file)
                )
            self._validate_export(cesium_file, component_name, "Cesium 3D Tiles")
            result["cesium"] = cesium_file

        return result

    def process_all(self) -> list[dict[str, Optional[Path]]]:
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

        # Print configuration
        print("Configuration:")
        print(f"  Simplification: {'Enabled' if self.enable_simplify else 'Disabled'}")
        print(f"  Export FBX: {'Yes' if self.export_fbx else 'No'}")
        print(f"  Export Cesium: {'Yes' if self.export_cesium else 'No'}")
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

        exported_models: list[dict[str, Optional[Path]]] = []

        for i, component_name in enumerate(component_names):
            print(f"\n[{i + 1}/{len(component_names)}] Processing component: {component_name}")

            try:
                result = self.process_component(component_name)
                exported_models.append(result)

                self.process_log.append({
                    "component": component_name,
                    "fbx_output": result["fbx"].name if result["fbx"] else "N/A",
                    "cesium_output": result["cesium"].name if result["cesium"] else "N/A",
                    "status": "success",
                })

            except ExportError as e:
                self.process_log.append({
                    "component": component_name,
                    "fbx_output": "FAILED",
                    "cesium_output": "FAILED",
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
            "=" * 100,
            "RealityCapture Model Processing Summary",
            "=" * 100,
            f"Processing Date/Time: {timestamp}",
            f"Alignment Directory: {self.alignment_dir}",
            f"Export Directory: {self.export_dir}",
            f"Project Prefix: {self.project_prefix}",
            "",
            "Configuration:",
            f"  Simplification: {'Enabled' if self.enable_simplify else 'Disabled'}",
            f"  Export FBX: {'Yes' if self.export_fbx else 'No'}",
            f"  Export Cesium: {'Yes' if self.export_cesium else 'No'}",
            "",
            f"Total Processed: {len(self.process_log)}",
            f"Successful: {successful}",
            f"Failed: {failed}",
            "",
        ]

        if failed > 0:
            summary_lines.append("*** PROCESSING HALTED DUE TO EXPORT FAILURE ***")
            summary_lines.append("")

        summary_lines.extend([
            "-" * 100,
            "Processing Details:",
            "-" * 100,
            f"{'Component Name':<35} {'FBX Output':<30} {'Cesium Output':<25} {'Status':<10}",
            "-" * 100,
        ])

        for entry in self.process_log:
            summary_lines.append(
                f"{entry['component']:<35} {entry['fbx_output']:<30} {entry['cesium_output']:<25} {entry['status']:<10}"
            )

        summary_lines.extend([
            "-" * 100,
            "",
            "Processing completed." if failed == 0 else "Processing incomplete due to error.",
            "=" * 100,
        ])

        summary_text = "\n".join(summary_lines)

        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(summary_text)

        print(f"\n{summary_text}")
        print(f"\nSummary saved to: {summary_file}")


def get_user_input() -> dict:
    """Prompt user for settings."""
    # Defaults
    default_rc_exe = r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe"
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_export_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_project_prefix = "NA168_H2080"

    print("=" * 80)
    print("RealityCapture Model Processor")
    print("=" * 80)
    print()
    print("Pipeline: Select Component -> Set Reconstruction Region (auto, 2x scale) ->")
    print("          High-Detail Model -> Filter Marginal Triangles ->")
    print("          Filter Large Triangles -> Keep Largest Part -> Clean -> Smooth ->")
    print("          Close Holes (50k, 5k) -> Clean -> Texture ->")
    print("          [Simplify/LOD if enabled] -> Save -> Export")
    print()
    print("NOTE: Simplification uses RC's current settings or a params.xml file.")
    print("      Set simplification to 50% in RC before running if using defaults.")
    print()
    print("REQUIREMENTS:")
    print("  - RealityCapture must be running")
    print("  - A project with the components must be open")
    print("  - Component names must match the .rsalign file stems")
    print()

    # RealityScan executable path
    while True:
        rc_input = input(f"RealityScan executable path [{default_rc_exe}]: ").strip()
        if not rc_input:
            rc_input = default_rc_exe
        rc_exe = Path(rc_input)
        if rc_exe.exists():
            print(f"  Using: {rc_exe}")
            break
        print(f"Error: Executable not found: {rc_exe}")

    # Alignment directory
    while True:
        align_input = input(f"Alignment directory [{default_alignment_dir}]: ").strip()
        if not align_input:
            align_input = default_alignment_dir
        alignment_dir = Path(align_input)
        if alignment_dir.exists():
            print(f"  Using: {alignment_dir}")
            break
        print(f"Error: Directory not found: {alignment_dir}")

    # Export directory
    while True:
        export_input = input(f"Export directory [{default_export_dir}]: ").strip()
        if not export_input:
            export_input = default_export_dir
        export_dir = Path(export_input)
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
            print(f"  Using: {export_dir}")
            break
        except Exception as e:
            print(f"Error: Could not create directory: {e}")

    # Project prefix
    prefix_input = input(f"Project prefix [{default_project_prefix}]: ").strip()
    if not prefix_input:
        prefix_input = default_project_prefix
    project_prefix = prefix_input
    print(f"  Using: {project_prefix}")

    # Simplification option
    print()
    simplify_input = input("Enable simplification (create HighPoly + LowPoly)? [Y/n]: ").strip().lower()
    enable_simplify = simplify_input != 'n'
    print(f"  Simplification: {'Enabled' if enable_simplify else 'Disabled'}")

    # Export options
    print()
    fbx_input = input("Export FBX? [Y/n]: ").strip().lower()
    export_fbx = fbx_input != 'n'
    print(f"  Export FBX: {'Yes' if export_fbx else 'No'}")

    cesium_input = input("Export Cesium 3D Tiles? [Y/n]: ").strip().lower()
    export_cesium = cesium_input != 'n'
    print(f"  Export Cesium: {'Yes' if export_cesium else 'No'}")

    # Validate export selection
    if not export_fbx and not export_cesium:
        print()
        print("Warning: No export formats selected. Models will be generated but not exported.")
        confirm = input("Continue anyway? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Aborted.")
            sys.exit(0)

    # Test mode
    print()
    test_input = input("Test mode (only process first component)? [Y/n]: ").strip().lower()
    test_mode = test_input != 'n'

    print()

    return {
        "rc_exe": rc_exe,
        "alignment_dir": alignment_dir,
        "export_dir": export_dir,
        "project_prefix": project_prefix,
        "enable_simplify": enable_simplify,
        "export_fbx": export_fbx,
        "export_cesium": export_cesium,
        "test_mode": test_mode,
    }


def main():
    """Main entry point."""
    try:
        config = get_user_input()

        processor = ModelProcessor(
            rc_exe=config["rc_exe"],
            alignment_dir=config["alignment_dir"],
            export_dir=config["export_dir"],
            project_prefix=config["project_prefix"],
            enable_simplify=config["enable_simplify"],
            export_fbx=config["export_fbx"],
            export_cesium=config["export_cesium"],
            poll_interval=2.0,
            test_mode=config["test_mode"],
        )

        exported = processor.process_all()
        processor.generate_summary()

        if exported:
            fbx_count = sum(1 for e in exported if e.get("fbx"))
            cesium_count = sum(1 for e in exported if e.get("cesium"))
            print(f"\nSuccessfully exported {fbx_count} FBX and {cesium_count} Cesium model(s).")
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