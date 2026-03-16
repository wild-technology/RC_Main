"""Component Export module — exports alignment components from a running RealityScan instance.

Uses the RCDelegationClient to communicate with a running RealityScan
instance via delegation commands.  Component existence is detected by
monitoring the revision counter: if selecting a component increments the
revision, the component exists and can be exported.

Based on ``StandaloneUtilities/text_rsalign_exporter.py``, adapted to
the RCModule pipeline interface with proper two-phase idle detection and
the project naming convention.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.rc_common.naming import generate_filename
from modules.rc_common.rc_delegation import RCDelegationClient


class ComponentExportModule(RCModule):
    """Export alignment components from a running RealityScan instance.

    For each component index 0..max_component_num the module:

    1. Records the current revision via ``get_revision()``.
    2. Sends ``-setMinComponentSize <size>`` then ``-selectComponent <index>``.
    3. Checks whether the revision changed (component exists).
    4. If the component exists, exports it as an ``.rsalign`` file via
       ``-exportSelectedComponent``.
    5. Waits for the export to finish.

    Results are returned as a dict with ``component_count`` and
    ``exported_files``.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        super().__init__(
            name="ComponentExport",
            logger=logger or logging.getLogger(__name__),
        )

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def get_parameters(self) -> dict[str, Parameter]:
        return {
            "export_enabled": Parameter(
                name="export_enabled",
                cli_short="-ee",
                cli_long="--export-enabled",
                type=bool,
                default_value=True,
                description="Enable component export step.",
                parameter_group="ComponentExport",
            ),
            "export_output_dir": Parameter(
                name="export_output_dir",
                cli_short="-eo",
                cli_long="--export-output-dir",
                type=str,
                default_value=None,
                description="Directory to save exported .rsalign files.",
                parameter_group="ComponentExport",
            ),
            "export_base_name": Parameter(
                name="export_base_name",
                cli_short="-eb",
                cli_long="--export-base-name",
                type=str,
                default_value=None,
                description="Base name for exported component files.",
                parameter_group="ComponentExport",
            ),
            "export_max_component_num": Parameter(
                name="export_max_component_num",
                cli_short="-em",
                cli_long="--export-max-component-num",
                type=int,
                default_value=66,
                description="Maximum component index to try (0 to this value).",
                min_value=0,
                max_value=9999,
                parameter_group="ComponentExport",
            ),
            "export_min_component_size": Parameter(
                name="export_min_component_size",
                cli_short="-es",
                cli_long="--export-min-component-size",
                type=int,
                default_value=100,
                description="Minimum images per component (passed to -setMinComponentSize).",
                min_value=1,
                max_value=100000,
                parameter_group="ComponentExport",
            ),
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_parameters(self) -> tuple[bool, str | None]:
        valid, msg = super().validate_parameters()
        if not valid:
            return valid, msg

        enabled = self.params.get("export_enabled")
        if enabled and not enabled.get_value():
            # Skip validation when disabled — run() will short-circuit.
            return True, None

        output_dir_param = self.params.get("export_output_dir")
        if output_dir_param is None or output_dir_param.get_value() is None:
            return False, "export_output_dir is required when component export is enabled."

        return True, None

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, object] | None:
        """Execute the component export workflow.

        Returns
        -------
        dict
            ``status``: ``"Success"`` or ``"Success+Skipped"``
            ``component_count``: number of components exported
            ``exported_files``: list of exported file path strings
        """
        # Check if disabled.
        enabled_param = self.params.get("export_enabled")
        if enabled_param and not enabled_param.get_value():
            self.logger.info("[ComponentExport] Skipped (export_enabled=False).")
            return {
                "status": "Success+Skipped",
                "component_count": 0,
                "exported_files": [],
            }

        # Remind user to clean up small components before exporting.
        self.logger.warning(
            "REMINDER: Delete any small/noise components from RealityScan before exporting."
        )
        try:
            input("Press Enter when ready, or Ctrl+C to cancel...")
        except KeyboardInterrupt:
            self.logger.info("[ComponentExport] Cancelled by user.")
            return {"status": "Cancelled", "component_count": 0, "exported_files": []}

        # Resolve parameters.
        output_dir = Path(self.params["export_output_dir"].get_value())
        base_name = self.params["export_base_name"].get_value()
        max_component_num = int(self.params["export_max_component_num"].get_value())
        min_component_size = int(self.params["export_min_component_size"].get_value())

        output_dir.mkdir(parents=True, exist_ok=True)

        # Build base name from naming convention if expedition/dive/utm are
        # available in session params, otherwise fall back to explicit param.
        if base_name is None:
            base_name = self._build_base_name()
        if base_name is None:
            base_name = "component"

        # Obtain the delegation client.  The pipeline is expected to have
        # already configured an RC executable path parameter.
        rc_exe_param = self.params.get("rc_executable_path") or self.params.get("rc_exe")
        rc_exe = rc_exe_param.get_value() if rc_exe_param else None
        if rc_exe is None:
            # Fallback: try common Windows defaults.
            import shutil
            for candidate in [
                r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe",
                r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe",
                r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe",
                r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe",
                r"C:\Program Files\Capturing Reality\RealityScan 2.0\RealityScan.exe",
                r"C:\Program Files\Capturing Reality\RealityScan\RealityScan.exe",
            ]:
                if Path(candidate).exists():
                    rc_exe = candidate
                    break
            if rc_exe is None:
                rc_exe = shutil.which("RealityScan") or r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe"

        instance_param = self.params.get("rc_instance_name")
        instance_name = instance_param.get_value() if instance_param else "*"

        client = RCDelegationClient(
            rc_exe=Path(rc_exe),
            instance_name=instance_name,
            logger=self.logger,
        )

        # Wire progress callback.
        if self._progress_reporter:
            client.on_progress = lambda op, pct, elapsed, eta: (
                self._report_progress(op, pct, elapsed, eta)
            )

        # Verify connection.
        if not client.verify_connection():
            self.logger.error(
                "[ComponentExport] Cannot communicate with RealityScan. "
                "Ensure a project is loaded."
            )
            return {
                "status": "Error",
                "component_count": 0,
                "exported_files": [],
            }

        # Clear queue at startup (race-condition prevention).
        client.clear_queue()

        self.logger.info(
            "[ComponentExport] Starting export — output=%s, max_comp=%d, "
            "min_size=%d",
            output_dir, max_component_num, min_component_size,
        )

        exported_files: list[str] = []
        export_index = 0

        for comp_idx in range(max_component_num + 1):
            self.logger.info(
                "[ComponentExport] Trying component %d/%d",
                comp_idx, max_component_num,
            )

            # 1. Set minimum component size.
            client.run_quick(
                f"setMinComponentSize({min_component_size})",
                "-setMinComponentSize", str(min_component_size),
            )

            # 2. Record revision before selection.
            rev_before = client.get_revision()

            # 3. Try to select the component.
            client.run_quick(
                f"selectComponent({comp_idx})",
                "-selectComponent", str(comp_idx),
            )

            # Brief pause for RC to update internal state.
            time.sleep(0.3)

            # 4. Check revision after selection.
            rev_after = client.get_revision()

            if rev_after == rev_before:
                self.logger.info(
                    "[ComponentExport] Component %d does not exist "
                    "(revision unchanged %d).",
                    comp_idx, rev_before,
                )
                continue

            # Component exists — build output filename.
            comp_label = f"comp{export_index:02d}"
            filename = self._make_export_filename(
                base_name, comp_label, output_dir,
            )
            output_path = output_dir / filename

            self.logger.info(
                "[ComponentExport] Exporting component %d -> %s",
                comp_idx, output_path,
            )

            # 5. Export the selected component.
            client.delegate(
                "-exportSelectedComponent", str(output_path),
            )

            # 6. Wait for export to complete via two-phase idle detection.
            try:
                client.wait_idle_two_phase(
                    f"export_component_{comp_idx}",
                )
            except TimeoutError:
                # Pickup timeout — the export command may not have been
                # recognised.  Fall back to a simple waitCompleted.
                self.logger.warning(
                    "[ComponentExport] Two-phase wait timed out for "
                    "component %d, falling back to waitCompleted.",
                    comp_idx,
                )
                client.wait_completed()
                time.sleep(3.0)

            # Verify the file was created.
            if output_path.exists() and output_path.stat().st_size > 0:
                exported_files.append(str(output_path))
                export_index += 1
                self.logger.info(
                    "[ComponentExport] Successfully exported component %d "
                    "(%s, %d bytes).",
                    comp_idx,
                    output_path.name,
                    output_path.stat().st_size,
                )
            else:
                self.logger.warning(
                    "[ComponentExport] Export file not created for "
                    "component %d (%s).",
                    comp_idx, output_path,
                )

            # Report progress.
            self._report_progress(
                "ComponentExport",
                progress_pct=((comp_idx + 1) / (max_component_num + 1)) * 100,
                message=f"Exported {export_index} component(s) so far",
            )

        self.logger.info(
            "[ComponentExport] Finished — %d component(s) exported.",
            len(exported_files),
        )

        return {
            "status": "Success",
            "component_count": len(exported_files),
            "exported_files": exported_files,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _build_base_name(self) -> str | None:
        """Try to build a base name from expedition/dive/utm params."""
        expedition_param = self.params.get("expedition")
        dive_param = self.params.get("dive")
        utm_param = self.params.get("utm_zone")

        if not (expedition_param and dive_param and utm_param):
            return None

        expedition = expedition_param.get_value()
        dive = dive_param.get_value()
        utm_zone = utm_param.get_value()

        if not (expedition and dive and utm_zone):
            return None

        return generate_filename(
            expedition, dive, utm_zone, extension="",
        )

    def _make_export_filename(
        self,
        base_name: str,
        comp_label: str,
        output_dir: Path,
    ) -> str:
        """Build the export filename for a component.

        Attempts to use the naming convention if expedition/dive/utm
        parameters are available; otherwise falls back to
        ``{base_name}_{comp_label}.rsalign``.
        """
        expedition_param = self.params.get("expedition")
        dive_param = self.params.get("dive")
        utm_param = self.params.get("utm_zone")

        if expedition_param and dive_param and utm_param:
            expedition = expedition_param.get_value()
            dive = dive_param.get_value()
            utm_zone = utm_param.get_value()
            if expedition and dive and utm_zone:
                return generate_filename(
                    expedition,
                    dive,
                    utm_zone,
                    component=comp_label,
                    timestamp=datetime.now(),
                    extension=".rsalign",
                )

        return f"{base_name}_{comp_label}.rsalign"
