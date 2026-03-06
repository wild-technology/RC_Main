"""Model generation module for the RC pipeline.

Generates textured 3D models from aligned components via RealityScan
delegation. Per-component checkpointing and signal handling are
supported.

Mesh cleanup (triangle removal, smoothing, hole closing) has been moved
to the PrepareModel module.  Simplification has been removed (always
high-poly).  Export has been moved to the ModelExport module.

Pipeline per component:
1. New scene
2. Import component
3. Re-apply flight log (if found)
4. Select component
5. Set reconstruction region auto + scale 2x
6. Calculate high model
7. Calculate texture
8. Save
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
            "model_texture_params": Parameter(
                "Texture XML Params", "mg_txml", "model_texture_params",
                str, None,
                "Path to XML parameter file for texturing (optional)",
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
        """Process a single component: import, model, texture, save.

        Mesh cleanup (PrepareModel) and export (ModelExport) are now
        handled by their own pipeline modules.

        Returns a result dict with success and duration.
        """
        comp_name = component_path.stem
        start_time = time.time()

        self.logger.info("=" * 60)
        self.logger.info("Processing component %d: %s", component_index, comp_name)
        self.logger.info("=" * 60)

        try:
            # Step 1: New scene + import component
            self._quick_step("New Scene", "-newScene")
            self._delegate_step("Import Component", "-importComponent", str(component_path))

            # Re-apply flight log after importing component
            flight_log_path = self._find_flight_log()
            if flight_log_path:
                from modules.rc_common.flight_log_utils import update_flight_log_params_xml
                utm_zone = self._detect_utm_from_flight_log(flight_log_path)
                if utm_zone:
                    xml_path = update_flight_log_params_xml(flight_log_path, utm_zone, self.logger)
                    if xml_path:
                        self._delegate_step("Set Flight Log Params", "-setPropertyFromXml", xml_path)
                self._delegate_step("Import Flight Log", "-importFlightLog", flight_log_path)

            # Step 2: Select component
            self._quick_step("Select Component", "-selectComponent", "0")

            # Step 3: Set reconstruction region
            self._quick_step("Set Region Auto", "-setReconstructionRegionAuto")
            self._quick_step("Scale Region", "-scaleReconstructionRegion", "2", "2", "2", "center", "factor")

            # Step 4: Calculate high model
            self._delegate_step("Calculate High Model", "-calculateHighModel")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 5: Calculate texture
            self._delegate_step("Calculate Texture", "-calculateTexture", xml_param_key="model_texture_params")

            if self._abort_requested:
                return {"Success": False, "Error": "Aborted by user"}

            # Step 6: Save
            self._quick_step("Save", "-save")

            elapsed = time.time() - start_time
            self.logger.info(
                "Component %s complete in %.1fs",
                comp_name, elapsed,
            )

            return {
                "Success": True,
                "Component": comp_name,
                "Duration": elapsed,
            }

        except TimeoutError as e:
            elapsed = time.time() - start_time
            self.logger.error("Component %s timed out after %.1fs: %s", comp_name, elapsed, e)
            return {"Success": False, "Component": comp_name, "Duration": elapsed, "Error": str(e)}
        except Exception as e:
            elapsed = time.time() - start_time
            self.logger.error("Component %s failed after %.1fs: %s", comp_name, elapsed, e)
            return {"Success": False, "Component": comp_name, "Duration": elapsed, "Error": str(e)}

    def _find_flight_log(self) -> str | None:
        """Find flight log in base directory via glob pattern."""
        import glob
        base_dir = self._get_param("model_alignment_dir")
        if not base_dir:
            return None
        # Look for {expedition}_{dive}_UTM*.txt pattern
        exp = self.params.get("expedition_name") and self.params["expedition_name"].get_value()
        dive = self.params.get("dive_name") and self.params["dive_name"].get_value()
        if exp and dive:
            pattern = os.path.join(base_dir, f"{exp}_{dive}_UTM*.txt")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
        # Fallback: any flight log in base dir
        pattern = os.path.join(base_dir, "*_UTM*.txt")
        matches = glob.glob(pattern)
        return matches[0] if matches else None

    def _detect_utm_from_flight_log(self, path: str) -> str | None:
        """Extract UTM zone from flight log filename. E.g. 'NA173_H2102_UTM57N.txt' -> '57N'"""
        import re
        match = re.search(r'UTM(\d{1,2}[NS])', os.path.basename(path), re.IGNORECASE)
        return match.group(1) if match else None

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

        return {
            "Success": len(failed) == 0,
            "ComponentsProcessed": len(results),
            "Successful": len(successful),
            "Failed": len(failed),
            "Duration": total_elapsed,
            "Results": results,
        }
