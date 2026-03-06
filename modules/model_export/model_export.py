"""Model export module for the RC pipeline.

Exports the current model from RealityScan in one or more formats
(FBX, OBJ, Cesium 3D Tiles).  This was previously the final stage of
model_generation._process_component() and has been extracted so that
export can be configured and triggered independently.

Before exporting, the user is warned to verify RC's export settings
(format, output path, coordinate system) and given the chance to
cancel.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.rc_common.rc_delegation import RCDelegationClient


_log = logging.getLogger(__name__)


class ModelExport(RCModule):
    """Export models from RealityScan in configurable formats."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__("Model Export", logger)
        self._client: Optional[RCDelegationClient] = None

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def get_parameters(self) -> dict[str, Parameter]:
        return {
            "me_enabled": Parameter(
                "Enable Model Export", "me_e", "me_enabled",
                bool, True,
                "Enable model export",
                parameter_group="Model Export",
                prompt_user=False,
            ),
            "me_export_dir": Parameter(
                "Export Directory", "me_ed", "me_export_dir",
                str, None,
                "Directory for exported models",
                parameter_group="Model Export",
                prompt_user=True,
                file_filter="directory",
            ),
            "me_export_fbx": Parameter(
                "Export FBX", "me_fbx", "me_export_fbx",
                bool, True,
                "Export FBX format",
                parameter_group="Model Export",
                prompt_user=True,
            ),
            "me_export_cesium": Parameter(
                "Export Cesium 3D Tiles", "me_ces", "me_export_cesium",
                bool, True,
                "Export Cesium 3D Tiles",
                parameter_group="Model Export",
                prompt_user=True,
            ),
            "me_export_obj": Parameter(
                "Export OBJ", "me_obj", "me_export_obj",
                bool, False,
                "Export OBJ format",
                parameter_group="Model Export",
                prompt_user=True,
            ),
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_parameters(self) -> tuple[bool, str | None]:
        base_ok, base_msg = super().validate_parameters()
        if not base_ok:
            return False, base_msg

        if not self._get_param("me_enabled", True):
            return True, None

        export_dir = self._get_param("me_export_dir")
        if not export_dir:
            return False, "Export directory is required for Model Export"

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

    def _init_client(self) -> bool:
        """Initialize the delegation client from session state or defaults."""
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
            self.logger.error("RealityScan executable not found")
            return False

        instance_name = self._get_param("rc_instance_name", "*")

        self._client = RCDelegationClient(
            rc_exe=rc_exe,
            instance_name=instance_name,
            poll_interval=2.0,
            logger=self.logger,
        )

        if self._progress_reporter:
            self._client.on_progress = lambda op, pct, elapsed, eta: self._report_progress(
                op, pct, elapsed, eta,
            )

        return True

    # ------------------------------------------------------------------ #
    # Main run
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, object] | None:
        if not self._get_param("me_enabled", True):
            self.logger.info("[Model Export] Skipped (disabled)")
            return {"Success": True, "Skipped": True}

        self.logger.warning("NOTE: RealityScan will export using its last-used export settings.")
        self.logger.warning("Verify format, output path, and coordinate system in RC before continuing.")

        try:
            input("Press Enter to proceed with export, or Ctrl+C to cancel...")
        except KeyboardInterrupt:
            self.logger.info("[Model Export] Cancelled by user")
            return {"Success": False, "Error": "Cancelled by user"}

        if not self._init_client():
            return {"Success": False, "Error": "RealityScan executable not found"}

        export_dir = self._get_param("me_export_dir")
        if not export_dir:
            self.logger.error("Export directory not specified")
            return {"Success": False, "Error": "Export directory not specified"}

        os.makedirs(export_dir, exist_ok=True)

        model_name = "HighPoly"  # Always high-poly in new workflow
        exported_files = []

        try:
            if self._get_param("me_export_fbx", True):
                fbx_path = os.path.join(export_dir, f"{model_name}.fbx")
                self._client.delegate("-exportModel", model_name, fbx_path)
                self._client.wait_idle_two_phase("Export FBX")
                self.logger.info("Exported FBX: %s", fbx_path)
                exported_files.append(fbx_path)

            if self._get_param("me_export_obj", False):
                obj_path = os.path.join(export_dir, f"{model_name}.obj")
                self._client.delegate("-exportModel", model_name, obj_path)
                self._client.wait_idle_two_phase("Export OBJ")
                self.logger.info("Exported OBJ: %s", obj_path)
                exported_files.append(obj_path)

            if self._get_param("me_export_cesium", True):
                cesium_path = os.path.join(export_dir, "cesium_tiles")
                self._client.run_quick("Select Model", "-selectModel", model_name)
                self._client.delegate("-export3dTiles", cesium_path)
                self._client.wait_idle_two_phase("Export 3D Tiles")
                self.logger.info("Exported Cesium 3D Tiles: %s", cesium_path)
                exported_files.append(cesium_path)

            self.logger.info("[Model Export] Complete. %d format(s) exported.", len(exported_files))

            return {
                "Success": True,
                "ExportDir": export_dir,
                "ExportedFiles": exported_files,
            }

        except TimeoutError as e:
            self.logger.error("[Model Export] Timed out: %s", e)
            return {"Success": False, "Error": str(e), "ExportedFiles": exported_files}
        except Exception as e:
            self.logger.error("[Model Export] Failed: %s", e)
            return {"Success": False, "Error": str(e), "ExportedFiles": exported_files}
