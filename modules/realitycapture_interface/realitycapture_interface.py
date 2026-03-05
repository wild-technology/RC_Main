from __future__ import annotations
from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import subprocess
import time
import os
import shutil
import re
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional
from ..file_metadata_parser import parse_timestamp, parse_timestamp_str, parse_frame_number, parse_frame_number_str
from modules.rc_common.rc_delegation import RCDelegationClient
from modules.rc_common.session import CheckpointManager
from modules.rc_common.naming import generate_filename


class RealityCaptureAlignment(RCModule):
    def __init__(self, logger):
        super().__init__("RealityCapture Alignment", logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['rc_input_image_dir'] = Parameter(
            name='Input Image Folder',
            cli_short='r_i',
            cli_long='r_input',
            type=str,
            default_value=None,
            description='Directory containing the images to align (or folder of batched images)',
            prompt_user=True,
            disable_when_module_active=['Georeference Images', 'Batch Directory']
        )

        additional_params['rc_expedition_name'] = Parameter(
            name='Expedition Name',
            cli_short='r_exp',
            cli_long='r_expedition',
            type=str,
            default_value=None,
            description='Expedition name/number for component naming (e.g., EX2501)',
            prompt_user=True
        )

        additional_params['rc_dive_name'] = Parameter(
            name='Dive Name',
            cli_short='r_dive',
            cli_long='r_dive',
            type=str,
            default_value=None,
            description='Dive name/number for component naming (e.g., H001)',
            prompt_user=True
        )

        additional_params['rc_display_output'] = Parameter(
            name='Display Output',
            cli_short='r_d',
            cli_long='r_display_output',
            type=bool,
            default_value=False,
            description='Whether to display the RealityCapture output',
            prompt_user=True
        )

        additional_params['rc_flight_log_path'] = Parameter(
            name='Flight Log Path',
            cli_short='r_f',
            cli_long='r_flight_log',
            type=str,
            default_value=None,
            description='Path to the flight log file (optional - auto-discovered for batched folders)',
            prompt_user=True,
            disable_when_module_active=['Batch Directory', 'Georeference Images']
        )

        additional_params['rc_model_generate'] = Parameter(
            name='Generate Model',
            cli_short='r_m',
            cli_long='r_model_generate',
            type=bool,
            default_value=True,
            description='Whether to automatically generate the model',
            prompt_user=True
        )

        additional_params['rc_model_cull_poly'] = Parameter(
            name='Model Polygon Culling',
            cli_short='r_c',
            cli_long='r_model_cull_poly',
            type=bool,
            default_value=True,
            description='Whether to automatically cull large and floating polygons on the generated model',
            prompt_user=True
        )

        additional_params['rc_model_texture'] = Parameter(
            name='Model Texturing',
            cli_short='r_t',
            cli_long='r_model_texture',
            type=bool,
            default_value=True,
            description='Whether to automatically texture the generated model',
            prompt_user=True
        )

        additional_params['rc_model_simplify'] = Parameter(
            name='Model Simplification',
            cli_short='r_s',
            cli_long='r_model_simplify',
            type=bool,
            default_value=True,
            description='Whether to automatically simplify the generated model',
            prompt_user=True
        )

        additional_params['rc_executable_path'] = Parameter(
            name='RealityScan Executable Path',
            cli_short='r_x',
            cli_long='r_executable_path',
            type=str,
            default_value=r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe",
            description='Full path to RealityScan.exe/RealityCapture.exe to be used by the batch scripts',
            prompt_user=False
        )

        additional_params['rc_dry_run'] = Parameter(
            name='RC Dry Run (no launch)',
            cli_short='r_n',
            cli_long='r_dry_run',
            type=bool,
            default_value=False,
            description='If set, do not launch RealityScan; just log planned actions',
            prompt_user=False
        )

        additional_params['rc_min_component_size'] = Parameter(
            name='Minimum Component Size',
            cli_short='r_min',
            cli_long='r_min_component_size',
            type=int,
            default_value=400,
            description='Minimum number of images required in a component to keep it',
            prompt_user=False
        )

        additional_params['rc_use_delegation'] = Parameter(
            name='Use Delegation Mode',
            cli_short='r_del',
            cli_long='r_use_delegation',
            type=bool,
            default_value=False,
            description='Use delegation to a running RealityScan instance instead of direct CLI',
            parameter_group='Alignment',
        )

        additional_params['rc_instance_name'] = Parameter(
            name='RC Instance Name',
            cli_short='r_inst',
            cli_long='r_instance_name',
            type=str,
            default_value='*',
            description='RealityScan instance name for delegation (default: first available)',
            parameter_group='Alignment',
        )

        additional_params['rc_checkpoint_dir'] = Parameter(
            name='Checkpoint Directory',
            cli_short='r_ckpt',
            cli_long='r_checkpoint_dir',
            type=str,
            default_value=None,
            description='Directory for alignment checkpoints (enables resume on failure)',
            parameter_group='Alignment',
        )

        return {**super().get_parameters(), **additional_params}

    def __find_realityscan_exe(self) -> Optional[Path]:
        """
        Find RealityScan executable from parameter or common installation locations.

        Returns:
            Path to RealityScan.exe or None if not found
        """
        exe_param = self.params.get('rc_executable_path')
        if exe_param and exe_param.get_value():
            exe_path = Path(exe_param.get_value())
            if exe_path.exists():
                return exe_path

        possible_paths = [
            Path("C:/Program Files/Epic Games/RealityScan_2.0/RealityScan.exe"),
            Path("C:/Program Files/Epic Games/RealityScan/RealityScan.exe"),
            Path("C:/Program Files (x86)/Epic Games/RealityScan_2.0/RealityScan.exe"),
            Path("C:/Program Files (x86)/Epic Games/RealityScan/RealityScan.exe"),
        ]

        if shutil.which("RealityScan.exe"):
            return Path(shutil.which("RealityScan.exe"))

        for path in possible_paths:
            if path.exists():
                return path

        return None

    def __delete_existing_xmp_files(self, base_dir: Path) -> int:
        """
        Find and delete all existing .xmp files in base directory and subdirectories.

        Args:
            base_dir: Base directory to search for .xmp files

        Returns:
            Number of .xmp files deleted
        """
        self.logger.info("Searching for existing .xmp files...")

        xmp_files = list(base_dir.rglob("*.xmp"))

        if not xmp_files:
            self.logger.info("No existing .xmp files found.")
            return 0

        self.logger.info(f"Found {len(xmp_files)} existing .xmp file(s)")

        auto_delete = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y') or \
                      os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')

        if not auto_delete:
            response = input(f"Delete {len(xmp_files)} existing .xmp file(s)? (yes/no): ").strip().lower()
            if response not in ['yes', 'y']:
                self.logger.info("Skipping .xmp deletion.")
                return 0

        self.logger.info("Deleting existing .xmp files...")

        deleted_count = 0
        for xmp_file in xmp_files:
            try:
                xmp_file.unlink()
                deleted_count += 1
                self.logger.debug(f"  Deleted: {xmp_file.relative_to(base_dir)}")
            except Exception as e:
                self.logger.error(f"  ERROR deleting {xmp_file}: {str(e)}")

        self.logger.info(f"Deleted {deleted_count} .xmp file(s)")
        return deleted_count

    def __determine_camera_subfolder(self, filename):
        """
        Determine camera subfolder based on filename.
        Uses same logic as GeoreferenceImages module for consistency.
        """
        filename_lower = filename.lower()

        # Check specific camera types (most specific to least specific)
        if filename_lower.startswith('camupper'):
            return 'camupper'
        elif filename_lower.startswith('cammid'):
            return 'cammid'
        elif filename_lower.startswith('camlower'):
            return 'camlower'
        elif '_herc_' in filename_lower:
            return 'zeuss'

        # Additional prefix-based detection
        # U prefix = upper camera, C prefix = lower camera
        elif filename.startswith('U'):
            return 'camupper'
        elif filename.startswith('C'):
            return 'camlower'

        # Fallback: check for generic zeuss indicators
        elif 'zeuss' in filename_lower or 'herc' in filename_lower:
            return 'zeuss'

        else:
            return 'other'

    def __detect_utm_zone_from_flight_log(self, flight_log_path: str) -> Optional[str]:
        """
        Detect UTM zone from flight log filename.

        Args:
            flight_log_path: Path to flight log file (e.g., flight_log_17S_UTM.txt)

        Returns:
            UTM zone string with hemisphere (e.g., "17S", "57N") or None
        """
        filename = os.path.basename(flight_log_path)

        match = re.search(r'flight_log_(\d{1,2})([A-Z])_UTM', filename, re.IGNORECASE)
        if not match:
            match = re.search(r'(\d{1,2})([A-Z])_UTM', filename, re.IGNORECASE)

        if match:
            zone_num = match.group(1)
            band_letter = match.group(2).upper()

            hemisphere = 'S' if band_letter < 'N' else 'N'
            zone_str = f"{zone_num}{hemisphere}"

            self.logger.info(
                f"Detected UTM zone from filename: {zone_str} "
                f"(zone {zone_num}, latitude band {band_letter})"
            )
            return zone_str

        self.logger.warning(
            f"Could not detect UTM zone from filename: {filename}. "
            f"Expected format: flight_log_<ZONE><BAND>_UTM.txt"
        )
        return None

    def __get_flight_log_path(self, zone_path: Optional[str] = None) -> Optional[str]:
        """
        Resolve the most appropriate flight log path.

        Args:
            zone_path: Zone directory to search first

        Returns:
            Path to flight log or None if not found
        """

        def validate_csv(path: str) -> bool:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    first = f.readline()
                    return (';' in first) and ('filename' in first or 'Image' in first)
            except Exception:
                return False

        if zone_path and os.path.isdir(zone_path):
            primary = os.path.join(zone_path, 'flight_log.txt')
            if os.path.isfile(primary) and validate_csv(primary):
                return primary

            candidates = []
            for name in os.listdir(zone_path):
                if name.lower().endswith('.txt') and (
                        name.lower().startswith('flight_log') or '_utm' in name.lower()
                ):
                    candidates.append(os.path.join(zone_path, name))

            for cand in sorted(candidates):
                if validate_csv(cand):
                    return cand

        if 'rc_flight_log_path' in self.params:
            path = self.params['rc_flight_log_path'].get_value()
            if path and path.strip() and os.path.isfile(path):
                return path

        if 'geo_input_image_dir' in self.params and self.params['geo_input_image_dir'].get_value():
            cand = os.path.join(self.params['geo_input_image_dir'].get_value(), 'flight_log.txt')
            if os.path.isfile(cand):
                return cand

        if 'output_dir' in self.params:
            cand = os.path.join(self.params['output_dir'].get_value(), 'flight_log.txt')
            if os.path.isfile(cand):
                return cand

        return None

    def __validate_flight_log(self, flight_log_path: str, input_folder: str) -> None:
        """
        Validate flight log matches images in input folder.

        Args:
            flight_log_path: Path to flight log file
            input_folder: Path to image folder
        """
        try:
            with open(flight_log_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                header = next(reader)
                flight_log_files = set([row[0] for row in reader if row and row[0]])

            input_images = set()
            for root, dirs, files in os.walk(input_folder):
                for f in files:
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heif')):
                        input_images.add(f)

            matched = input_images & flight_log_files
            missing_in_log = input_images - flight_log_files

            match_rate = len(matched) / len(input_images) if input_images else 0

            self.logger.info(
                f"Flight log validation: {len(matched)}/{len(input_images)} images matched ({match_rate * 100:.1f}%)"
            )

            if missing_in_log:
                self.logger.warning(
                    f"Flight log missing {len(missing_in_log)} images. "
                    f"Sample: {list(missing_in_log)[:3]}"
                )

            if match_rate < 0.5:
                self.logger.error(
                    f"Flight log match rate critically low ({match_rate * 100:.1f}%). "
                    f"Verify flight log corresponds to this image set."
                )

        except Exception as e:
            self.logger.warning(f"Could not validate flight log: {e}")

    def __run_realityscan_command(self, realityscan_exe: Path, command_list: list[str]) -> subprocess.CompletedProcess:
        """
        Execute RealityScan CLI command directly.

        Args:
            realityscan_exe: Path to RealityScan.exe
            command_list: List of command arguments

        Returns:
            CompletedProcess object

        Raises:
            subprocess.CalledProcessError: If command fails
        """
        full_command = [str(realityscan_exe)] + command_list

        self.logger.info(f"Executing: {' '.join(full_command[:3])}... ({len(command_list)} args)")

        try:
            result = subprocess.run(
                full_command,
                check=True,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            return result
        except subprocess.CalledProcessError as e:
            self.logger.error(f"RealityScan command failed with code {e.returncode}")
            if e.stdout:
                self.logger.error(f"STDOUT: {e.stdout[-500:]}")
            if e.stderr:
                self.logger.error(f"STDERR: {e.stderr[-500:]}")
            raise

    def __count_xmp_files(self, base_dir: Path) -> int:
        """
        Count XMP files in directory tree.

        Args:
            base_dir: Base directory to search

        Returns:
            Count of .xmp files
        """
        return len(list(base_dir.rglob("*.xmp")))

    def __align_and_export_zone(
            self,
            realityscan_exe: Path,
            input_folder: str,
            output_folder: str,
            zone_name: str,
            expedition: str,
            dive: str,
            flight_log_path: Optional[str],
            generate_model: bool,
            timestamp: str
    ) -> dict:
        """
        Align images in a zone and export components with XMP sidecars.

        Args:
            realityscan_exe: Path to RealityScan executable
            input_folder: Folder containing images
            output_folder: Where to save components
            zone_name: Name of the zone
            expedition: Expedition name/number
            dive: Dive name/number
            flight_log_path: Path to flight log (optional)
            generate_model: Whether to generate model
            timestamp: Timestamp for component naming

        Returns:
            Dict with success status, component count, and XMP count
        """
        self.logger.info(f"Processing zone: {zone_name}")
        self.logger.info(f"  Input: {input_folder}")
        self.logger.info(f"  Output: {output_folder}")

        Path(output_folder).mkdir(parents=True, exist_ok=True)
        input_path = Path(input_folder)

        # Delete existing XMP files before alignment
        deleted_xmp_count = self.__delete_existing_xmp_files(input_path)

        # Build RealityScan command
        command = [
            "-newScene",
            "-addFolder", input_folder
        ]

        if flight_log_path and os.path.isfile(flight_log_path):
            self.logger.info(f"  Flight log: {flight_log_path}")
            self.__validate_flight_log(flight_log_path, input_folder)

            utm_zone = self.__detect_utm_zone_from_flight_log(flight_log_path)
            if utm_zone:
                self.logger.info(f"  UTM Zone: {utm_zone}")

            command.extend(["-importFlightLog", flight_log_path])
        else:
            self.logger.warning("  No flight log - alignment will use image metadata only")

        # Set minimum component size to 100 images
        min_component_size = 100

        # Add alignment and XMP export settings
        command.extend([
            "-align",
            "-set", "xmpCamera=3",
            "-set", "xmpMerge=true",
            "-set", "xmpRig=true",
            "-set", "xmpCalibGroups=true",
            "-set", "xmpFlags=true",
            "-set", "xmpExGps=true",
            "-exportXMP",
            "-setMinComponentSize", str(min_component_size),
            "-exportLatestComponents", f"{output_folder}\\",
            "-quit"
        ])

        self.logger.info(f"  Minimum component size: {min_component_size} images")

        try:
            self.__run_realityscan_command(realityscan_exe, command)
        except subprocess.CalledProcessError:
            return {
                'Success': False,
                'Component Count': 0,
                'XMP Count': 0,
                'XMP Deleted': deleted_xmp_count,
                'Error': 'RealityScan command failed'
            }

        # Count generated XMP files
        xmp_count = self.__count_xmp_files(input_path)
        self.logger.info(f"  Generated {xmp_count} XMP sidecar file(s)")

        # Get all exported components (already filtered by RealityScan)
        generated_components = sorted(Path(output_folder).glob("Component*.rsalign"))

        if not generated_components:
            self.logger.warning(f"No components with >={min_component_size} images generated for {zone_name}")
            return {
                'Success': False,
                'Component Count': 0,
                'XMP Count': xmp_count,
                'XMP Deleted': deleted_xmp_count,
                'Error': f'No components with >={min_component_size} images'
            }

        self.logger.info(f"  Found {len(generated_components)} component(s) with >={min_component_size} images")

        # Rename components with expedition metadata
        # Format: {expedition}_{dive}_{zone_name}_{timestamp}_{counter}.rsalign
        renamed_count = 0
        for counter, component_file in enumerate(generated_components, start=1):
            new_name = f"{expedition}_{dive}_{zone_name}_{timestamp}_{counter}.rsalign"
            new_path = Path(output_folder) / new_name

            if new_path.exists():
                auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y')
                if not auto_overwrite:
                    self.logger.warning(f"Component {new_path} already exists. Overwrite? (y/n)")
                    if input().lower() != 'y':
                        component_file.unlink()
                        continue
                new_path.unlink()

            component_file.rename(new_path)
            renamed_count += 1
            self.logger.info(f"  Renamed: {component_file.name} -> {new_name}")

        return {
            'Success': True,
            'Component Count': renamed_count,
            'XMP Count': xmp_count,
            'XMP Deleted': deleted_xmp_count,
            'Zone': zone_name
        }

    def __interactive_dry_run_selection(self, process_data: list[dict]) -> list[dict]:
        """
        Interactive dry-run mode for zone selection and validation.

        Args:
            process_data: List of zone processing configurations

        Returns:
            Filtered list of zones to process
        """
        print("\n=== RealityScan Dry-Run Debug ===")
        print(f"Discovered {len(process_data)} zone(s)/folder(s) to process.")

        for idx, item in enumerate(process_data, start=1):
            folder = item['input_folder']
            try:
                files = [f for f in os.listdir(folder)
                         if f.lower().endswith((".jpg", ".jpeg", ".png", ".heif"))]
                low = sum(1 for f in files if "camlower" in f.lower())
                mid = sum(1 for f in files if "cammid" in f.lower())
                up = sum(1 for f in files if "camupper" in f.lower())
                zeuss = sum(1 for f in files if "_herc_" in f.lower())
                print(
                    f"  [{idx}] {os.path.basename(folder)}: {len(files)} images "
                    f"(lower:{low} mid:{mid} upper:{up} zeuss:{zeuss})"
                )
            except Exception as e:
                print(f"  [{idx}] {os.path.basename(folder)}: error reading ({e})")

        selection = input("Enter indexes to include (comma/range, blank=all, q=abort): ").strip()

        if selection.lower() == 'q':
            return []

        if selection:
            def parse_selection(s):
                result = set()
                parts = [p.strip() for p in s.split(',') if p.strip()]
                for p in parts:
                    if '-' in p:
                        a, b = p.split('-', 1)
                        for k in range(int(a), int(b) + 1):
                            result.add(k)
                    else:
                        result.add(int(p))
                return sorted(result)

            idxs = parse_selection(selection)
            process_data = [item for i, item in enumerate(process_data, start=1) if i in idxs]
            print(f"Selected {len(process_data)} folder(s).")

        for idx, item in enumerate(process_data, start=1):
            folder = item['input_folder']
            print(f"\n-- Inspecting [{idx}] {folder}")

            fl = item.get('flight_log_path') or 'NONE'
            print(f"Flight log: {fl}")

            if os.path.isfile(fl):
                try:
                    with open(fl, 'r', encoding='utf-8', errors='ignore') as fh:
                        head = ''.join([next(fh) for _ in range(3)])
                        print('Flight log preview:\n' + head)
                except Exception:
                    pass

            files = [f for f in os.listdir(folder)
                     if f.lower().endswith((".jpg", ".jpeg", ".png", ".heif"))]
            sample = files[:3] + files[-3:] if len(files) >= 6 else files[:]
            print("Sample images:")
            for s in sample:
                print("  ", s)

            ans = input("Continue with this folder? (Y)es/(s)kip/(q)uit: ").strip().lower()
            if ans == 'q':
                return []
            if ans == 's':
                item['__skip'] = True
                continue

            print(f"Zone name: {item['zone_name']}")
            newname = input("Enter new zone name or blank to keep: ").strip()
            if newname:
                item['zone_name'] = newname

        return [it for it in process_data if not it.get('__skip')]

    def __get_component_file_name_from_zone(self, zone_folder: str) -> str:
        """
        Gets component name for a zone folder containing camera subfolders.

        Args:
            zone_folder: Path to zone folder

        Returns:
            Component base filename (zone name only, no timestamp)
        """
        if zone_folder is None or not os.path.isdir(zone_folder):
            raise ValueError("Zone folder is not specified or is invalid")

        all_images = []
        for root, dirs, files in os.walk(zone_folder):
            for f in files:
                if f.lower().endswith((".png", ".heif", ".jpg", ".jpeg")):
                    all_images.append(f)

        if not all_images:
            raise ValueError(f"No image files found in zone folder: {zone_folder}")

        # Just return the zone folder name - expedition/dive/timestamp added during export
        zone_name = os.path.basename(zone_folder)
        return zone_name

    def _run_delegation_mode(
        self,
        realityscan_exe: Path,
        process_data: list[dict],
        expedition: str,
        dive: str,
        output_dir: str,
        timestamp: str,
    ) -> dict:
        """Run alignment via delegation to a running RealityScan instance.

        This mode delegates commands to an already-running RC instance using
        two-phase idle detection for robust operation monitoring.
        """
        instance_name = self.params.get('rc_instance_name', Parameter('', '', '', str, '*')).get_value() or '*'

        client = RCDelegationClient(
            rc_exe=realityscan_exe,
            instance_name=instance_name,
            poll_interval=2.0,
            logger=self.logger,
        )

        # Set up progress callback
        if self._progress_reporter:
            client.on_progress = lambda op, pct, elapsed, eta: self._report_progress(
                op, pct, elapsed, eta,
            )

        # Clear any stale queue
        client.clear_queue()

        # Verify connection
        if not client.verify_connection():
            self.logger.error("Cannot connect to RealityScan instance '%s'", instance_name)
            return {'Success': False, 'Error': f'Cannot connect to RC instance: {instance_name}'}

        self.logger.info("Connected to RealityScan instance: %s", instance_name)

        # Set up checkpoint manager
        ckpt_dir = None
        if self.params.get('rc_checkpoint_dir') and self.params['rc_checkpoint_dir'].get_value():
            ckpt_dir = self.params['rc_checkpoint_dir'].get_value()
        elif self.params.get('output_dir') and self.params['output_dir'].get_value():
            ckpt_dir = os.path.join(self.params['output_dir'].get_value(), '.checkpoints')

        checkpoint = CheckpointManager(ckpt_dir) if ckpt_dir else None
        completed_zones = checkpoint.get_completed_items("alignment") if checkpoint else []

        output_data = {
            'Success': True,
            'Output Directory': output_dir,
            'Expedition': expedition,
            'Dive': dive,
            'Timestamp': timestamp,
            'Zone Count': len(process_data),
            'Zones': {},
            'Total XMP Deleted': 0,
            'Total XMP Created': 0,
            'Mode': 'delegation',
        }

        bar = self._initialize_loading_bar(len(process_data), "Aligning Zones (Delegation)")
        zone_summary = []

        for data in process_data:
            zone_name = data['zone_name']
            input_folder = data['input_folder']
            flight_log_path = data.get('flight_log_path')

            # Check checkpoint — skip completed zones
            if zone_name in completed_zones:
                self.logger.info("[%s] Skipping (already completed per checkpoint)", zone_name)
                zone_summary.append({'zone': zone_name, 'components': 0, 'status': 'SKIPPED'})
                self._update_loading_bar(bar, 1)
                continue

            self.logger.info("[%s] Starting delegation alignment for: %s", zone_name, input_folder)

            try:
                # Delete existing XMP files
                input_path = Path(input_folder)
                deleted_xmp = self._RealityCaptureAlignment__delete_existing_xmp_files(input_path)

                # Step 1: New scene
                self.logger.info("[%s] Creating new scene", zone_name)
                client.run_quick("New Scene", "-newScene")

                # Step 2: Add images
                self.logger.info("[%s] Adding images from %s", zone_name, input_folder)
                client.delegate("-addFolder", input_folder)
                client.wait_idle_two_phase("Add Images")

                # Step 3: Import flight log
                if flight_log_path and os.path.isfile(flight_log_path):
                    self.logger.info("[%s] Importing flight log: %s", zone_name, flight_log_path)
                    self._RealityCaptureAlignment__validate_flight_log(flight_log_path, input_folder)
                    client.run_quick("Import Flight Log", "-importFlightLog", flight_log_path)
                else:
                    self.logger.warning("[%s] No flight log available", zone_name)

                # Step 4: Align (the long operation — no timeout)
                self.logger.info("[%s] Starting alignment (this may take hours)...", zone_name)
                client.delegate("-align")
                client.wait_idle_two_phase("Alignment")

                # Step 5: Export XMP sidecars
                self.logger.info("[%s] Exporting XMP sidecars", zone_name)
                for xmp_cmd in [
                    ("-set", "xmpCamera=3"),
                    ("-set", "xmpMerge=true"),
                    ("-set", "xmpRig=true"),
                    ("-set", "xmpCalibGroups=true"),
                    ("-set", "xmpFlags=true"),
                    ("-set", "xmpExGps=true"),
                ]:
                    client.run_quick("Set XMP", *xmp_cmd)
                client.run_quick("Export XMP", "-exportXMP")

                xmp_count = self._RealityCaptureAlignment__count_xmp_files(input_path)

                # Step 6: Export components
                min_component_size = self.params.get('rc_min_component_size', Parameter('', '', '', int, 100)).get_value()
                self.logger.info("[%s] Exporting components (min size: %d)", zone_name, min_component_size)

                Path(output_dir).mkdir(parents=True, exist_ok=True)
                client.run_quick("Set Min Component", "-setMinComponentSize", str(min_component_size))

                export_path = output_dir + os.sep
                client.delegate("-exportLatestComponents", export_path)
                client.wait_idle_two_phase("Export Components")

                # Wait for files to stabilize
                try:
                    stable_files = client.wait_for_stable_files(
                        output_dir, "Component*.rsalign",
                        min_stable_sec=5.0, timeout=120.0,
                    )
                except TimeoutError:
                    stable_files = sorted(Path(output_dir).glob("Component*.rsalign"))

                # Step 7: Rename components
                utm_zone = self._RealityCaptureAlignment__detect_utm_zone_from_flight_log(
                    flight_log_path
                ) if flight_log_path else None

                renamed_count = 0
                for counter, comp_file in enumerate(sorted(Path(output_dir).glob("Component*.rsalign")), 1):
                    if utm_zone:
                        new_name = generate_filename(
                            expedition, dive, utm_zone,
                            zone_number=int(zone_name.replace('zone_', '')) if zone_name.startswith('zone_') else None,
                            component=f"comp{counter:02d}",
                            timestamp=datetime.now(),
                            extension=".rsalign",
                        )
                    else:
                        new_name = f"{expedition}_{dive}_{zone_name}_{timestamp}_{counter}.rsalign"

                    new_path = Path(output_dir) / new_name
                    if new_path.exists():
                        new_path.unlink()
                    comp_file.rename(new_path)
                    renamed_count += 1
                    self.logger.info("[%s] Renamed: %s -> %s", zone_name, comp_file.name, new_name)

                # Save checkpoint
                if checkpoint:
                    completed_zones.append(zone_name)
                    checkpoint.save_checkpoint("alignment", completed_zones, {
                        "expedition": expedition,
                        "dive": dive,
                        "output_dir": output_dir,
                    })

                result = {
                    'Success': True,
                    'Component Count': renamed_count,
                    'XMP Count': xmp_count,
                    'XMP Deleted': deleted_xmp,
                    'Zone': zone_name,
                }
                output_data['Zones'][zone_name] = result
                output_data['Total XMP Deleted'] += deleted_xmp
                output_data['Total XMP Created'] += xmp_count
                zone_summary.append({
                    'zone': zone_name,
                    'components': renamed_count,
                    'status': 'SUCCESS',
                })

            except TimeoutError as e:
                self.logger.error("[%s] Timeout: %s", zone_name, e)
                output_data['Zones'][zone_name] = {'Success': False, 'Error': str(e)}
                zone_summary.append({'zone': zone_name, 'components': 0, 'status': 'TIMEOUT'})
            except Exception as e:
                self.logger.error("[%s] Error: %s", zone_name, e)
                output_data['Zones'][zone_name] = {'Success': False, 'Error': str(e)}
                zone_summary.append({'zone': zone_name, 'components': 0, 'status': 'FAILED'})

            self._update_loading_bar(bar, 1)

        # Summary
        total_components = sum(z.get('Component Count', 0) for z in output_data['Zones'].values())
        output_data['Total Components'] = total_components

        self.logger.info("=" * 80)
        self.logger.info("ALIGNMENT SUMMARY (Delegation Mode)")
        self.logger.info("=" * 80)
        self.logger.info("Expedition: %s | Dive: %s", expedition, dive)
        header = f"{'Zone':<30} {'Components':<15} {'Status':<10}"
        self.logger.info(header)
        self.logger.info("-" * 60)
        for item in zone_summary:
            self.logger.info(f"{item['zone']:<30} {item['components']:<15} {item['status']:<10}")
        self.logger.info("-" * 60)
        self.logger.info(f"{'TOTAL':<30} {total_components:<15}")
        self.logger.info("=" * 80)

        return output_data

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        realityscan_exe = self.__find_realityscan_exe()
        if not realityscan_exe:
            self.logger.error("RealityScan.exe not found in standard locations")
            return {'Success': False, 'Error': 'RealityScan.exe not found'}

        self.logger.info(f"Using RealityScan: {realityscan_exe}")

        # Get expedition and dive metadata
        expedition = self.params['rc_expedition_name'].get_value()
        dive = self.params['rc_dive_name'].get_value()

        if not expedition or not dive:
            self.logger.error("Expedition and Dive names are required")
            return {'Success': False, 'Error': 'Missing expedition/dive metadata'}

        self.logger.info(f"Expedition: {expedition}")
        self.logger.info(f"Dive: {dive}")

        output_dir = os.path.join(self.params['output_dir'].get_value(), "aligned_components")
        generate_model = self.params['rc_model_generate'].get_value()
        is_dry_run = bool(self.params.get('rc_dry_run').get_value()) if self.params.get('rc_dry_run') else False

        # More human-readable timestamp: YYYYMMDD_HHMM
        # Example: 20251018_1430 instead of 1018_1430
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")

        process_data = []

        input_folder = None
        is_batched_structure = False

        if 'rc_input_image_dir' in self.params and self.params['rc_input_image_dir'].get_value():
            input_folder = self.params['rc_input_image_dir'].get_value()
            if os.path.isdir(input_folder):
                subfolders = [f for f in os.listdir(input_folder)
                              if os.path.isdir(os.path.join(input_folder, f))]
                is_batched_structure = any(f.startswith('zone_') for f in subfolders)
        else:
            input_folder = os.path.join(self.params['output_dir'].get_value(), "batched_images_by_zone")
            is_batched_structure = True

        if is_batched_structure:
            self.logger.info("Detected batched folder structure")
            zone_folders = [f for f in os.listdir(input_folder)
                            if os.path.isdir(os.path.join(input_folder, f)) and f.startswith('zone_')]

            if not zone_folders:
                self.logger.error(f"No zone folders found in {input_folder}")
                return {'Success': False, 'Error': 'No zone folders found'}

            for zone_folder in zone_folders:
                zone_path = os.path.join(input_folder, zone_folder)
                flight_log_path = self.__get_flight_log_path(zone_path)

                if not flight_log_path:
                    self.logger.warning(f"No flight log found in {zone_folder}")

                try:
                    zone_name = self.__get_component_file_name_from_zone(zone_path)
                except ValueError as e:
                    self.logger.error(f"Cannot generate component name for {zone_folder}: {e}")
                    continue

                process_data.append({
                    'input_folder': zone_path,
                    'output_folder': output_dir,
                    'zone_name': zone_name,
                    'flight_log_path': flight_log_path
                })
        else:
            flight_log_path = self.__get_flight_log_path(input_folder)

            try:
                zone_name = os.path.basename(input_folder)
                process_data.append({
                    'input_folder': input_folder,
                    'output_folder': output_dir,
                    'zone_name': zone_name,
                    'flight_log_path': flight_log_path
                })
            except Exception as e:
                self.logger.error(f"Error queueing folder: {e}")

        if not process_data:
            self.logger.error("No folders were queued for processing")
            return {'Success': False, 'Error': 'No folders queued'}

        if is_dry_run and not os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y'):
            process_data = self.__interactive_dry_run_selection(process_data)
            if not process_data:
                return {'Success': False, 'Aborted': True}

        output_data = {
            'Success': True,
            'Output Directory': output_dir,
            'Expedition': expedition,
            'Dive': dive,
            'Timestamp': timestamp,
            'Zone Count': len(process_data),
            'Zones': {},
            'Total XMP Deleted': 0,
            'Total XMP Created': 0
        }

        # Check for delegation mode
        use_delegation = self.params.get('rc_use_delegation')
        if use_delegation and use_delegation.get_value():
            return self._run_delegation_mode(
                realityscan_exe, process_data, expedition, dive, output_dir, timestamp,
            )

        bar = self._initialize_loading_bar(len(process_data), "Aligning Zones")

        zone_summary = []

        for data in process_data:
            zone_name = data['zone_name']

            try:
                if is_dry_run:
                    self.logger.info(
                        f"[DRY RUN] Would align: zone='{zone_name}', "
                        f"input='{data['input_folder']}', "
                        f"flight_log='{data['flight_log_path'] or 'NONE'}'"
                    )
                    result = {'Success': True, 'DryRun': True, 'Component Count': 0}
                    output_data['Zones'][zone_name] = result
                    zone_summary.append({
                        'zone': zone_name,
                        'components': 0,
                        'status': 'DRY_RUN'
                    })
                else:
                    result = self.__align_and_export_zone(
                        realityscan_exe,
                        data['input_folder'],
                        data['output_folder'],
                        zone_name,
                        expedition,
                        dive,
                        data['flight_log_path'],
                        generate_model,
                        timestamp
                    )
                    output_data['Zones'][zone_name] = result
                    output_data['Total XMP Deleted'] += result.get('XMP Deleted', 0)
                    output_data['Total XMP Created'] += result.get('XMP Count', 0)

                    zone_summary.append({
                        'zone': zone_name,
                        'components': result.get('Component Count', 0),
                        'status': 'SUCCESS' if result.get('Success') else 'FAILED'
                    })
            except Exception as e:
                self.logger.error(f"Error processing {zone_name}: {e}")
                output_data['Zones'][zone_name] = {
                    'Success': False,
                    'Error': str(e)
                }
                zone_summary.append({
                    'zone': zone_name,
                    'components': 0,
                    'status': 'FAILED'
                })

            self._update_loading_bar(bar, 1)

        total_components = sum(
            z.get('Component Count', 0)
            for z in output_data['Zones'].values()
        )
        output_data['Total Components'] = total_components

        # Print detailed summary table
        self.logger.info("=" * 80)
        self.logger.info("ALIGNMENT SUMMARY")
        self.logger.info("=" * 80)
        self.logger.info(f"Expedition: {expedition}")
        self.logger.info(f"Dive: {dive}")
        self.logger.info(f"Timestamp: {timestamp}")
        self.logger.info(f"Output Location: {output_dir}")
        self.logger.info("")
        self.logger.info(f"XMP files deleted: {output_data['Total XMP Deleted']}")
        self.logger.info(f"XMP files created: {output_data['Total XMP Created']} (saved next to original images)")
        self.logger.info("")

        # Summary table header
        header = f"{'Zone':<30} {'Components':<15} {'Status':<10}"
        self.logger.info(header)
        self.logger.info("-" * 80)

        # Summary table rows
        for item in zone_summary:
            row = f"{item['zone']:<30} {item['components']:<15} {item['status']:<10}"
            self.logger.info(row)

        self.logger.info("-" * 80)
        self.logger.info(f"{'TOTAL':<30} {total_components:<15}")
        self.logger.info("")
        self.logger.info(f"Total components exported: {total_components}")
        self.logger.info(f"Zones processed: {len(process_data)}")
        self.logger.info(f"Successful: {sum(1 for x in zone_summary if x['status'] == 'SUCCESS')}")
        self.logger.info(f"Failed: {sum(1 for x in zone_summary if x['status'] == 'FAILED')}")
        self.logger.info("=" * 80)

        return output_data

    def validate_parameters(self) -> tuple[bool, Optional[str]]:
        success, message = super().validate_parameters()
        if not success:
            return success, message

        required_params = [
            'rc_display_output',
            'rc_model_generate',
            'rc_model_cull_poly',
            'rc_model_texture',
            'rc_model_simplify',
            'rc_min_component_size',
            'rc_expedition_name',
            'rc_dive_name'
        ]

        for param in required_params:
            if param not in self.params:
                return False, f'Parameter not found: {param}'

        # Validate expedition and dive names are provided
        expedition = self.params['rc_expedition_name'].get_value()
        dive = self.params['rc_dive_name'].get_value()

        if not expedition or not expedition.strip():
            return False, 'Expedition name is required'

        if not dive or not dive.strip():
            return False, 'Dive name is required'

        output_dir = os.path.join(self.params['output_dir'].get_value(), 'aligned_components')

        if os.path.isdir(output_dir) and os.listdir(output_dir):
            auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y') or \
                             os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')

            if auto_overwrite:
                self.logger.warning('Aligned components folder exists. Auto-overwriting.')
                shutil.rmtree(output_dir)
            else:
                self.logger.warning('Aligned components folder already exists. Overwrite? (y/n)')
                if input().lower() != 'y':
                    return False, 'Aligned components folder not created'
                shutil.rmtree(output_dir)

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        return True, None