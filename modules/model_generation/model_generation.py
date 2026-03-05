"""Model generation module for the RC pipeline.

Generates textured 3D models from aligned components via RealityScan
delegation. Supports configurable step ordering, simplification,
multi-format export, and per-component checkpointing.

Based on ModelGenerator.py (two-phase detection, signal handling) and
MakeModels.py (configurable steps, XML parameter support).

Default pipeline per component:
1. Select component
2. Set reconstruction region auto + scale 2x
3. Calculate high model
4. Select marginal triangles → remove
5. Select large triangles (threshold) → remove
6. Select largest component → invert → remove
7. Clean model
8. Smooth
9. Close small holes
10. Clean model
11. Calculate texture
12. (If simplify) Rename _HighPoly → simplify × N → close large holes
    → rename _LowPoly → unwrap → reproject texture
13. Save
14. Export (per format toggles)
"""

from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.rc_common.naming import generate_filename
from modules.rc_common.rc_delegation import RCDelegationClient
from modules.rc_common.session import CheckpointManager

_log = logging.getLogger(__name__)


class ModelGeneration(RCModule):
    """Generate textured 3D models from aligned components."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__("Model Generation", logger)
        self._client: Optional[RCDelegationClient] = None
        self._abort_requested = False

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def get_parameters(self) -> dict[str, Parameter]:
        return {
            "model_enabled": Parameter(
                "Enable Model Generation", "mg_e", "model_enabled",
                bool, True,
                "Enable model generation from aligned components",
                parameter_group="Model Generation",
            ),
            "model_alignment_dir": Parameter(
                "Alignment Directory", "mg_ad", "model_alignment_dir",
                str, None,
                "Directory containing .rsalign component files",
                parameter_group="Model Generation",
                file_filter="directory",
            ),
            "model_export_dir": Parameter(
                "Model Export Directory", "mg_ed", "model_export_dir",
                str, None,
                "Directory for exported model files",
                parameter_group="Model Generation",
                file_filter="directory",
            ),
            "model_project_prefix": Parameter(
                "Project Prefix", "mg_pp", "model_project_prefix",
                str, "model",
                "Prefix for project and model names",
                parameter_group="Model Generation",
            ),
            "model_test_mode": Parameter(
                "Test Mode (First Component Only)", "mg_tm", "model_test_mode",
                bool, True,
                "Process only the first component for testing",
                parameter_group="Model Generation",
            ),
            "model_large_triangle_threshold": Parameter(
                "Large Triangle Threshold", "mg_lt", "model_large_triangle_threshold",
                float, 2.0,
                "Relative threshold for large triangle removal (selectLargeTrianglesRel)",
                parameter_group="Model Generation",
                min_value=1.0, max_value=10.0,
            ),
            "model_small_hole_max_edges": Parameter(
                "Small Hole Max Edges", "mg_sh", "model_small_hole_max_edges",
                int, 5000,
                "Maximum edges for small hole closing",
                parameter_group="Model Generation",
                min_value=100, max_value=100000,
            ),
            "model_large_hole_max_edges": Parameter(
                "Large Hole Max Edges", "mg_lh", "model_large_hole_max_edges",
                int, 600000,
                "Maximum edges for large hole closing (after simplification)",
                parameter_group="Model Generation",
                min_value=1000, max_value=1000000,
            ),
            "model_enable_simplify": Parameter(
                "Enable Simplification", "mg_sim", "model_enable_simplify",
                bool, True,
                "Enable mesh simplification (creates LowPoly + HighPoly)",
                parameter_group="Model Generation",
            ),
            "model_simplify_passes": Parameter(
                "Simplification Passes", "mg_sp", "model_simplify_passes",
                int, 2,
                "Number of simplification passes",
                parameter_group="Model Generation",
                min_value=1, max_value=5,
            ),
            "model_simplify_params": Parameter(
                "Simplify XML Params", "mg_sxml", "model_simplify_params",
                str, None,
                "Path to XML parameter file for simplification (optional)",
                parameter_group="Model Generation",
                file_filter="*.xml",
            ),
            "model_export_fbx": Parameter(
                "Export FBX", "mg_fbx", "model_export_fbx",
                bool, True,
                "Export model as FBX",
                parameter_group="Model Generation",
            ),
            "model_export_cesium": Parameter(
                "Export Cesium 3D Tiles", "mg_ces", "model_export_cesium",
                bool, True,
                "Export model as Cesium 3D Tiles",
                parameter_group="Model Generation",
            ),
            "model_export_obj": Parameter(
                "Export OBJ", "mg_obj", "model_export_obj",
                bool, False,
                "Export model as OBJ",
                parameter_group="Model Generation",
            ),
            "model_texture_params": Parameter(
                "Texture XML Params", "mg_txml", "model_texture_params",
                str, None,
                "Path to XML parameter file for texturing (optional)",
                parameter_group="Model Generation",
                file_filter="*.xml",
            ),
            "model_smooth_params": Parameter(
                "Smooth XML Params", "mg_smxml", "model_smooth_params",
                str, None,
                "Path to XML parameter file for smoothing (optional)",
                parameter_group="Model Generation",
                file_filter="*.xml",
            ),
            "model_unwrap_params": Parameter(
                "Unwrap XML Params", "mg_uxml", "model_unwrap_params",
                str, None,
                "Path to XML parameter file for UV unwrapping (optional)",
                parameter_group="Model Generation",
                file_filter="*.xml",
            ),
            "model_reprojection_params": Parameter(
                "Reprojection XML Params", "mg_rxml", "model_reprojection_params",
                str, None,
                "Path to XML parameter file for texture reprojection (optional)",
                parameter_group="Model Generation",
                file_filter="*.xml",
            ),
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_parameters(self) -> tuple[bool, str | None]:
        base_ok, base_msg = super().validate_parameters()
        if not base_ok:
            return False, base_msg

        if not self._get_param("model_enabled", False):
            return True, None

        alignment_dir = self._get_param("model_alignment_dir")
        if not alignment_dir:
            return False, "Model alignment directory is required"
        if not Path(alignment_dir).is_dir():
            return False, f"Model alignment directory not found: {alignment_dir}"

        export_dir = self._get_param("model_export_dir")
        if not export_dir:
            return False, "Model export directory is required"

        return True, None

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _get_param(self, key: str, default=None):
        """Get a parameter value with fallback."""
        p = self.params.get(key)
        if p is None:
            return default
        val = p.get_value()
        return val if val is not None else default

    def _delegate_step(
        self, step_name: str, *args: str, xml_param_key: str | None = None,
    ) -> None:
        """Delegate a single step with optional XML parameter file."""
        cmd_args = list(args)
        if xml_param_key:
            xml_path = self._get_param(xml_param_key)
            if xml_path and Path(xml_path).is_file():
                cmd_args.append(xml_path)
                self.logger.info("[%s] Using XML params: %s", step_name, xml_path)

        self.logger.info("[%s] Delegating: %s", step_name, " ".join(cmd_args))
        self._client.delegate(*cmd_args)
        self._client.wait_idle_two_phase(step_name)

    def _quick_step(self, step_name: str, *args: str) -> None:
        """Run a quick delegation step (delegate + waitCompleted)."""
        self.logger.info("[%s] %s", step_name, " ".join(args))
        self._client.run_quick(step_name, *args)

    def _setup_signal_handler(self) -> None:
        """Install SIGINT/SIGTERM handler for graceful abort."""
        def handler(signum, frame):
            self.logger.warning("Signal %d received — aborting current operation", signum)
            self._abort_requested = True
            if self._client:
                try:
                    self._client.abort_instance()
                except Exception:
                    pass

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # ------------------------------------------------------------------ #
    # Per-component pipeline
    # ------------------------------------------------------------------ #

    def _process_component(
        self,
        component_path: Path,
        export_dir: Path,
        prefix: str,
        component_index: int,
    ) -> dict:
        """Process a single component through the full model pipeline.

        Returns a result dict with success, duration, export paths.
        """
        comp_name = component_path.stem
        model_name = f"{prefix}_{comp_name}"
        start_time = time.time()

        self.logger.info("=" * 60)
        self.logger.info("Processing component %d: %s", component_index, comp_name)
        self.logger.info("=" * 60)

        tri_threshold = self._get_param("model_large_triangle_threshold", 2.0)
        small_holes = self._get_param("model_small_hole_max_edges", 5000)
        large_holes = self._get_param("model_large_hole_max_edges", 600000)
        enable_simplify = self._get_param("model_enable_simplify", True)
        simplify_passes = self._get_param("model_simplify_passes", 2)

        try:
            # Step 1: Load component
            self._quick_step("New Scene", "-newScene")
            self._delegate_step("Import Component", "-importComponent", str(component_path))

            # Step 2: Select component
            self._quick_step("Select Component", "-selectComponent", "0")

            # Step 3: Set reconstruction region
            self._quick_step("Set Region Auto", "-setReconstructionRegionAuto")
            self._quick_step("Scale Region", "-scaleReconstructionRegion", "2", "2", "2", "center", "factor")

            # Step 4: Calculate high model
            self._delegate_step("Calculate High Model", "-calculateHighModel")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 5: Mesh cleanup
            self._quick_step("Select Marginal", "-selectMarginalTriangles")
            self._quick_step("Remove Marginal", "-removeSelectedTriangles")

            self._quick_step("Select Large", "-selectLargeTrianglesRel", str(tri_threshold))
            self._quick_step("Remove Large", "-removeSelectedTriangles")

            self._quick_step("Select Largest", "-selectLargestModelComponent")
            self._quick_step("Invert Selection", "-invertTrianglesSelection")
            self._quick_step("Remove Floating", "-removeSelectedTriangles")

            # Step 6: Clean + smooth + close holes
            self._quick_step("Clean Model", "-cleanModel")
            self._delegate_step("Smooth", "-smooth", xml_param_key="model_smooth_params")
            self._quick_step("Close Small Holes", "-closeHoles", str(small_holes))
            self._quick_step("Clean Model 2", "-cleanModel")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 7: Texture
            self._delegate_step("Calculate Texture", "-calculateTexture", xml_param_key="model_texture_params")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 8: Simplification (optional)
            high_poly_name = f"{model_name}_HighPoly"
            low_poly_name = f"{model_name}_LowPoly"
            export_model_name = model_name  # Default: use the single model

            if enable_simplify:
                self._quick_step("Rename HighPoly", "-renameSelectedModel", high_poly_name)

                for pass_num in range(1, simplify_passes + 1):
                    self.logger.info("[Simplify Pass %d/%d]", pass_num, simplify_passes)
                    self._delegate_step(
                        f"Simplify Pass {pass_num}",
                        "-simplify",
                        xml_param_key="model_simplify_params",
                    )

                # Close large holes after simplification
                self._quick_step("Close Large Holes", "-closeHoles", str(large_holes))
                self._quick_step("Clean Simplified", "-cleanModel")

                self._quick_step("Rename LowPoly", "-renameSelectedModel", low_poly_name)

                # Unwrap and reproject texture
                self._delegate_step("Unwrap", "-unwrap", xml_param_key="model_unwrap_params")
                self._delegate_step(
                    "Reproject Texture",
                    "-reprojectTexture", high_poly_name, low_poly_name,
                    xml_param_key="model_reprojection_params",
                )
                export_model_name = low_poly_name

            # Step 9: Save
            self._quick_step("Save", "-save")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 10: Export
            export_dir.mkdir(parents=True, exist_ok=True)
            exported_files = []

            if self._get_param("model_export_fbx", True):
                fbx_path = export_dir / f"{export_model_name}.fbx"
                self._delegate_step("Export FBX", "-exportModel", export_model_name, str(fbx_path))
                exported_files.append(str(fbx_path))
                self.logger.info("Exported FBX: %s", fbx_path)

            if self._get_param("model_export_cesium", True):
                # Cesium uses high-poly if available, otherwise the single model
                cesium_model = high_poly_name if enable_simplify else model_name
                cesium_path = export_dir / f"{cesium_model}_cesium.json"
                self._quick_step("Select for Cesium", "-selectModel", cesium_model)
                self._delegate_step("Export Cesium", "-export3dTiles", str(cesium_path))
                exported_files.append(str(cesium_path))
                self.logger.info("Exported Cesium 3D Tiles: %s", cesium_path)

            if self._get_param("model_export_obj", False):
                obj_path = export_dir / f"{export_model_name}.obj"
                self._delegate_step("Export OBJ", "-exportModel", export_model_name, str(obj_path))
                exported_files.append(str(obj_path))
                self.logger.info("Exported OBJ: %s", obj_path)

            elapsed = time.time() - start_time
            self.logger.info(
                "Component %s complete in %.1fs. Exports: %d files",
                comp_name, elapsed, len(exported_files),
            )

            return {
                "Success": True,
                "Component": comp_name,
                "Duration": elapsed,
                "ExportedFiles": exported_files,
            }

        except TimeoutError as e:
            elapsed = time.time() - start_time
            self.logger.error("Component %s timed out after %.1fs: %s", comp_name, elapsed, e)
            return {"Success": False, "Component": comp_name, "Duration": elapsed, "Error": str(e)}
        except Exception as e:
            elapsed = time.time() - start_time
            self.logger.error("Component %s failed after %.1fs: %s", comp_name, elapsed, e)
            return {"Success": False, "Component": comp_name, "Duration": elapsed, "Error": str(e)}

    # ------------------------------------------------------------------ #
    # Main run
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, object] | None:
        if not self._get_param("model_enabled", True):
            self.logger.info("[Model Generation] Skipped (disabled)")
            return {"Success": True, "Skipped": True}

        alignment_dir = Path(self._get_param("model_alignment_dir"))
        export_dir = Path(self._get_param("model_export_dir"))
        prefix = self._get_param("model_project_prefix", "model")
        test_mode = self._get_param("model_test_mode", True)

        # Find .rsalign files
        components = sorted(alignment_dir.glob("*.rsalign"))
        if not components:
            self.logger.error("No .rsalign files found in %s", alignment_dir)
            return {"Success": False, "Error": "No .rsalign files found"}

        if test_mode:
            self.logger.info("Test mode: processing first component only")
            components = components[:1]

        self.logger.info(
            "[Model Generation] Found %d component(s) in %s",
            len(components), alignment_dir,
        )

        # Find RC executable
        rc_exe = self._get_param("rc_executable_path")
        if not rc_exe:
            rc_exe_paths = [
                Path("C:/Program Files/Epic Games/RealityScan_2.1/RealityScan.exe"),
                Path("C:/Program Files/Epic Games/RealityScan_2.0/RealityScan.exe"),
                Path("C:/Program Files/Epic Games/RealityScan/RealityScan.exe"),
            ]
            for p in rc_exe_paths:
                if p.exists():
                    rc_exe = str(p)
                    break
        if not rc_exe:
            return {"Success": False, "Error": "RealityScan executable not found"}

        instance_name = self._get_param("rc_instance_name", "*")

        self._client = RCDelegationClient(
            rc_exe=rc_exe,
            instance_name=instance_name,
            poll_interval=2.0,
            logger=self.logger,
        )

        # Set up progress callback
        if self._progress_reporter:
            self._client.on_progress = lambda op, pct, elapsed, eta: self._report_progress(
                op, pct, elapsed, eta,
            )

        # Install signal handler
        self._setup_signal_handler()

        # Clear queue
        self._client.clear_queue()

        if not self._client.verify_connection():
            return {"Success": False, "Error": "Cannot connect to RealityScan instance"}

        # Set up checkpointing
        ckpt_dir = self._get_param("rc_checkpoint_dir")
        if not ckpt_dir:
            ckpt_dir = str(export_dir / ".checkpoints")
        checkpoint = CheckpointManager(ckpt_dir)
        completed_items = checkpoint.get_completed_items("model_generation")

        bar = self._initialize_loading_bar(len(components), "Model Generation")
        start_time = time.time()
        results = []

        for idx, comp_path in enumerate(components, 1):
            comp_name = comp_path.stem

            if comp_name in completed_items:
                self.logger.info("[%s] Skipping (checkpoint: already completed)", comp_name)
                self._update_loading_bar(bar)
                continue

            if self._abort_requested:
                self.logger.warning("Abort requested — stopping model generation")
                break

            self._log_file_processing(
                "Model Generation", str(comp_path), idx, len(components),
            )

            result = self._process_component(comp_path, export_dir, prefix, idx)
            results.append(result)

            if result.get("Success"):
                completed_items.append(comp_name)
                checkpoint.save_checkpoint("model_generation", completed_items, {
                    "prefix": prefix,
                    "export_dir": str(export_dir),
                })

            self._update_loading_bar(bar)

        self._finish_loading_bar(bar)
        total_elapsed = time.time() - start_time

        # Summary
        successful = [r for r in results if r.get("Success")]
        failed = [r for r in results if not r.get("Success")]

        self.logger.info("=" * 60)
        self.logger.info("MODEL GENERATION SUMMARY")
        self.logger.info("=" * 60)
        self.logger.info("Components processed: %d", len(results))
        self.logger.info("Successful: %d", len(successful))
        self.logger.info("Failed: %d", len(failed))
        self.logger.info("Total time: %.1fs", total_elapsed)

        for r in results:
            status = "OK" if r.get("Success") else f"FAIL: {r.get('Error', 'unknown')}"
            self.logger.info(
                "  %s — %s (%.1fs)",
                r.get("Component", "?"), status, r.get("Duration", 0),
            )
        self.logger.info("=" * 60)

        all_exports = []
        for r in successful:
            all_exports.extend(r.get("ExportedFiles", []))

        return {
            "Success": len(failed) == 0,
            "ComponentsProcessed": len(results),
            "Successful": len(successful),
            "Failed": len(failed),
            "Duration": total_elapsed,
            "ExportedFiles": all_exports,
            "Results": results,
        }
