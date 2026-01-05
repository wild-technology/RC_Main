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
- Supports checkpoint/resume to continue from last successful component

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
  11. -calculateTexture — Re-texture to cover closed holes
  12. -renameSelectedModel — Rename to <n>_HighPoly
  13. -simplify — First simplification (uses RC settings or params.xml)
  14. -closeHoles — Close holes
  15. -simplify — Second simplification
  16. -closeHoles — Close holes
  17. -renameSelectedModel — Rename to <n>_LowPoly
  18. -unwrap — Unwrap low-poly model (required for texture reprojection)
  19. -reprojectTexture — Reproject texture from _HighPoly to _LowPoly
  20. -save — Save project
  21. -exportModel — Export _LowPoly as FBX (to fbx_lowpoly/ subdirectory)
  22. -selectModel — Select _HighPoly model
  23. -export3dTiles — Export _HighPoly as Cesium 3D Tiles (to cesium/ subdirectory)
  24. -exportModel — Export _HighPoly as FBX (to fbx_highpoly/ subdirectory)
  25. Copy .rsalign alignment file (to alignments/ subdirectory)
  26. Validate all exports exist — HALT if missing

Export-Only Mode:
  - Assumes models already processed and named as {component_name}_HighPoly
  - Only exports _HighPoly models as Cesium 3D Tiles
  - Skips all processing steps

