"""Camera Setup module -- applies per-camera calibration settings via RealityScan delegation.

For each camera profile defined in camera_profiles.json, this module:
1. Selects images matching the camera's keyword pattern
2. Configures calibration mode, focal length, distortion model
3. Sets absolute pose and prior calibration
4. Assigns calibration and lens groups

All commands are sent to a running RealityScan instance through the
delegation client using ``run_quick`` (each operation completes quickly).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.rc_common.camera_utils import load_camera_profiles
from modules.rc_common.rc_delegation import RCDelegationClient

_log = logging.getLogger(__name__)

# Default path to camera_profiles.json (same location used by camera_utils)
_DEFAULT_PROFILES_PATH = str(
    Path(__file__).resolve().parents[2] / "config" / "camera_profiles.json"
)

# RealityScan distortion model numeric codes
DISTORTION_CODES: dict[str, int] = {
    "Division": 1,
    "Brown3": 2,
    "Brown3WithTangential2": 3,
}


class CameraSetup(RCModule):
    """Apply per-camera calibration settings to images in a running RealityScan instance."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__("Camera Setup", logger)

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def get_parameters(self) -> dict[str, Parameter]:
        return {
            "cam_setup_enabled": Parameter(
                name="Enable Camera Setup",
                cli_short="cs",
                cli_long="cam_setup_enabled",
                type=bool,
                default_value=False,
                description="Apply per-camera calibration settings via RealityScan delegation",
                parameter_group="Camera Setup",
            ),
            "cam_profiles_path": Parameter(
                name="Camera Profiles Path",
                cli_short="csp",
                cli_long="cam_profiles_path",
                type=str,
                default_value=_DEFAULT_PROFILES_PATH,
                description="Path to camera_profiles.json configuration file",
                parameter_group="Camera Setup",
                file_filter="*.json",
            ),
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_parameters(self) -> tuple[bool, str | None]:
        valid, msg = super().validate_parameters()
        if not valid:
            return valid, msg

        enabled = self.params.get("cam_setup_enabled")
        if enabled and enabled.get_value():
            profiles_param = self.params.get("cam_profiles_path")
            profiles_path = profiles_param.get_value() if profiles_param else _DEFAULT_PROFILES_PATH
            path = Path(profiles_path)
            if not path.exists():
                return False, f"Camera profiles file not found: {path}"
            # Validate that file is loadable
            try:
                data = load_camera_profiles(path)
                if not data.get("cameras"):
                    return False, f"No cameras defined in {path}"
            except Exception as exc:
                return False, f"Failed to load camera profiles from {path}: {exc}"

        return True, None

    # ------------------------------------------------------------------ #
    # Main execution
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, object] | None:
        """Apply camera calibration settings for each camera profile.

        Returns
        -------
        dict with keys:
            status: "Success" or "Error"
            message: human-readable summary
            skipped: True if module was disabled
            camera_counts: dict mapping camera name to number of commands sent
        """
        enabled_param = self.params.get("cam_setup_enabled")
        if not enabled_param or not enabled_param.get_value():
            self.logger.info("[Camera Setup] Module disabled, skipping.")
            return {"status": "Success", "message": "Skipped (disabled)", "skipped": True}

        # Load camera profiles
        profiles_param = self.params.get("cam_profiles_path")
        profiles_path = profiles_param.get_value() if profiles_param else _DEFAULT_PROFILES_PATH
        profiles = load_camera_profiles(profiles_path)
        cameras = profiles.get("cameras", [])

        self.logger.info("[Camera Setup] Loaded %d camera profiles.", len(cameras))

        # Get delegation client from session state
        delegation: Optional[RCDelegationClient] = None
        if self._session_state and hasattr(self._session_state, "delegation_client"):
            delegation = self._session_state.delegation_client

        if delegation is None:
            return {
                "status": "Error",
                "message": "No delegation client available. A running RealityScan instance is required.",
            }

        # Clear any pending commands before starting
        delegation.clear_queue()

        camera_counts: dict[str, int] = {}
        total_cameras = len(cameras)

        for cam_idx, camera in enumerate(cameras, start=1):
            cam_name = camera["name"]
            keywords = camera.get("keywords", [])
            focal_mm = camera["focal_length_mm"]
            distortion_model = camera["distortion_model"]
            calib_group = camera["calibration_group"]
            lens_group = camera["lens_group"]

            distortion_code = DISTORTION_CODES.get(distortion_model)
            if distortion_code is None:
                self.logger.error(
                    "[Camera Setup] Unknown distortion model '%s' for camera '%s'. Skipping.",
                    distortion_model,
                    cam_name,
                )
                continue

            self.logger.info(
                "[Camera Setup] Configuring %s (%d/%d): focal=%dmm, distortion=%s(%d), "
                "calib_group=%d, lens_group=%d",
                cam_name,
                cam_idx,
                total_cameras,
                focal_mm,
                distortion_model,
                distortion_code,
                calib_group,
                lens_group,
            )

            # Step 1: Deselect all images
            delegation.run_quick(
                f"{cam_name} deselect all",
                "-deselectAllImages",
            )

            # Step 2: Select images matching each keyword pattern
            for keyword in keywords:
                delegation.run_quick(
                    f"{cam_name} select '{keyword}'",
                    "-selectImage",
                    f"g/{keyword}/",
                )

            # Step 3: Set calibration mode (1 = calibrate from metadata)
            delegation.run_quick(
                f"{cam_name} set calibration mode",
                "-editInputSelection",
                "inpCalibration=1",
            )

            # Step 4: Set focal length
            delegation.run_quick(
                f"{cam_name} set focal length",
                "-editInputSelection",
                f"inpFocal={focal_mm}",
            )

            # Step 5: Set distortion model
            delegation.run_quick(
                f"{cam_name} set distortion enabled",
                "-editInputSelection",
                "inpDistortion=1",
            )
            delegation.run_quick(
                f"{cam_name} set distortion model",
                "-editInputSelection",
                f"inpDistortionModel={distortion_code}",
            )

            # Step 6: Set absolute pose (2 = use external registration)
            delegation.run_quick(
                f"{cam_name} set absolute pose",
                "-editInputSelection",
                "inpAbsolutePose=2",
            )

            # Step 7: Set prior calibration (1 = use prior)
            delegation.run_quick(
                f"{cam_name} set prior calibration",
                "-editInputSelection",
                "inpPriorCalibration=1",
            )

            # Step 8: Set calibration group
            delegation.run_quick(
                f"{cam_name} set calibration group",
                "-setPriorCalibrationGroup",
                str(calib_group),
            )

            # Step 9: Set lens group
            delegation.run_quick(
                f"{cam_name} set lens group",
                "-setPriorLensGroup",
                str(lens_group),
            )

            # Count commands sent for this camera (deselect + selects + 7 settings)
            commands_sent = 1 + len(keywords) + 7
            camera_counts[cam_name] = commands_sent

            self.logger.info(
                "[Camera Setup] %s configured (%d commands sent).",
                cam_name,
                commands_sent,
            )

            # Report progress
            progress_pct = (cam_idx / total_cameras) * 100.0
            self._report_progress(
                operation="Camera Setup",
                progress_pct=progress_pct,
                message=f"Configured {cam_name}",
            )

        self.logger.info(
            "[Camera Setup] Complete. Configured %d camera groups: %s",
            len(camera_counts),
            ", ".join(f"{k}={v} cmds" for k, v in camera_counts.items()),
        )

        return {
            "status": "Success",
            "message": f"Configured {len(camera_counts)} camera groups",
            "skipped": False,
            "camera_counts": camera_counts,
        }
