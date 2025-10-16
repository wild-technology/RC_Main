from __future__ import annotations
from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import subprocess
import time
import os
import shutil
import re
import csv
from typing import Optional
from ..file_metadata_parser import parse_timestamp, parse_timestamp_str, parse_frame_number, parse_frame_number_str


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
            disable_when_module_active='Batch Directory'
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

        return {**super().get_parameters(), **additional_params}

    def __check_and_create_folder(self, path):
        """
        Checks if a folder exists, if not, creates it.
        """
        if not os.path.isdir(path):
            os.mkdir(path)
            self.logger.info(f"Created folder: {path}")

    def __run_subprocess(self, command, cwd, log_folder, display_output=False):
        """
        Runs a subprocess command and waits for it to finish.
        - Streams stdout/stderr in real-time with progress logging
        - Writes output to timestamped log file
        - Honors rc_executable_path by exporting RC_EXECUTABLE for called batch scripts

        Returns:
            int: Process return code
        """
        self.__check_and_create_folder(os.path.join(cwd, log_folder))

        cur_time = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        output_path = os.path.join(cwd, log_folder, f"output_{cur_time}.txt")

        # Ensure RC_EXECUTABLE is available to scripts
        try:
            exe_param = self.params.get('rc_executable_path')
            if exe_param and exe_param.get_value():
                os.environ['RC_EXECUTABLE'] = exe_param.get_value()
        except Exception:
            pass

        # Log the command for traceability
        self.logger.info(f"Executing subprocess: {' '.join(command)} (cwd={cwd})")

        # Open log file
        output_file = open(output_path, "w", encoding='utf-8', errors='replace')
        output_file.write("COMMAND: " + ' '.join(command) + "\n")
        output_file.flush()

        # Start process with piped output
        result = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,  # Line buffered
            creationflags=subprocess.CREATE_NO_WINDOW if not display_output else subprocess.CREATE_NEW_CONSOLE
        )

        # Stream output line by line with timeout protection
        import select
        import sys

        timeout_seconds = 3600  # 1 hour max
        start_time = time.time()
        last_output_time = start_time

        try:
            while result.poll() is None:
                # Check for overall timeout
                if time.time() - start_time > timeout_seconds:
                    self.logger.error(f"Subprocess timeout after {timeout_seconds} seconds")
                    result.terminate()
                    try:
                        result.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        result.kill()
                    break

                # Read with timeout to avoid hanging
                line = result.stdout.readline()
                if not line:
                    # Check if process is stalled (no output for 5 minutes)
                    if time.time() - last_output_time > 300:
                        self.logger.warning("No output for 5 minutes - process may be stalled")
                        last_output_time = time.time()  # Reset to avoid spam
                    time.sleep(0.1)
                    continue

                last_output_time = time.time()

                # Write to log file
                output_file.write(line)
                output_file.flush()

                # Parse and log important progress messages
                line_lower = line.lower().strip()

                # Key progress milestones
                if 'adding images' in line_lower or 'addFolder' in line:
                    self.logger.info("→ Adding images to project...")
                elif 'importing flight log' in line_lower or 'importFlightLog' in line:
                    self.logger.info("→ Importing flight log with camera positions...")
                elif 'aligning images' in line_lower or line.strip() == 'Aligning images':
                    self.logger.info("→ Aligning images (this may take a while)...")
                elif 'exporting xmp' in line_lower or 'exportXMP' in line:
                    self.logger.info("→ Exporting camera calibration metadata...")
                elif 'selecting' in line_lower and 'component' in line_lower:
                    self.logger.info("→ Selecting components for export...")
                elif 'exporting component' in line_lower:
                    self.logger.info("→ Exporting alignment components...")
                elif 'generating model' in line_lower or 'calculateHighModel' in line:
                    self.logger.info("→ Generating high-poly model...")
                elif 'culling polygon' in line_lower or 'cleanModel' in line:
                    self.logger.info("→ Cleaning model geometry...")
                elif 'texturing' in line_lower and 'model' in line_lower:
                    self.logger.info("→ Generating model textures...")
                elif 'simplify' in line_lower and 'model' in line_lower:
                    self.logger.info("→ Simplifying model...")
                elif 'saving project' in line_lower:
                    self.logger.info("→ Saving project file...")
                elif 'progress:' in line_lower:
                    # Parse progress percentage if available
                    try:
                        import re
                        match = re.search(r'progress:(\d+\.?\d*)%', line_lower)
                        if match:
                            progress = float(match.group(1))
                            if progress % 10 == 0 or progress > 95:  # Log every 10% or near completion
                                self.logger.info(f"  Progress: {progress:.1f}%")
                    except Exception:
                        pass
                elif 'error' in line_lower or 'fail' in line_lower:
                    # Log errors immediately
                    self.logger.warning(f"  {line.strip()}")

        except KeyboardInterrupt:
            self.logger.warning("Subprocess interrupted by user")
            result.terminate()
            try:
                result.wait(timeout=30)
            except subprocess.TimeoutExpired:
                result.kill()
            raise
        except Exception as e:
            self.logger.error(f"Error streaming subprocess output: {e}")

        # Read any remaining output after process exits
        try:
            remaining = result.stdout.read()
            if remaining:
                output_file.write(remaining)
                output_file.flush()
        except Exception:
            pass
        finally:
            output_file.close()

        # Wait for process to complete
        result.wait()

        # Check return code
        if result.returncode != 0:
            self.logger.error(f"Command failed with exit code {result.returncode}")
            # Read last 50 lines of log for error context
            try:
                with open(output_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                    error_context = ''.join(lines[-50:])
                    self.logger.error(f"Last 50 lines of output:\n{error_context}")
            except Exception:
                pass

        return result.returncode

    # __wait_for_realitycapture_completion() method removed
    # Not needed - batch script with -delegateTo commands blocks until completion
    # subprocess.wait() in __run_subprocess() handles waiting correctly

    def __get_flight_log_path(self, batch_path=None):
        """
        Resolve the most appropriate flight log path.
        - For a zone (batch_path), prefer 'flight_log.txt' in that folder.
        - Otherwise search for common patterns like 'flight_log*.txt' and '*_UTM*.txt'.
        - Fallback to user-specified rc_flight_log_path or a top-level flight_log.txt near images/output.
        """

        def _validate_csv(path: str) -> bool:
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    first = f.readline()
                    # Expect semicolon-delimited header with known columns
                    return (';' in first) and ('filename' in first or 'Image' in first)
            except Exception:
                return False

        # Zone-level discovery
        if batch_path is not None and os.path.isdir(batch_path):
            primary = os.path.join(batch_path, 'flight_log.txt')
            if os.path.isfile(primary) and _validate_csv(primary):
                return primary
            # search variants
            candidates = []
            for name in os.listdir(batch_path):
                if name.lower().endswith('.txt') and (
                        name.lower().startswith('flight_log') or '_utm' in name.lower()
                ):
                    candidates.append(os.path.join(batch_path, name))
            # pick the first valid candidate
            for cand in sorted(candidates):
                if _validate_csv(cand):
                    return cand
            # No valid per-zone log
            return None

        # Explicit path from user (skip if empty string)
        if 'rc_flight_log_path' in self.params:
            path = self.params['rc_flight_log_path'].get_value()
            if path and path.strip():  # Check for non-empty string
                return path if os.path.isfile(path) else None

        # Fallbacks based on other module locations
        if 'geo_input_image_dir' in self.params and self.params['geo_input_image_dir'].get_value():
            cand = os.path.join(self.params['geo_input_image_dir'].get_value(), 'flight_log.txt')
            return cand if os.path.isfile(cand) else None
        else:
            cand = os.path.join(self.params['output_dir'].get_value(), 'flight_log.txt')
            return cand if os.path.isfile(cand) else None

    def __generate_flight_log_params(self, utm_zone_string: str, output_path: str) -> str:
        """
        Generate FlightLogParams.xml with correct UTM zone.

        Args:
            utm_zone_string: UTM zone string (e.g., "17S", "4N")
            output_path: Where to save the generated XML

        Returns:
            Path to generated XML file
        """
        # Parse zone number and hemisphere
        match = re.match(r'(\d+)([NS])', utm_zone_string.upper())
        if not match:
            raise ValueError(f"Invalid UTM zone format: {utm_zone_string}")

        zone_num = match.group(1)
        hemisphere = match.group(2)

        # Determine EPSG code
        # North hemisphere: 326XX, South hemisphere: 327XX
        zone_num_padded = zone_num.zfill(2)
        if hemisphere == 'N':
            epsg_code = f"326{zone_num_padded}"
        else:
            epsg_code = f"327{zone_num_padded}"

        # Build proj4 string per PROJ documentation
        # Must include +datum=WGS84 for proper ellipsoid
        if hemisphere == 'S':
            proj4_string = f"+proj=utm +zone={zone_num} +south +datum=WGS84 +units=m +no_defs"
        else:
            proj4_string = f"+proj=utm +zone={zone_num} +datum=WGS84 +units=m +no_defs"

        # Use standard EPSG naming format recognized by RealityScan
        epsg_name = f"WGS 84 / UTM zone {zone_num}{hemisphere}"

        self.logger.info(f"FlightLogParams: EPSG:{epsg_code}, Zone {zone_num}{hemisphere}, proj4: {proj4_string}")

        # Generate XML
        xml_content = f'''<Configuration id="{{93DBD041-AE1C-4631-89BC-D9430FCED843}}">
  <entry key="ifuuInhEn" value="true"/>
  <entry key="ifCSopt" value="1"/>
  <entry key="gpsLogFileFormat" value="{{B438A617-2434-5A24-C1B7-58980F28345A}}"/>
  <entry key="CoordinateSystemFlightLog" value="{proj4_string}"/>
  <entry key="CoordinateSystemFlightLogType" value="{epsg_name}"/>
  <entry key="ifKGrp" value="2"/>
  <entry key="csvFLIgn" value="true"/>
  <entry key="ifuuInh" value="0"/>
  <entry key="ifKmode" value="0x0"/>
  <entry key="csvFLSep" value="1"/>
  <entry key="ifUsePosAcc" value="true"/>
  <entry key="ifUseOriAcc" value="true"/>
</Configuration>
'''

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)

        self.logger.info(f"Generated FlightLogParams.xml for UTM Zone {zone_num}{hemisphere}")
        return output_path

    def __detect_utm_zone_from_flight_log(self, flight_log_path: str) -> Optional[str]:
        """
        Detect UTM zone from flight log filename or content.

        Args:
            flight_log_path: Path to flight log file

        Returns:
            UTM zone string (e.g., "17S") or None
        """
        # Try filename first (e.g., "flight_log_17S_UTM.txt")
        filename = os.path.basename(flight_log_path)
        match = re.search(r'_(\d+[NS])_UTM', filename, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        # Fall back to parsing coordinates from first data row
        try:
            with open(flight_log_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                header = next(reader)

                # Find X and Y columns
                x_idx = None
                y_idx = None
                for i, col in enumerate(header):
                    if 'x (east)' in col.lower():
                        x_idx = i
                    elif 'y (north)' in col.lower():
                        y_idx = i

                if x_idx is not None and y_idx is not None:
                    # Read first data row
                    row = next(reader)
                    easting = float(row[x_idx])
                    northing = float(row[y_idx])

                    # Try to infer hemisphere from northing value
                    # Northern hemisphere typically has northing > 0
                    # Southern hemisphere typically has large northing values (offset)
                    hemisphere = 'N' if northing < 10000000 else 'S'

                    # Rough zone calculation from easting
                    # Each UTM zone is 6 degrees wide, zone 1 starts at -180°
                    # This is approximate but better than hardcoded
                    zone_num = int((easting / 1000000) % 60) + 1
                    if zone_num < 1:
                        zone_num = 1
                    elif zone_num > 60:
                        zone_num = 60

                    return f"{zone_num}{hemisphere}"
        except Exception as e:
            self.logger.warning(f"Could not detect UTM zone from flight log: {e}")

        return None

    def __align_images(self, input_folder, output_folder, component_file_name, flight_log_path, flight_log_params_path,
                       display_output=False, generate_model=True, cull_polygons=False, texture_model=False,
                       simplify_model=False):
        """
        Aligns images in a folder and saves the component file to the output folder.
        """

        if not input_folder:
            raise ValueError("Input folder is not specified")

        if not os.path.isdir(input_folder):
            raise ValueError(f"Input folder {input_folder} is not a directory")

        if not os.path.isdir(output_folder):
            self.logger.info(f"Output folder does not exist. Creating folder: {output_folder}")
            os.mkdir(output_folder)

        self.__check_and_create_folder(output_folder)

        # Detect UTM zone and generate appropriate FlightLogParams.xml
        if flight_log_path and os.path.isfile(flight_log_path):
            self.logger.info(f"Using flight log: {flight_log_path}")
            utm_zone = self.__detect_utm_zone_from_flight_log(flight_log_path)
            if utm_zone:
                # Generate zone-specific FlightLogParams
                temp_params_path = os.path.join(
                    output_folder,
                    f"FlightLogParams_{utm_zone}.xml"
                )
                flight_log_params_path = self.__generate_flight_log_params(
                    utm_zone,
                    temp_params_path
                )
                self.logger.info(f"Generated FlightLogParams for zone {utm_zone}: {flight_log_params_path}")
            else:
                self.logger.warning(
                    f"Could not detect UTM zone from flight log {flight_log_path}, "
                    "using default FlightLogParams.xml"
                )
        else:
            self.logger.warning("No flight log provided - alignment will use image metadata only")
            flight_log_path = ""
            flight_log_params_path = ""

        if flight_log_params_path and not os.path.isfile(flight_log_params_path):
            flight_log_params_path = ""

        # VALIDATION: Check flight log matches images in input folder (including subfolders)
        if flight_log_path:
            try:
                with open(flight_log_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f, delimiter=';')
                    header = next(reader)
                    flight_log_files = set([row[0] for row in reader if row and row[0]])

                # Get images in input folder and all subdirectories (camera folders)
                input_images = set()
                for root, dirs, files in os.walk(input_folder):
                    for f in files:
                        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heif')):
                            input_images.add(f)

                matched = input_images & flight_log_files
                missing_in_log = input_images - flight_log_files

                match_rate = len(matched) / len(input_images) if input_images else 0

                self.logger.info(
                    f"Flight log validation: {len(matched)}/{len(input_images)} images matched ({match_rate*100:.1f}%)"
                )

                if missing_in_log:
                    self.logger.warning(
                        f"Flight log missing {len(missing_in_log)} images. "
                        f"Sample: {list(missing_in_log)[:3]}"
                    )

                if match_rate < 0.5:
                    self.logger.error(
                        f"Flight log match rate critically low ({match_rate*100:.1f}%). "
                        f"Verify flight log corresponds to this image set."
                    )

            except Exception as e:
                self.logger.warning(f"Could not validate flight log: {e}")

        # Validate XMP sidecars are present (search all camera subfolders)
        image_files = []
        xmp_files = []
        for root, dirs, files in os.walk(input_folder):
            for f in files:
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heif')):
                    image_files.append(os.path.join(root, f))
                elif f.lower().endswith('.xmp'):
                    xmp_files.append(os.path.join(root, f))

        if len(image_files) > 0:
            xmp_ratio = len(xmp_files) / len(image_files)
            self.logger.info(f"XMP sidecar coverage: {len(xmp_files)}/{len(image_files)} ({xmp_ratio*100:.1f}%)")

            if xmp_ratio < 0.5:
                self.logger.warning(
                    f"Low XMP sidecar coverage ({xmp_ratio*100:.1f}%). "
                    f"Camera calibration priors may not be properly set."
                )

            # Sample XMP to verify structure
            if xmp_files:
                sample_xmp = xmp_files[0]
                try:
                    with open(sample_xmp, 'r', encoding='utf-8') as f:
                        xmp_content = f.read()
                        has_calib_group = 'CalibrationGroup' in xmp_content
                        has_lens_group = 'LensDistortionGroup' in xmp_content
                        has_distortion = 'DistortionModel' in xmp_content

                        if has_calib_group and has_lens_group and has_distortion:
                            self.logger.info("✓ XMP sidecars contain camera calibration metadata")
                        else:
                            self.logger.warning(
                                f"⚠ XMP sidecars may be incomplete: "
                                f"CalibrationGroup={has_calib_group}, "
                                f"LensDistortionGroup={has_lens_group}, "
                                f"DistortionModel={has_distortion}"
                            )
                except Exception as e:
                    self.logger.warning(f"Could not validate XMP structure: {e}")

        # Extract zone name from input folder path
        zone_name = os.path.basename(input_folder)
        if not zone_name:
            zone_name = "alignment"

        this_file_dir = os.path.dirname(os.path.realpath(__file__))
        scripts_dir = os.path.join(this_file_dir, 'RC_CLI', 'Scripts')

        generate_model_str = "true" if generate_model else "false"
        cull_polygons_str = "true" if cull_polygons else "false"
        texture_model_str = "true" if texture_model else "false"
        simplify_model_str = "true" if simplify_model else "false"

        log_dir = os.path.join(os.path.dirname(output_folder), "logs")

        self.logger.info(f"Invoking RealityCapture alignment:")
        self.logger.info(f"  Input: {input_folder}")
        self.logger.info(f"  Output: {output_folder}")
        self.logger.info(f"  Zone: {zone_name}")
        self.logger.info(f"  Flight log: {flight_log_path or 'NONE'}")
        self.logger.info(f"  Component: {component_file_name}")

        self.logger.info("Starting RealityScan processing (batch script will block until complete)...")

        returncode = self.__run_subprocess(["cmd", "/c", "AlignImagesFromFolder.bat", 
                               input_folder, output_folder, zone_name, flight_log_path,
                               flight_log_params_path, generate_model_str, cull_polygons_str,
                               texture_model_str, simplify_model_str],
                              scripts_dir, log_dir, display_output)

        # Batch script blocks until all RealityScan commands complete
        # No timeout needed - subprocess.wait() handles it correctly
        self.logger.info("RealityScan processing completed")

        # Check if batch script failed
        if returncode != 0:
            self.logger.error(f"Batch script failed with return code {returncode}")
            # Try to kill any stuck RC instances
            try:
                subprocess.run(['taskkill', '/F', '/IM', 'RealityScan.exe'], 
                             capture_output=True, timeout=5)
            except Exception:
                pass
            return {'Success': False, 'Component Count': 0, 'Error': 'Batch script failed'}, {'Success': False}

        generated_component_files = [f for f in os.listdir(output_folder) if
                                     f.startswith("Component") and f.endswith(".rcalign")]
        component_path_base = os.path.join(output_folder, component_file_name)

        outputted_component_count = 0
        outputted_scene = False

        if not generated_component_files or len(generated_component_files) == 0:
            self.logger.error(
                f"No component files generated for {input_folder}. "
                f"Check RealityCapture logs at {log_dir}"
            )
            return {
                'Success': False, 
                'Component Count': 0,
                'Error': 'No components generated'
            }, {
                'Success': False,
                'Error': 'No scene generated'
            }

        # Filter components by size (minimum 400 images)
        # Must parse component files to check image count since CLI doesn't support filtering
        MIN_COMPONENT_SIZE = 400
        filtered_components = []

        for comp_file in generated_component_files:
            comp_path = os.path.join(output_folder, comp_file)
            try:
                # Component files are text-based - count image entries
                with open(comp_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    # Count image references (simplified - may need adjustment)
                    image_count = content.count('<Image ')

                if image_count >= MIN_COMPONENT_SIZE:
                    filtered_components.append((comp_file, image_count))
                    self.logger.info(f"Component {comp_file}: {image_count} images - ACCEPTED")
                else:
                    self.logger.info(f"Component {comp_file}: {image_count} images - REJECTED (< {MIN_COMPONENT_SIZE})")
                    # Delete small components
                    os.remove(comp_path)
            except Exception as e:
                self.logger.warning(f"Could not parse component {comp_file}: {e}")
                # Keep component if we can't parse it
                filtered_components.append((comp_file, 0))

        generated_component_files = [comp[0] for comp in filtered_components]

        if not generated_component_files:
            self.logger.warning(f"All components filtered out (< {MIN_COMPONENT_SIZE} images)")
            return {
                'Success': False,
                'Component Count': 0,
                'Error': f'No components with >={MIN_COMPONENT_SIZE} images'
            }, {
                'Success': False,
                'Error': 'No scene generated'
            }

        # use index for loop so we can index the name
        for index, generated_component_file in enumerate(generated_component_files):
            generated_component_path = os.path.join(output_folder, generated_component_file)
            component_path = f"{component_path_base}_{index}.rcalign"

            if os.path.exists(component_path):
                auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y') or \
                                 os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')
                if auto_overwrite:
                    self.logger.warning(f'Component "{component_path}" exists. Auto-overwriting due to RC_OVERWRITE/RC_NO_PROMPT.')
                    os.remove(component_path)
                else:
                    self.logger.warning(f'Component "{component_path}" already exists. Overwrite? (y/n)')
                    overwrite = input()

                    if overwrite.lower() != 'y':
                        self.logger.warning('Component not created')
                        os.remove(generated_component_path)
                        continue
                    else:
                        os.remove(component_path)

            os.rename(generated_component_path, component_path)
            outputted_component_count += 1

        generated_scene_files = [f for f in os.listdir(output_folder) if
                                 f.startswith("Scene") and f.endswith(".rcproj")]

        if generated_scene_files and len(generated_scene_files) == 1:
            generated_scene_path = os.path.join(output_folder, generated_scene_files[0])
            scene_path = f"{component_path_base}.rcproj"

            if os.path.exists(scene_path):
                auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y') or \
                                 os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')
                if auto_overwrite:
                    self.logger.warning(f'Scene "{scene_path}" exists. Auto-overwriting due to RC_OVERWRITE/RC_NO_PROMPT.')
                    os.remove(scene_path)
                    os.rename(generated_scene_path, scene_path)
                    outputted_scene = True
                else:
                    self.logger.warning(f'Scene "{scene_path}" already exists. Overwrite? (y/n)')
                    overwrite = input()

                    if overwrite.lower() != 'y':
                        self.logger.warning('Scene not created')
                        os.remove(generated_scene_path)
                    else:
                        os.remove(scene_path)
                        os.rename(generated_scene_path, scene_path)
                        outputted_scene = True
            else:
                os.rename(generated_scene_path, scene_path)
                outputted_scene = True

        component_data = {}
        component_data['Success'] = True
        component_data['Component Count'] = outputted_component_count

        scene_data = {}
        scene_data['Success'] = True
        return component_data, scene_data

    def __get_component_file_name(self, image_folder):
        """
        Gets the name of the component output file for a folder of images based on the start and end frame files.
        """

        if image_folder is None or not os.path.isdir(image_folder):
            raise ValueError("Image folder is not specified or is invalid")

        files = [f for f in os.listdir(image_folder) if f.endswith((".png", ".heif", ".jpg", ".jpeg"))]

        if not files:
            raise ValueError(f"No image files found in folder: {image_folder}")

        files.sort(key=lambda x: (parse_timestamp(x), parse_frame_number(x)))

        start_file = files[0]
        end_file = files[-1]

        start_timestamp = parse_timestamp_str(start_file)
        end_timestamp = parse_timestamp_str(end_file)

        timestamp_segment = f"{start_timestamp}-{end_timestamp}"

        component_metadata_ext = start_file.replace(start_timestamp, timestamp_segment)
        component_metadata_ext = component_metadata_ext.replace(f"_frame{parse_frame_number_str(start_file)}", "")
        component_metadata = os.path.splitext(component_metadata_ext)[0]

        component_name = f"{component_metadata}.rcalign"

        return component_name

    def __get_component_file_name_from_zone(self, zone_folder):
        """
        Gets component name for a zone folder containing camera subfolders.
        Searches all subfolders to find first and last image across all cameras.
        """
        if zone_folder is None or not os.path.isdir(zone_folder):
            raise ValueError("Zone folder is not specified or is invalid")

        # Collect all images from all subfolders
        all_images = []
        for root, dirs, files in os.walk(zone_folder):
            for f in files:
                if f.lower().endswith((".png", ".heif", ".jpg", ".jpeg")):
                    all_images.append(f)

        if not all_images:
            raise ValueError(f"No image files found in zone folder: {zone_folder}")

        all_images.sort(key=lambda x: (parse_timestamp(x), parse_frame_number(x)))

        start_file = all_images[0]
        end_file = all_images[-1]

        start_timestamp = parse_timestamp_str(start_file)
        end_timestamp = parse_timestamp_str(end_file)

        # Use zone folder name as base
        zone_name = os.path.basename(zone_folder)
        component_name = f"{zone_name}_{start_timestamp}-{end_timestamp}.rcalign"

        return component_name

    def run(self):
        # Validate parameters
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        output_dir = os.path.join(self.params['output_dir'].get_value(), "aligned_components")
        display_output = self.params['rc_display_output'].get_value()
        generate_model = self.params['rc_model_generate'].get_value()
        cull_polygons = self.params['rc_model_cull_poly'].get_value()
        texture_model = self.params['rc_model_texture'].get_value()
        simplify_model = self.params['rc_model_simplify'].get_value()

        this_file_dir = os.path.dirname(os.path.realpath(__file__))
        metadata_dir = os.path.join(this_file_dir, 'RC_CLI', 'Metadata')
        flight_log_params_path = os.path.join(metadata_dir, "FlightLogParams.xml")

        process_data = []
        is_dry_run = bool(self.params.get('rc_dry_run').get_value()) if self.params.get('rc_dry_run') else False
        no_prompt = os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')

        def queue_folder_to_process(
                local_input_folder,
                local_output_dir,
                local_flight_log_path,
                local_flight_log_params_path,
                local_display_output,
                recurse_into_subfolders=True
        ):
            """
            Queue a folder for alignment processing.

            Args:
                local_input_folder: Folder containing images (and optionally camera subfolders)
                local_output_dir: Where to save alignment components
                local_flight_log_path: Path to flight log (should match this folder's images)
                local_flight_log_params_path: Path to flight log parameters XML
                local_display_output: Whether to show RealityCapture window
                recurse_into_subfolders: If False, only process this folder (not subfolders)
            """
            if not os.path.isdir(local_input_folder):
                raise ValueError(f"Input folder {local_input_folder} is not a directory")

            # Check for images in this folder
            local_image_files = [
                f for f in os.listdir(local_input_folder)
                if f.lower().endswith((".png", ".heif", ".jpg", ".jpeg"))
            ]

            # Check for camera subfolders
            camera_subfolders = ['camlower', 'cammid', 'camupper', 'zeuss', 'other']
            subfolders = [
                f for f in os.listdir(local_input_folder)
                if os.path.isdir(os.path.join(local_input_folder, f))
            ]
            has_camera_subfolders = any(sf.lower() in camera_subfolders for sf in subfolders)

            # CRITICAL: If folder has camera subfolders, queue the PARENT folder
            # RealityScan needs all cameras together for proper multi-camera alignment
            # -addFolder will recursively find images in all subfolders
            if has_camera_subfolders:
                # Count total images across all camera subfolders
                total_images = 0
                camera_breakdown = []
                for subfolder in subfolders:
                    if subfolder.lower() in camera_subfolders:
                        subfolder_path = os.path.join(local_input_folder, subfolder)
                        try:
                            subfolder_images = [
                                f for f in os.listdir(subfolder_path)
                                if f.lower().endswith((".png", ".heif", ".jpg", ".jpeg"))
                            ]
                            if subfolder_images:
                                total_images += len(subfolder_images)
                                camera_breakdown.append(f"{subfolder}:{len(subfolder_images)}")
                        except Exception:
                            pass

                if total_images > 0:
                    self.logger.info(
                        f"Queueing zone folder: {os.path.basename(local_input_folder)} "
                        f"({total_images} total images across cameras: {', '.join(camera_breakdown)})"
                    )

                    # Queue the PARENT folder so all cameras are processed together
                    try:
                        local_component_file_name = self.__get_component_file_name_from_zone(local_input_folder)
                    except ValueError as e:
                        self.logger.error(f"Cannot generate component name for {local_input_folder}: {e}")
                        return

                    process_data.append({
                        'input_folder': local_input_folder,
                        'output_dir': local_output_dir,
                        'component_file_name': local_component_file_name,
                        'flight_log_path': local_flight_log_path,
                        'flight_log_params_path': local_flight_log_params_path,
                        'display_output': local_display_output
                    })
                else:
                    self.logger.warning(f"No images found in camera subfolders of {local_input_folder}")

            elif local_image_files:
                # Folder has images directly - queue it
                self.logger.info(
                    f"Queueing folder: {os.path.basename(local_input_folder)} "
                    f"({len(local_image_files)} images)"
                )

                try:
                    local_component_file_name = self.__get_component_file_name(local_input_folder)
                except ValueError as e:
                    self.logger.error(f"Cannot generate component name for {local_input_folder}: {e}")
                    return

                process_data.append({
                    'input_folder': local_input_folder,
                    'output_dir': local_output_dir,
                    'component_file_name': local_component_file_name,
                    'flight_log_path': local_flight_log_path,
                    'flight_log_params_path': local_flight_log_params_path,
                    'display_output': local_display_output
                })

                # If recursion enabled, process non-camera subfolders
                if recurse_into_subfolders:
                    non_camera_subfolders = [
                        sf for sf in subfolders
                        if sf.lower() not in camera_subfolders
                    ]

                    for subfolder in non_camera_subfolders:
                        subfolder_path = os.path.join(local_input_folder, subfolder)

                        # Try to find subfolder-specific flight log
                        subfolder_flight_log = self.__get_flight_log_path(subfolder_path)
                        if not subfolder_flight_log:
                            subfolder_flight_log = local_flight_log_path

                        queue_folder_to_process(
                            subfolder_path,
                            local_output_dir,
                            subfolder_flight_log,
                            local_flight_log_params_path,
                            local_display_output,
                            recurse_into_subfolders=True
                        )
            else:
                self.logger.warning(
                    f"Folder {local_input_folder} has no images and no valid subfolders, skipping"
                )

        # Determine if input is a batched folder structure or single folder
        input_folder = None
        is_batched_structure = False

        if 'rc_input_image_dir' in self.params:
            input_folder = self.params['rc_input_image_dir'].get_value()
            # Check if this is a batched folder structure (contains zone_* subfolders)
            if os.path.isdir(input_folder):
                subfolders = [f for f in os.listdir(input_folder) if os.path.isdir(os.path.join(input_folder, f))]
                is_batched_structure = any(f.startswith('zone_') for f in subfolders)
        else:
            # Fallback to default batched location
            input_folder = os.path.join(self.params['output_dir'].get_value(), "batched_images_by_zone")
            is_batched_structure = True

        if is_batched_structure:
            # Processing batched folders - auto-discover flight log per zone
            self.logger.info(f"Detected batched folder structure, will auto-discover flight logs per zone")
            batch_folders = [f for f in os.listdir(input_folder) 
                           if os.path.isdir(os.path.join(input_folder, f)) and f.startswith('zone_')]

            if not batch_folders:
                self.logger.error(f"No zone folders found in {input_folder}")
                return {
                    'Success': False,
                    'Error': 'No zone folders found in batched directory'
                }

            for batch_folder in batch_folders:
                batch_input_folder = os.path.join(input_folder, batch_folder)
                # Auto-discover flight log in this zone folder
                batch_flight_log_path = self.__get_flight_log_path(batch_input_folder)

                if not batch_flight_log_path:
                    self.logger.warning(f"No flight log found in {batch_folder}, will proceed without position priors")

                try:
                    queue_folder_to_process(batch_input_folder, output_dir, batch_flight_log_path,
                                          flight_log_params_path, display_output)
                except Exception as e:
                    self.logger.error(f"Error queueing {batch_folder}: {e}")
        else:
            # Single folder input - use global flight log path
            overall_flight_log_path = self.__get_flight_log_path()

            try:
                queue_folder_to_process(input_folder, output_dir, overall_flight_log_path, flight_log_params_path,
                                        display_output)
            except Exception as e:
                self.logger.error(f"Error queueing folder to process: {e}")

        # Validate that at least one folder was queued
        if not process_data:
            self.logger.error("No folders were queued for processing - check input directories")
            return {
                'Success': False,
                'Error': 'No folders queued for processing',
                'Component Count': 0
            }

        output_data = {}
        output_data['Success'] = True
        output_data['Output Directory'] = output_dir
        output_data['Component Count'] = len(process_data)
        output_data['Components'] = {}
        output_data['Scenes'] = {}

        bar = self._initialize_loading_bar(len(process_data), "Aligning Batches")

        # Interactive dry-run debugging flow
        if is_dry_run and not no_prompt and process_data:
            print("\n=== RealityScan Dry-Run Debug ===")
            print(f"Discovered {len(process_data)} zone(s)/folder(s) to process.")
            # List zones with basic stats
            for idx, item in enumerate(process_data, start=1):
                folder = item['input_folder']
                try:
                    files = [f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png", ".heif"))]
                    xmp_present = sum(1 for f in files if os.path.isfile(os.path.join(folder, f + ".xmp")))
                    # Camera split
                    low = sum(1 for f in files if f.lower().startswith("camlower"))
                    mid = sum(1 for f in files if f.lower().startswith("cammid"))
                    up = sum(1 for f in files if f.lower().startswith("camupper"))
                    zeuss = sum(1 for f in files if "_herc_" in f.lower())
                    print(
                        f"  [{idx}] {os.path.basename(folder)}: {len(files)} images (lower:{low} mid:{mid} upper:{up} zeuss:{zeuss}), XMPs:{xmp_present}")
                except Exception as e:
                    print(f"  [{idx}] {os.path.basename(folder)}: error reading ({e})")
            # Selection prompt
            selection = input("Enter indexes to include (comma/range, blank=all, q=abort): ").strip()
            if selection.lower() == 'q':
                return {'Success': False, 'Aborted': True}
            if selection:
                def parse_sel(s):
                    res = set()
                    parts = [p.strip() for p in s.split(',') if p.strip()]
                    for p in parts:
                        if '-' in p:
                            a, b = p.split('-', 1)
                            for k in range(int(a), int(b) + 1):
                                res.add(k)
                        else:
                            res.add(int(p))
                    return sorted(res)

                idxs = parse_sel(selection)
                process_data = [item for i, item in enumerate(process_data, start=1) if i in idxs]
                print(f"Selected {len(process_data)} folder(s).")
            # Per-zone inspection
            for idx, item in enumerate(process_data, start=1):
                folder = item['input_folder']
                print(f"\n-- Inspecting [{idx}] {folder}")
                fl = item['flight_log_path'] or 'NONE'
                print(f"Flight log: {fl}")
                if os.path.isfile(fl):
                    try:
                        with open(fl, 'r', encoding='utf-8', errors='ignore') as fh:
                            head = ''.join([next(fh) for _ in range(0, 3)])
                            print('Flight log preview:\n' + head)
                    except Exception:
                        pass
                files = [f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png", ".heif"))]
                sample = files[:3] + files[-3:] if len(files) >= 6 else files[:]
                print("Sample images:")
                for s in sample:
                    print("  ", s, " XMP:", "yes" if os.path.isfile(os.path.join(folder, s + ".xmp")) else "no")
                ans = input("Continue with this folder? (Y)es/(s)kip/(q)uit: ").strip().lower()
                if ans == 'q':
                    return {'Success': False, 'Aborted': True}
                if ans == 's':
                    item['__skip'] = True
                    continue
                # Allow rename of component base
                print(f"Planned component base: {item['component_file_name']}")
                newname = input("Enter new component base name (without extension) or blank to keep: ").strip()
                if newname:
                    if not newname.lower().endswith('.rcalign'):
                        newname = newname + '.rcalign'
                    item['component_file_name'] = newname
            # Drop skipped
            process_data = [it for it in process_data if not it.get('__skip')]
            print(f"Dry-run will simulate {len(process_data)} folder(s). Proceeding...\n")

        # process the data
        for data in process_data:
            input_folder = data['input_folder']
            output_dir = data['output_dir']
            component_file_name = data['component_file_name']
            flight_log_path = data['flight_log_path']
            flight_log_params_path = data['flight_log_params_path']
            display_output = data['display_output']

            component_path = os.path.join(output_dir, component_file_name)
            scene_path = os.path.join(output_dir, component_file_name + ".rcproj")

            try:
                if is_dry_run:
                    self.logger.info(
                        f"[DRY RUN] Would align: input='{input_folder}', output='{output_dir}', component='{component_file_name}', flight_log='{flight_log_path or 'NONE'}'")
                    output_data['Components'][component_path] = {'Success': True, 'DryRun': True}
                    output_data['Scenes'][scene_path] = {'Success': True, 'DryRun': True}
                else:
                    component_data, scene_data = self.__align_images(input_folder, output_dir, component_file_name,
                                                                     flight_log_path, flight_log_params_path,
                                                                     display_output, generate_model, cull_polygons,
                                                                     texture_model, simplify_model)
                    output_data['Components'][component_path] = component_data
                    output_data['Scenes'][scene_path] = scene_data
            except Exception as e:
                self.logger.error(f"Error aligning images: {e}")

            self._update_loading_bar(bar, 1)

        return output_data

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if not 'rc_display_output' in self.params:
            return False, 'Display output parameter not found'

        if not 'rc_model_generate' in self.params:
            return False, 'Generate model parameter not found'

        if not 'rc_model_cull_poly' in self.params:
            return False, 'Model cull polygons parameter not found'

        if not 'rc_model_texture' in self.params:
            return False, 'Model texture parameter not found'

        if not 'rc_model_simplify' in self.params:
            return False, 'Model simplify parameter not found'

        # Validate output directory
        output_dir = os.path.join(self.params['output_dir'].get_value(), 'aligned_components')

        # if the output directory already exists and it's not empty, handle overwrite with automation flags
        if os.path.isdir(output_dir) and os.listdir(output_dir):
            auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y') or \
                             os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')
            if auto_overwrite:
                self.logger.warning(
                    'Aligned components folder exists. Auto-overwriting due to RC_OVERWRITE/RC_NO_PROMPT.')
                shutil.rmtree(output_dir)
            else:
                self.logger.warning('Aligned components folder already exists. Overwrite? (y/n)')
                overwrite = input()

                if overwrite.lower() != 'y':
                    return False, 'Aligned components folder not created'
                else:
                    shutil.rmtree(output_dir)

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        return True, None