Uses delegation (-delegateTo * -waitCompleted *) to communicate with running RealityCapture instance.
"""

import json
import subprocess
import sys
import time
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional


CHECKPOINT_FILENAME = "processing_checkpoint.json"


class ExportError(Exception):
    """Raised when model export fails or exported file is not found."""
    pass


class CheckpointData:
    """
    Container for checkpoint state data.
    """

    def __init__(
            self,
            alignment_dir: str,
            output_base_dir: str,
            project_prefix: str,
            export_only: bool,
            completed_components: list[str],
            failed_component: Optional[str] = None,
            timestamp: Optional[str] = None,
            export_lowpoly_fbx: bool = True,
            export_highpoly_fbx: bool = True,
            export_cesium: bool = True,
            export_alignment: bool = True,
    ):
        self.alignment_dir = alignment_dir
        self.output_base_dir = output_base_dir
        self.project_prefix = project_prefix
        self.export_only = export_only
        self.completed_components = completed_components
        self.failed_component = failed_component
        self.timestamp = timestamp or datetime.now().isoformat()
        self.export_lowpoly_fbx = export_lowpoly_fbx
        self.export_highpoly_fbx = export_highpoly_fbx
        self.export_cesium = export_cesium
        self.export_alignment = export_alignment

    def to_dict(self) -> dict:
        """Serialize checkpoint data to dictionary."""
        return {
            "alignment_dir": self.alignment_dir,
            "output_base_dir": self.output_base_dir,
            "project_prefix": self.project_prefix,
            "export_only": self.export_only,
            "completed_components": self.completed_components,
            "failed_component": self.failed_component,
            "timestamp": self.timestamp,
            "export_lowpoly_fbx": self.export_lowpoly_fbx,
            "export_highpoly_fbx": self.export_highpoly_fbx,
            "export_cesium": self.export_cesium,
            "export_alignment": self.export_alignment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointData":
        """Deserialize checkpoint data from dictionary."""
        return cls(
            alignment_dir=data["alignment_dir"],
            output_base_dir=data["output_base_dir"],
            project_prefix=data["project_prefix"],
            export_only=data.get("export_only", False),
            completed_components=data.get("completed_components", []),
            failed_component=data.get("failed_component"),
            timestamp=data.get("timestamp"),
            export_lowpoly_fbx=data.get("export_lowpoly_fbx", True),
            export_highpoly_fbx=data.get("export_highpoly_fbx", True),
            export_cesium=data.get("export_cesium", True),
            export_alignment=data.get("export_alignment", True),
        )


class ModelProcessor:
    """
    Process components in an open RealityCapture project through the model pipeline.
    """

    def __init__(
            self,
            rc_exe: Path,
            alignment_dir: Path,
            output_base_dir: Path,
            project_prefix: str,
            simplify_params: Optional[Path] = None,
            poll_interval: float = 2.0,
            test_mode: bool = True,
            export_only: bool = False,
            resume_from_checkpoint: bool = False,
            export_lowpoly_fbx: bool = True,
            export_highpoly_fbx: bool = True,
            export_cesium: bool = True,
            export_alignment: bool = True,
    ):
        self.rc_exe = rc_exe
        self.alignment_dir = alignment_dir
        self.output_base_dir = output_base_dir
        self.project_prefix = project_prefix
        self.simplify_params = simplify_params
        self.poll_interval = poll_interval
        self.test_mode = test_mode
        self.export_only = export_only
        self.resume_from_checkpoint = resume_from_checkpoint
        self.export_lowpoly_fbx = export_lowpoly_fbx
        self.export_highpoly_fbx = export_highpoly_fbx
        self.export_cesium = export_cesium
        self.export_alignment = export_alignment
        self.process_log: list[dict[str, str]] = []

        self.checkpoint_file = output_base_dir / CHECKPOINT_FILENAME
        self.completed_components: list[str] = []

        self.fbx_lowpoly_dir = output_base_dir / "fbx_lowpoly"
        self.fbx_highpoly_dir = output_base_dir / "fbx_highpoly"
        self.cesium_dir = output_base_dir / "cesium"
        self.alignments_dir = output_base_dir / "alignments"

        if self.export_lowpoly_fbx:
            self.fbx_lowpoly_dir.mkdir(parents=True, exist_ok=True)
        if self.export_highpoly_fbx:
            self.fbx_highpoly_dir.mkdir(parents=True, exist_ok=True)
        if self.export_cesium:
            self.cesium_dir.mkdir(parents=True, exist_ok=True)
        if self.export_alignment:
            self.alignments_dir.mkdir(parents=True, exist_ok=True)

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        if not self.alignment_dir.exists():
            raise FileNotFoundError(f"Alignment directory not found: {self.alignment_dir}")

        if self.resume_from_checkpoint:
            self._load_checkpoint()

    def _save_checkpoint(self, failed_component: Optional[str] = None) -> None:
        """Save current processing state to checkpoint file."""
        checkpoint = CheckpointData(
            alignment_dir=str(self.alignment_dir),
            output_base_dir=str(self.output_base_dir),
            project_prefix=self.project_prefix,
            export_only=self.export_only,
            completed_components=self.completed_components.copy(),
            failed_component=failed_component,
            export_lowpoly_fbx=self.export_lowpoly_fbx,
            export_highpoly_fbx=self.export_highpoly_fbx,
            export_cesium=self.export_cesium,
            export_alignment=self.export_alignment,
        )

        try:
            with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint.to_dict(), f, indent=2)
        except Exception as e:
            print(f"    Warning: Could not save checkpoint: {e}")

    def _load_checkpoint(self) -> None:
        """Load processing state from checkpoint file."""
        if not self.checkpoint_file.exists():
            print("No checkpoint file found. Starting fresh.")
            return

        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            checkpoint = CheckpointData.from_dict(data)
            self.completed_components = checkpoint.completed_components
            self.export_lowpoly_fbx = checkpoint.export_lowpoly_fbx
            self.export_highpoly_fbx = checkpoint.export_highpoly_fbx
            self.export_cesium = checkpoint.export_cesium
            self.export_alignment = checkpoint.export_alignment

            print(f"Loaded checkpoint from {checkpoint.timestamp}")
            print(f"  Previously completed: {len(self.completed_components)} component(s)")
            if checkpoint.failed_component:
                print(f"  Failed component: {checkpoint.failed_component}")

        except Exception as e:
            print(f"Warning: Could not load checkpoint: {e}")
            print("Starting fresh.")
            self.completed_components = []

    def _clear_checkpoint(self) -> None:
        """Remove checkpoint file after successful completion."""
        if self.checkpoint_file.exists():
            try:
                self.checkpoint_file.unlink()
                print(f"Checkpoint file cleared: {self.checkpoint_file.name}")
            except Exception as e:
                print(f"Warning: Could not remove checkpoint file: {e}")

    def _get_status(self) -> Optional[str]:
        """Query RealityCapture status."""
        cmd = [str(self.rc_exe), "-getStatus", "*"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None

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

    def _wait_until_idle(self, operation_name: str = "operation", timeout: float = 18000.0) -> None:
        """Wait until RealityCapture reports idle status (5 hour timeout)."""
        print(f"    Waiting for {operation_name}...", end=" ", flush=True)

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
        """Run a command and wait for completion using combined delegation."""
        cmd = [str(self.rc_exe), "-delegateTo", "*", "-waitCompleted", "*"] + list(args)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            print(f"    Warning: Command returned error code {result.returncode}")
            if result.stderr:
                print(f"    Error: {result.stderr}")

        self._wait_until_idle(operation_name)

        return True

    def _validate_export(self, output_file: Path, component_name: str, max_retries: int = 10,
                         retry_delay: float = 2.0) -> None:
        """
        Validate that the exported model file exists and has content.
        Includes retry logic for network drive sync delays.
        """
        for attempt in range(max_retries):
            if output_file.exists():
                file_size = output_file.stat().st_size
                if file_size == 0:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
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
                return

            if attempt < max_retries - 1:
                if attempt == 0:
                    print(f"    Waiting for file sync...", end=" ", flush=True)
                else:
                    print(f"{attempt + 1}", end=" ", flush=True)
                time.sleep(retry_delay)

        print("failed")
        raise ExportError(
            f"Export FAILED for component '{component_name}': "
            f"Output file not found at {output_file} after {max_retries} retries"
        )

    def _validate_and_rename_cesium_export(
            self, expected_output: Path, component_num: str, max_retries: int = 10, retry_delay: float = 2.0
    ) -> Optional[Path]:
        """
        Validate and rename Cesium export if needed.
        Cesium exports create both a .json file and a folder with the same base name.
        Both need to be renamed if RC adds the 'tileset_' prefix.
        Includes retry logic for network drive sync delays.
        """
        cesium_with_prefix = expected_output.parent / f"tileset_{expected_output.name}"
        folder_with_prefix = expected_output.parent / f"tileset_{expected_output.stem}"
        expected_folder = expected_output.parent / expected_output.stem

        for attempt in range(max_retries):
            actual_cesium_file = None
            needs_rename = False

            if expected_output.exists() and expected_output.stat().st_size > 0:
                actual_cesium_file = expected_output
            elif cesium_with_prefix.exists() and cesium_with_prefix.stat().st_size > 0:
                actual_cesium_file = cesium_with_prefix
                needs_rename = True

            if actual_cesium_file:
                size = actual_cesium_file.stat().st_size
                if size < 1024:
                    size_str = f"{size} bytes"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"

                if needs_rename:
                    print(f"           Renaming: {actual_cesium_file.name} -> {expected_output.name}")
                    actual_cesium_file.rename(expected_output)
                    actual_cesium_file = expected_output

                    if folder_with_prefix.exists() and folder_with_prefix.is_dir():
                        print(f"           Renaming folder: {folder_with_prefix.name}/ -> {expected_folder.name}/")
                        folder_with_prefix.rename(expected_folder)

                print(f"           Cesium export validated: {actual_cesium_file.name} ({size_str})")
                return actual_cesium_file

            if attempt < max_retries - 1:
                if attempt == 0:
                    print(f"           Waiting for file sync...", end=" ", flush=True)
                else:
                    print(f"{attempt + 1}", end=" ", flush=True)
                time.sleep(retry_delay)

        print("failed")
        print(f"           Warning: Cesium export failed after {max_retries} retries")
        print(f"           Checked: {expected_output.name}")
        print(f"           Checked: {cesium_with_prefix.name}")
        return None

    def _extract_component_number(self, component_name: str) -> str:
        """Extract the component number from the component name."""
        import re

        match = re.search(r'\((\d+)\)', component_name)
        if match:
            num = int(match.group(1))
            return f"{num:02d}"

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

    def _copy_alignment_file(self, component_name: str, component_num: str) -> None:
        """Copy the .rsalign file to the alignments output directory."""
        source_file = self.alignment_dir / f"{component_name}.rsalign"
        dest_file = self.alignments_dir / f"{self.project_prefix}_{component_num}.rsalign"

        if source_file.exists():
            try:
                shutil.copy2(source_file, dest_file)
                print(f"    Alignment file copied: {dest_file.name}")
            except Exception as e:
                print(f"    Warning: Could not copy alignment file: {e}")
        else:
            print(f"    Warning: Source alignment file not found: {source_file}")

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
        9. Calculate texture on high-poly model
        10. Close holes (max 80000 edges)
        11. Re-calculate texture (to properly texture closed holes)
        12. Rename to _HighPoly (textured source model - preserved)
        13. Simplify (pass 1) - creates new model
        14. Close holes
        15. Simplify (pass 2)
        16. Close holes
        17. Rename to _LowPoly
        18. Unwrap _LowPoly (required for texture reprojection)
        19. Reproject texture from _HighPoly to _LowPoly
        20. Save project
        21. Export _LowPoly as FBX (to fbx_lowpoly/ subdirectory)
        22. Select _HighPoly model
        23. Export _HighPoly as Cesium 3D Tiles (to cesium/ subdirectory)
        24. Export _HighPoly as FBX (to fbx_highpoly/ subdirectory)
        25. Copy .rsalign alignment file (to alignments/ subdirectory)
        """
        print(f"\n{'=' * 60}")
        print(f"Processing component: {component_name}")
        print(f"{'=' * 60}")

        high_poly_name = f"{component_name}_HighPoly"
        low_poly_name = f"{component_name}_LowPoly"
        component_num = self._extract_component_number(component_name)

        # Derive export paths from model names to ensure consistency
        fbx_lowpoly_output = self.fbx_lowpoly_dir / f"{low_poly_name}.fbx"
        fbx_highpoly_output = self.fbx_highpoly_dir / f"{high_poly_name}.fbx"
        cesium_output = self.cesium_dir / f"{high_poly_name}.json"

        # 1. Select the component by name
        print("\n  [1/26] Selecting component...")
        self._run_command("select component", "-selectComponent", component_name)

        # 2. Calculate high detail model
        print("\n  [2/26] Calculating high detail model...")
        self._run_command("model calculation", "-calculateHighModel")

        # 3. Select marginal triangles and filter
        print("\n  [3/26] Filtering marginal triangles...")
        self._run_command("select marginal triangles", "-selectMarginalTriangles")
        self._run_command("filter", "-removeSelectedTriangles")

        # 4. Simplify to 70% (keep 70% of triangles)
        print("\n  [4/26] Simplifying to 70% (using RC current settings)...")
        self._run_command("simplify", "-simplify")

        # 5. Select large triangles and filter (absolute threshold 60 units)
        print("\n  [5/26] Filtering large triangles (>60 units)...")
        self._run_command("select large triangles", "-selectLargeTrianglesAbs", "60")
        self._run_command("filter", "-removeSelectedTriangles")

        # 6. Keep largest connected part
        print("\n  [6/26] Keeping largest connected part...")
        self._run_command("select largest", "-selectLargestModelComponent")
        self._run_command("invert selection", "-invertTrianglesSelection")
        self._run_command("filter", "-removeSelectedTriangles")

        # 7. Clean model
        print("\n  [7/26] Cleaning model...")
        self._run_command("clean model", "-cleanModel")

        # 8. Smooth
        print("\n  [8/26] Smoothing...")
        self._run_command("smooth", "-smooth")

        # 9. Calculate texture on high-poly model
        print("\n  [9/26] Calculating texture...")
        self._run_command("texture", "-calculateTexture")

        # 10. Close holes (max 80000 edges)
        print("\n  [10/26] Closing holes (max 80000 edges)...")
        self._run_command("close holes", "-closeHoles", "80000")

        # 11. Re-calculate texture to properly texture the closed holes
        print("\n  [11/26] Re-calculating texture (to texture closed holes)...")
        self._run_command("texture", "-calculateTexture")

        # 12. Rename to preserve as high-poly textured source
        print(f"\n  [12/26] Renaming to {high_poly_name}...")
        self._run_command("rename", "-renameSelectedModel", high_poly_name)

        # 13. Simplify (pass 1)
        print("\n  [13/26] Simplifying (pass 1)...")
        if simplify_params and simplify_params.exists():
            self._run_command("simplify", "-simplify", str(simplify_params))
        else:
            self._run_command("simplify", "-simplify")

        # 14. Close holes
        print("\n  [14/26] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 15. Simplify (pass 2)
        print("\n  [15/26] Simplifying (pass 2)...")
        if simplify_params and simplify_params.exists():
            self._run_command("simplify", "-simplify", str(simplify_params))
        else:
            self._run_command("simplify", "-simplify")

        # 16. Close holes
        print("\n  [16/26] Closing holes...")
        self._run_command("close holes", "-closeHoles")

        # 17. Rename simplified model to low-poly
        print(f"\n  [17/26] Renaming to {low_poly_name}...")
        self._run_command("rename", "-renameSelectedModel", low_poly_name)

        # 18. Unwrap the low-poly model
        print(f"\n  [18/26] Unwrapping {low_poly_name}...")
        self._run_command("unwrap", "-unwrap")

        # 19. Reproject texture from high-poly to low-poly
        print(f"\n  [19/26] Reprojecting texture...")
        print(f"           Source (textured): {high_poly_name}")
        print(f"           Target (simplified): {low_poly_name}")
        self._run_command("reproject texture", "-reprojectTexture", high_poly_name, low_poly_name)

        # 20. Save project
        print("\n  [20/26] Saving project...")
        self._run_command("save", "-save")

        # 21. Export low-poly as FBX
        print(f"\n  [21/26] Exporting low-poly FBX to fbx_lowpoly/{fbx_lowpoly_output.name}...")
        self._run_command("export", "-exportModel", low_poly_name, str(fbx_lowpoly_output))

        self._validate_export(fbx_lowpoly_output, component_name)

        # 22. Select high-poly model for exports
        print(f"\n  [22/26] Selecting {high_poly_name} for exports...")
        self._run_command("select model", "-selectModel", high_poly_name)

        # 23. Export high-poly as Cesium 3D Tiles
        print(f"\n  [23/26] Exporting high-poly Cesium 3D Tiles to cesium/{cesium_output.name}...")
        self._run_command("export cesium", "-export3dTiles", str(cesium_output))

        print(f"           Validating Cesium export...")
        actual_cesium_file = self._validate_and_rename_cesium_export(cesium_output, component_num)

        if not actual_cesium_file:
            print(f"           Warning: Cesium export validation failed")

        # 24. Export high-poly as FBX
        print(f"\n  [24/26] Exporting high-poly FBX to fbx_highpoly/{fbx_highpoly_output.name}...")
        self._run_command("export", "-exportModel", high_poly_name, str(fbx_highpoly_output))

        self._validate_export(fbx_highpoly_output, component_name)

        # 25. Copy alignment file
        print(f"\n  [25/26] Copying alignment file to alignments/{self.project_prefix}_{component_num}.rsalign...")
        self._copy_alignment_file(component_name, component_num)

        # 26. Final validation summary
        print(f"\n  [26/26] All exports completed successfully")

        return fbx_lowpoly_output

    def export_only_highpoly(self) -> list[Path]:
        """
        Export only mode: Assumes models are already calculated and named correctly.
        Only exports _HighPoly models as Cesium 3D Tiles and FBX.
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

        # Filter out already-completed components if resuming
        if self.resume_from_checkpoint and self.completed_components:
            remaining = [c for c in component_names if c not in self.completed_components]
            skipped = len(component_names) - len(remaining)
            if skipped > 0:
                print(f"Resuming: Skipping {skipped} already-completed component(s)")
                print(f"  Completed: {', '.join(self.completed_components[:5])}" +
                      (f"... (+{len(self.completed_components) - 5} more)" if len(
                          self.completed_components) > 5 else ""))
                print()
            component_names = remaining

        exported_models: list[Path] = []

        for i, component_name in enumerate(component_names):
            print(f"\n[{i + 1}/{len(component_names)}] Exporting component: {component_name}")

            high_poly_name = f"{component_name}_HighPoly"
            component_num = self._extract_component_number(component_name)

            # Derive export paths from model names to ensure consistency
            cesium_output = self.cesium_dir / f"{high_poly_name}.json"
            fbx_highpoly_output = self.fbx_highpoly_dir / f"{high_poly_name}.fbx"

            try:
                print(f"  [1/5] Selecting {high_poly_name}...")
                self._run_command("select model", "-selectModel", high_poly_name)

                print(f"  [2/5] Exporting Cesium 3D Tiles to cesium/{cesium_output.name}...")
                self._run_command("export cesium", "-export3dTiles", str(cesium_output))

                print(f"  [3/5] Validating Cesium export...")
                actual_cesium_file = self._validate_and_rename_cesium_export(cesium_output, component_num)

                if actual_cesium_file:
                    exported_models.append(actual_cesium_file)
                else:
                    raise ExportError(f"Cesium export failed: {cesium_output.name}")

                print(f"  [4/5] Exporting high-poly FBX to fbx_highpoly/{fbx_highpoly_output.name}...")
                self._run_command("export", "-exportModel", high_poly_name, str(fbx_highpoly_output))
                self._validate_export(fbx_highpoly_output, component_name)
                exported_models.append(fbx_highpoly_output)

                print(f"  [5/5] Copying alignment file...")
                self._copy_alignment_file(component_name, component_num)

                # Mark component as completed and save checkpoint
                self.completed_components.append(component_name)
                self._save_checkpoint()

                self.process_log.append({
                    "component": component_name,
                    "output": f"cesium/{actual_cesium_file.name}, fbx_highpoly/{fbx_highpoly_output.name}",
                    "status": "success",
                })

            except Exception as e:
                print(f"    Error exporting {component_name}: {e}")
                self._save_checkpoint(failed_component=component_name)
                self.process_log.append({
                    "component": component_name,
                    "output": f"cesium/{high_poly_name}.json",
                    "status": "FAILED",
                })
                self.generate_summary()
                raise

        # Clear checkpoint on successful completion
        self._clear_checkpoint()

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

        if self.resume_from_checkpoint and self.completed_components:
            remaining = [c for c in component_names if c not in self.completed_components]
            skipped = len(component_names) - len(remaining)
            if skipped > 0:
                print(f"Resuming: Skipping {skipped} already-completed component(s)")
                print(f"  Completed: {', '.join(self.completed_components[:5])}" +
                      (f"... (+{len(self.completed_components) - 5} more)" if len(self.completed_components) > 5 else ""))
                print()

                all_component_names = set(self.scan_component_names())
                for comp in self.completed_components:
                    if comp in all_component_names:
                        comp_num = self._extract_component_number(comp)
                        self.process_log.append({
                            "component": comp,
                            "output": f"fbx_lowpoly/{self.project_prefix}_{comp_num}_LowPoly.fbx, "
                                      f"fbx_highpoly/{self.project_prefix}_{comp_num}_HighPoly.fbx",
                            "status": "success (previous run)",
                        })

            component_names = remaining

        if not component_names:
            print("All components already processed. Nothing to do.")
            return []

        exported_models: list[Path] = []
        total_components = len(component_names) + len(self.completed_components)

        for i, component_name in enumerate(component_names):
            current_index = len(self.completed_components) + i + 1
            print(f"\n[{current_index}/{total_components}] Processing component: {component_name}")

            try:
                output_files = self.process_component(component_name, self.simplify_params)
                exported_models.extend(output_files)
                component_num = self._extract_component_number(component_name)

                self.completed_components.append(component_name)
                self._save_checkpoint()

                output_list = []
                if self.export_lowpoly_fbx:
                    output_list.append(f"fbx_lowpoly/{self.project_prefix}_{component_num}_LowPoly.fbx")
                if self.export_highpoly_fbx:
                    output_list.append(f"fbx_highpoly/{self.project_prefix}_{component_num}_HighPoly.fbx")

                self.process_log.append({
                    "component": component_name,
                    "output": ", ".join(output_list) if output_list else "none",
                    "status": "success",
                })

            except ExportError as e:
                component_num = self._extract_component_number(component_name)
                self._save_checkpoint(failed_component=component_name)
                self.process_log.append({
                    "component": component_name,
                    "output": f"fbx_lowpoly/{self.project_prefix}_{component_num}_LowPoly.fbx",
                    "status": "FAILED",
                })

                self.generate_summary()

                print(f"\n{'=' * 60}")
                print("FATAL ERROR: Export failed. Processing halted.")
                print(f"{'=' * 60}")
                print(f"\nCheckpoint saved. Re-run script to resume from: {component_name}")
                raise

        self._clear_checkpoint()

        return exported_models

    def generate_summary(self) -> None:
        """Generate and save a summary of processing."""
        if not self.process_log:
            print("\nNo components were processed.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_file = self.output_base_dir / "processing_summary.txt"

        successful = sum(1 for entry in self.process_log if 'success' in entry['status'])
        failed = sum(1 for entry in self.process_log if entry['status'] == 'FAILED')

        export_formats = []
        if self.export_lowpoly_fbx:
            export_formats.append("FBX (_LowPoly)")
        if self.export_highpoly_fbx:
            export_formats.append("FBX (_HighPoly)")
        if self.export_cesium:
            export_formats.append("Cesium 3D Tiles")
        if self.export_alignment:
            export_formats.append("Alignment (.rsalign)")

        summary_lines = [
            "=" * 80,
            "RealityCapture Model Processing Summary",
            "=" * 80,
            f"Processing Date/Time: {timestamp}",
            f"Alignment Directory: {self.alignment_dir}",
            f"Output Directory: {self.output_base_dir}",
            ]

        if self.export_lowpoly_fbx:
            summary_lines.append(f"  - Low-poly FBX exports: {self.fbx_lowpoly_dir}")
        if self.export_highpoly_fbx:
            summary_lines.append(f"  - High-poly FBX exports: {self.fbx_highpoly_dir}")
        if self.export_cesium:
            summary_lines.append(f"  - Cesium 3D Tiles exports: {self.cesium_dir}")
        if self.export_alignment:
            summary_lines.append(f"  - Alignment files: {self.alignments_dir}")

        summary_lines.extend([
            f"Project Prefix: {self.project_prefix}",
            f"Export Formats: {', '.join(export_formats) if export_formats else 'None'}",
            f"Total Processed: {len(self.process_log)}",
            f"Successful: {successful}",
            f"Failed: {failed}",
            "",
        ])

        if failed > 0:
            summary_lines.append("*** PROCESSING HALTED DUE TO EXPORT FAILURE ***")
            summary_lines.append(f"*** Checkpoint saved - re-run to resume ***")
            summary_lines.append("")

        summary_lines.extend([
            "-" * 80,
            "Processing Details:",
            "-" * 80,
            f"{'Component Name':<30} {'Output Files':<45} {'Status':<15}",
            "-" * 80,
            ])

        for entry in self.process_log:
            summary_lines.append(
                f"{entry['component']:<30} {entry['output']:<45} {entry['status']:<15}"
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


def find_rc_executable() -> Optional[Path]:
    """
    Try to find RealityScan executable in common locations.
    Returns first existing path or None.
    """
    candidates = [
        Path(r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe"),
        Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def check_for_checkpoint(output_dir: Path) -> Optional[CheckpointData]:
    """
    Check if a checkpoint file exists in the output directory.
    Returns CheckpointData if found, None otherwise.
    """
    checkpoint_file = output_dir / CHECKPOINT_FILENAME
    if not checkpoint_file.exists():
        return None

    try:
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CheckpointData.from_dict(data)
    except Exception:
        return None


def prompt_resume_from_checkpoint(checkpoint: CheckpointData) -> tuple[bool, bool]:
    """
    Prompt user to resume from existing checkpoint.

    Returns:
        Tuple of (should_resume, should_clear_and_restart)
    """
    print()
    print("┌" + "─" * 78 + "┐")
    print("│ EXISTING CHECKPOINT DETECTED".ljust(79) + "│")
    print("├" + "─" * 78 + "┤")
    print("│                                                                              │")
    print(f"│ Timestamp: {checkpoint.timestamp:<65}│")
    print(f"│ Project Prefix: {checkpoint.project_prefix:<60}│")
    print(f"│ Completed Components: {len(checkpoint.completed_components):<54}│")
    if checkpoint.failed_component:
        print(f"│ Failed Component: {checkpoint.failed_component:<58}│")
    print("│                                                                              │")
    print("│ Mode: " + ("Export-Only" if checkpoint.export_only else "Full Processing").ljust(70) + "│")

    export_types = []
    if checkpoint.export_lowpoly_fbx:
        export_types.append("LowPoly FBX")
    if checkpoint.export_highpoly_fbx:
        export_types.append("HighPoly FBX")
    if checkpoint.export_cesium:
        export_types.append("Cesium")
    if checkpoint.export_alignment:
        export_types.append("Alignment")

    print("│ Exports: " + (", ".join(export_types) if export_types else "None").ljust(68) + "│")
    print("│                                                                              │")
    print("├" + "─" * 78 + "┤")
    print("│ OPTIONS:                                                                     │")
    print("│   [R] Resume from checkpoint (skip completed components)                     │")
    print("│   [C] Clear checkpoint and start fresh                                       │")
    print("│   [Q] Quit                                                                   │")
    print("└" + "─" * 78 + "┘")
    print()

    while True:
        choice = input("Select option [R/C/Q]: ").strip().upper()
        if choice == 'R':
            return True, False
        elif choice == 'C':
            confirm = input("Confirm clear checkpoint and restart? [y/N]: ").strip().lower()
            if confirm == 'y':
                return False, True
            continue
        elif choice == 'Q':
            sys.exit(0)
        else:
            print("Invalid option. Please enter R, C, or Q.")


def get_user_input(checkpoint: Optional[CheckpointData] = None) -> tuple[Path, Path, str, bool, bool, bool, bool, bool, bool, bool]:
    """
    Prompt user for settings.

    Returns:
        Tuple of (alignment_dir, output_dir, project_prefix, test_mode, export_only, resume_from_checkpoint,
                  export_lowpoly_fbx, export_highpoly_fbx, export_cesium, export_alignment)
    """
    default_alignment_dir = r"D:\NA168\Zeuss_NA168_H2080\finishthesealignments"
    default_output_dir = r"D:\NA168\Zeuss_NA168_H2080\models"
    default_project_prefix = "NA168_H2080"

    resume_from_checkpoint = False
    if checkpoint:
        default_alignment_dir = checkpoint.alignment_dir
        default_output_dir = checkpoint.output_base_dir
        default_project_prefix = checkpoint.project_prefix
        resume_from_checkpoint = True

    print()
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "        RealityCapture Automated Model Processing Pipeline".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    if not resume_from_checkpoint:
        print("┌" + "─" * 78 + "┐")
        print("│ WHAT THIS SCRIPT DOES:".ljust(79) + "│")
        print("├" + "─" * 78 + "┤")
        print("│                                                                              │")
        print("│ This script automates the complete 3D model generation workflow in          │")
        print("│ RealityCapture, processing aligned components through a comprehensive       │")
        print("│ pipeline that produces production-ready assets.                             │")
        print("│                                                                              │")
        print("│ For each component, the script will:                                        │")
        print("│   - Generate high-detail mesh from aligned images                           │")
        print("│   - Filter marginal/oversized triangles and keep largest geometry           │")
        print("│   - Clean, smooth, and texture the high-poly model                          │")
        print("│   - Close holes and re-texture to ensure complete coverage                  │")
        print("│   - Create simplified low-poly version through double-pass decimation       │")
        print("│   - Reproject texture from high-poly to low-poly for optimal quality        │")
        print("│   - Export in selected formats (FBX, Cesium 3D Tiles, alignment files)      │")
        print("│                                                                              │")
        print("│ CHECKPOINT/RESUME: Progress is saved after each component. If processing   │")
        print("│ fails, re-run the script to resume from the last successful export.        │")
        print("│                                                                              │")
        print("│ OUTPUT STRUCTURE:                                                            │")
        print("│   output_directory/                                                          │")
        print("│   ├── fbx_lowpoly/  → Low-poly FBX models (simplified, game-ready)          │")
        print("│   ├── fbx_highpoly/ → High-poly FBX models (full detail)                    │")
        print("│   ├── cesium/       → High-poly Cesium 3D Tiles for web visualization       │")
        print("│   ├── alignments/   → Component alignment files (.rsalign)                  │")
        print("│   ├── processing_checkpoint.json → Resume state (auto-deleted on success)  │")
        print("│   └── processing_summary.txt → Detailed processing log                      │")
        print("│                                                                              │")
        print("└" + "─" * 78 + "┘")
        print()
        print("┌" + "─" * 78 + "┐")
        print("│ CRITICAL REQUIREMENTS - READ BEFORE PROCEEDING:".ljust(79) + "│")
        print("├" + "─" * 78 + "┤")
        print("│                                                                              │")
        print("│ * RealityCapture must be ALREADY RUNNING with your project OPEN             │")
        print("│ * All components must be loaded and named to match .rsalign filenames       │")
        print("│                                                                              │")
        print("│ CONFIGURE THESE SETTINGS IN REALITYCAPTURE BEFORE RUNNING:                  │")
        print("│                                                                              │")
        print("│   1. SIMPLIFICATION SETTINGS (Tools -> Simplify):                           │")
        print("│      - For Step 4 (initial simplify): Set to keep 70% of triangles          │")
        print("│      - For Steps 13 & 15 (low-poly): Set to keep 30% of triangles           │")
        print("│                                                                              │")
        print("│   2. TEXTURE SETTINGS (optional):                                            │")
        print("│      - Set desired texture resolution and quality                           │")
        print("│      - Configure unwrap parameters if needed                                │")
        print("│                                                                              │")
        print("│   3. EXPORT SETTINGS (optional):                                             │")
        print("│      - Configure FBX export parameters if needed                            │")
        print("│      - Configure Cesium 3D Tiles export settings if needed                  │")
        print("│                                                                              │")
        print("│ NOTE: Script uses 5-hour timeout per operation. Complex models may take     │")
        print("│       significant time to process - this is normal for high-quality output. │")
        print("│                                                                              │")
        print("└" + "─" * 78 + "┘")
        print()
        print("┌" + "─" * 78 + "┐")
        print("│ PROCESSING MODES:".ljust(79) + "│")
        print("├" + "─" * 78 + "┤")
        print("│                                                                              │")
        print("│ [1] FULL PROCESSING (Default)                                               │")
        print("│     Complete 26-step pipeline from component selection through export       │")
        print("│     Recommended for: First-time processing of aligned components            │")
        print("│                                                                              │")
        print("│ [2] EXPORT-ONLY MODE                                                         │")
        print("│     Only exports existing _HighPoly models (skips all processing)           │")
        print("│     Recommended for: Re-exporting after changing export settings            │")
        print("│                                                                              │")
        print("└" + "─" * 78 + "┘")
        print()

    if resume_from_checkpoint:
        export_only = checkpoint.export_only
        print(f"Resuming in {'export-only' if export_only else 'full processing'} mode (from checkpoint)")
    else:
        export_only_input = input("Enable export-only mode? [y/N]: ").strip().lower()
        export_only = export_only_input == 'y'
    print()

    if resume_from_checkpoint:
        export_lowpoly_fbx = checkpoint.export_lowpoly_fbx
        export_highpoly_fbx = checkpoint.export_highpoly_fbx
        export_cesium = checkpoint.export_cesium
        export_alignment = checkpoint.export_alignment

        print("Export settings (from checkpoint):")
        print(f"  - Low-poly FBX: {'enabled' if export_lowpoly_fbx else 'disabled'}")
        print(f"  - High-poly FBX: {'enabled' if export_highpoly_fbx else 'disabled'}")
        print(f"  - Cesium 3D Tiles: {'enabled' if export_cesium else 'disabled'}")
        print(f"  - Alignment files: {'enabled' if export_alignment else 'disabled'}")
    else:
        print("┌" + "─" * 78 + "┐")
        print("│ EXPORT OPTIONS:".ljust(79) + "│")
        print("├" + "─" * 78 + "┤")
        print("│ Select which export formats to generate (default: all enabled)              │")
        print("└" + "─" * 78 + "┘")
        print()

        if not export_only:
            lowpoly_input = input("Export low-poly FBX? [Y/n]: ").strip().lower()
            export_lowpoly_fbx = lowpoly_input != 'n'
        else:
            export_lowpoly_fbx = False

        highpoly_input = input("Export high-poly FBX? [Y/n]: ").strip().lower()
        export_highpoly_fbx = highpoly_input != 'n'

        cesium_input = input("Export Cesium 3D Tiles? [Y/n]: ").strip().lower()
        export_cesium = cesium_input != 'n'

        alignment_input = input("Copy alignment files? [Y/n]: ").strip().lower()
        export_alignment = alignment_input != 'n'

    print()

    alignment_dir = Path(default_alignment_dir)
    if alignment_dir.exists():
        if resume_from_checkpoint:
            print(f"* Alignment directory (from checkpoint): {alignment_dir}")
        else:
            print(f"* Alignment directory: {alignment_dir}")
    else:
        while True:
            align_input = input(f"Alignment directory [{default_alignment_dir}]: ").strip()
            if not align_input:
                align_input = default_alignment_dir
            alignment_dir = Path(align_input)
            if alignment_dir.exists():
                print(f"* Alignment directory: {alignment_dir}")
                break
            print(f"X Error: Directory not found: {alignment_dir}")

    print()
    if resume_from_checkpoint:
        output_dir = Path(default_output_dir)
        print(f"* Output directory (from checkpoint): {output_dir}")
    else:
        output_input = input(f"Output directory [{default_output_dir}]: ").strip()
        if not output_input:
            output_input = default_output_dir
        output_dir = Path(output_input)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if not resume_from_checkpoint:
            print(f"* Output directory: {output_dir}")
        if export_lowpoly_fbx:
            print(f"  -> Low-poly FBX: {output_dir / 'fbx_lowpoly'}")
        if export_highpoly_fbx:
            print(f"  -> High-poly FBX: {output_dir / 'fbx_highpoly'}")
        if export_cesium:
            print(f"  -> Cesium 3D Tiles: {output_dir / 'cesium'}")
        if export_alignment:
            print(f"  -> Alignment files: {output_dir / 'alignments'}")
    except Exception as e:
        print(f"X Error: Could not create directory: {e}")
        sys.exit(1)

    print()
    if resume_from_checkpoint:
        project_prefix = default_project_prefix
        print(f"* Project prefix (from checkpoint): {project_prefix}")
    else:
        project_prefix = input(f"Project prefix [{default_project_prefix}]: ").strip()
        if not project_prefix:
            project_prefix = default_project_prefix
        print(f"* Project prefix: {project_prefix}")

    print()
    if resume_from_checkpoint:
        test_mode = False
        print("* Full mode enabled (resuming all remaining components)")
    else:
        test_input = input("Test mode (process only first component)? [Y/n]: ").strip().lower()
        test_mode = test_input != 'n'
        if test_mode:
            print("* Test mode enabled - will process only first component")
        else:
            print("* Full mode enabled - will process all components")

    print()
    print("-" * 80)
    print()

    return (alignment_dir, output_dir, project_prefix, test_mode, export_only, resume_from_checkpoint,
            export_lowpoly_fbx, export_highpoly_fbx, export_cesium, export_alignment)


def main():
    """Main entry point."""
    rc_exe = find_rc_executable()

    if not rc_exe:
        print()
        print("X RealityScan executable not found in default locations.")
        print()
        print("  Checked locations:")
        print("    - C:\\Program Files\\Epic Games\\RealityScan_2.1\\RealityScan.exe")
        print("    - C:\\Program Files\\Epic Games\\RealityScan_2.0\\RealityScan.exe")
        print()

        custom_path = input("Please enter the full path to RealityScan.exe: ").strip()
        rc_exe = Path(custom_path)

        if not rc_exe.exists():
            print(f"X Error: File not found at {rc_exe}")
            sys.exit(1)

    print(f"* Using RealityScan: {rc_exe}")
    print()

    default_output_dir = Path(r"D:\NA168\Zeuss_NA168_H2080\models")
    checkpoint = check_for_checkpoint(default_output_dir)

    resume_from_checkpoint = False
    if checkpoint:
        should_resume, should_clear = prompt_resume_from_checkpoint(checkpoint)
        if should_resume:
            resume_from_checkpoint = True
        elif should_clear:
            checkpoint_file = default_output_dir / CHECKPOINT_FILENAME
            if checkpoint_file.exists():
                checkpoint_file.unlink()
                print("Checkpoint cleared.")
            checkpoint = None

    try:
        if resume_from_checkpoint and checkpoint:
            (alignment_dir, output_dir, project_prefix, test_mode, export_only, _,
             export_lowpoly_fbx, export_highpoly_fbx, export_cesium, export_alignment) = get_user_input(checkpoint)
        else:
            (alignment_dir, output_dir, project_prefix, test_mode, export_only, _,
             export_lowpoly_fbx, export_highpoly_fbx, export_cesium, export_alignment) = get_user_input()

        processor = ModelProcessor(
            rc_exe=rc_exe,
            alignment_dir=alignment_dir,
            output_base_dir=output_dir,
            project_prefix=project_prefix,
            poll_interval=2.0,
            test_mode=test_mode,
            export_only=export_only,
            resume_from_checkpoint=resume_from_checkpoint,
            export_lowpoly_fbx=export_lowpoly_fbx,
            export_highpoly_fbx=export_highpoly_fbx,
            export_cesium=export_cesium,
            export_alignment=export_alignment,
        )

        if export_only:
            print("+" + "=" * 78 + "+")
            print("|" + "EXPORT-ONLY MODE: Exporting _HighPoly models".center(78) + "|")
            print("+" + "=" * 78 + "+")
            print()
            exported = processor.export_only_highpoly()
        else:
            mode_text = "RESUMING" if resume_from_checkpoint else "FULL PROCESSING MODE"
            print("+" + "=" * 78 + "+")
            print("|" + f"{mode_text}: Starting 26-step pipeline".center(78) + "|")
            print("+" + "=" * 78 + "+")
            print()
            exported = processor.process_all()

        processor.generate_summary()

        if exported:
            print()
            print("+" + "=" * 78 + "+")
            print("|" + f"* SUCCESS: Exported {len(exported)} file(s)".center(78) + "|")
            print("+" + "=" * 78 + "+")
        else:
            print()
            print("+" + "=" * 78 + "+")
            print("|" + "WARNING: No files were exported".center(78) + "|")
            print("+" + "=" * 78 + "+")

    except ExportError as e:
        print()
        print("+" + "=" * 78 + "+")
        print("|" + "X EXPORT ERROR".center(78) + "|")
        print("+" + "=" * 78 + "+")
        print(f"\n{e}", file=sys.stderr)
        print("\nCheckpoint saved. Re-run script to resume from failed component.")
        sys.exit(2)
    except KeyboardInterrupt:
        print()
        print()
        print("+" + "=" * 78 + "+")
        print("|" + "Processing cancelled by user".center(78) + "|")
        print("+" + "=" * 78 + "+")
        print("\nCheckpoint saved. Re-run script to resume.")
        sys.exit(1)
    except Exception as e:
        print()
        print("+" + "=" * 78 + "+")
        print("|" + "X UNEXPECTED ERROR:".center(78) + "|")
        print("+" + "=" * 78 + "+")
        print(f"\n{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()