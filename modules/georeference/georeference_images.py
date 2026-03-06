from __future__ import annotations
import os
import csv
from datetime import datetime, timedelta
from PIL import Image
import sys
import utm
import math

from ..file_metadata_parser import parse_timestamp_str, parse_timestamp
from module_base.rc_module import RCModule
from module_base.parameter import Parameter


class GeoreferenceImages(RCModule):
    TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
    WCA_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
    ZEUSS_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
    WCA2025_FILENAME_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

    def __init__(self, logger):
        super().__init__("Georeference Images", logger)
        self.utm_zone = None
        self.stats: dict[str, int | float] = {}

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['geo_input_image_dir'] = Parameter(
            name='Input Image Folder',
            cli_short='g_i',
            cli_long='g_input',
            type=str,
            default_value=None,
            description='Directory containing the images to georeference',
            prompt_user=True,
            disable_when_module_active='Extract Images'
        )

        additional_params['geo_input_flight_log'] = Parameter(
            name='Input Flight Log',
            cli_short='g_f',
            cli_long='g_flight_log',
            type=str,
            default_value=None,
            description='Path to the ROV output GPS data file',
            prompt_user=True
        )

        additional_params['geo_input_type'] = Parameter(
            name='Input Data Type',
            cli_short='g_t',
            cli_long='g_type',
            type=str,
            default_value=None,
            description='Type of data to process (Zeuss, WCA, WCA2025, or All)',
            prompt_user=True
        )

        additional_params['magnetic_declination_deg'] = Parameter(
            name='Magnetic Declination (deg)',
            cli_short='g_d',
            cli_long='g_declination',
            type=float,
            default_value=0.0,
            description='Magnetic declination in degrees (east positive)',
            prompt_user=False
        )

        return {**super().get_parameters(), **additional_params}

    def __get_project_title(self) -> str:
        """
        Build project title from expedition and dive names.
        Returns format: 'NA168_H2080' or empty string if not available.
        """
        expedition = None
        dive = None

        if 'expedition_name' in self.params:
            expedition = self.params['expedition_name'].get_value()
        if 'dive_name' in self.params:
            dive = self.params['dive_name'].get_value()

        if expedition and dive:
            return f"{expedition}_{dive}"
        elif expedition:
            return expedition
        elif dive:
            return dive
        return ""

    def _get_hemisphere(self) -> str | None:
        """Return 'N' or 'S' from the detected UTM zone letter.

        UTM latitude bands C-M are southern hemisphere, N-X are northern.
        Returns None if no UTM zone has been detected yet.
        """
        if not self.utm_zone:
            return None
        zone_letter = self.utm_zone[-1].upper()
        return "N" if zone_letter >= "N" else "S"

    def _get_utm_suffix(self) -> str | None:
        """Return a UTM suffix like 'UTM57N' from the detected zone.

        Combines the zone number with the hemisphere letter.
        Returns None if no UTM zone has been detected yet.
        """
        if not self.utm_zone:
            return None
        zone_number = self.utm_zone[:-1]
        hemisphere = self._get_hemisphere()
        return f"UTM{zone_number}{hemisphere}"

    @staticmethod
    def _wrap180(angle_deg: float) -> float:
        """Wrap angle to [-180, 180] range."""
        return ((angle_deg + 180.0) % 360.0) - 180.0

    @staticmethod
    def _wrap360(angle_deg: float) -> float:
        """Wrap angle to [0, 360) range."""
        return angle_deg % 360.0

    def _get_camera_type(self, filename: str) -> str:
        """
        Determine camera type from filename.
        Returns: 'camupper', 'cammid', 'camlower', 'zeuss', or 'unknown'
        """
        if not filename:
            self.logger.warning("Empty filename provided to _get_camera_type")
            return 'unknown'

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
            return 'unknown'

    def _get_camera_profile(self, filename: str) -> dict | None:
        """Load camera profile from camera_profiles.json for the given filename."""
        if not hasattr(self, '_camera_profiles'):
            try:
                from modules.rc_common.camera_utils import load_camera_profiles, get_camera_profile
                self._camera_profiles = load_camera_profiles(
                    self.params.get('camera_profiles_path', None)
                    and self.params['camera_profiles_path'].get_value()
                )
                self._get_cam_profile_fn = get_camera_profile
            except Exception as e:
                self.logger.warning(f"Could not load camera profiles: {e}")
                self._camera_profiles = {"cameras": []}
                self._get_cam_profile_fn = lambda fn, p: None
        return self._get_cam_profile_fn(filename, self._camera_profiles)

    def _get_camera_pitch_accuracy(self, filename: str) -> float:
        """
        Return pitch accuracy (degrees) for a camera based on its name.
        Uses camera_profiles.json for values.
        """
        profile = self._get_camera_profile(filename)
        if profile:
            return profile.get("accuracy", {}).get("pitch_deg", 10.0)
        self.logger.warning(f"Unknown camera type for {filename}, using default pitch accuracy 10 degrees")
        return 10.0

    def _get_camera_pitch_offset(self, filename: str) -> float:
        """
        Return camera pitch offset (degrees down from vehicle forward axis).
        Positive values = camera pointing down relative to vehicle.
        Uses camera_profiles.json for values.
        """
        profile = self._get_camera_profile(filename)
        if profile:
            return profile.get("pitch_offset_deg", 0.0)
        self.logger.warning(f"Unknown camera type for {filename}, assuming 0 degree pitch offset")
        return 0.0

    def _apply_camera_position_offset(self, utm_x: float | None, utm_y: float | None,
                                      altitude: float | None, heading_deg: float | None,
                                      forward_m: float, lateral_m: float, down_m: float) -> tuple[
        float | None, float | None, float | None]:
        """
        Apply camera position offset from vehicle center to world coordinates.

        Args:
            utm_x, utm_y: Vehicle position in UTM
            altitude: Vehicle altitude (negative depth)
            heading_deg: Vehicle heading in degrees (0=North, 90=East, clockwise)
            forward_m: Camera offset forward from vehicle center
            lateral_m: Camera offset to right from vehicle center
            down_m: Camera offset down from vehicle center

        Returns:
            (adjusted_utm_x, adjusted_utm_y, adjusted_altitude)
        """
        if utm_x is None or utm_y is None or heading_deg is None:
            return utm_x, utm_y, altitude

        try:
            # Convert heading to radians for trig functions
            heading_rad = math.radians(heading_deg)

            # Transform offsets from vehicle frame to world frame
            east_offset = forward_m * math.sin(heading_rad)
            north_offset = forward_m * math.cos(heading_rad)

            # Lateral offset contribution (right side of vehicle)
            east_offset += lateral_m * math.cos(heading_rad)
            north_offset += lateral_m * (-math.sin(heading_rad))

            # Apply offsets
            adjusted_utm_x = utm_x + east_offset
            adjusted_utm_y = utm_y + north_offset

            # Altitude offset (down is negative altitude)
            adjusted_altitude = altitude - down_m if altitude is not None else None

            return adjusted_utm_x, adjusted_utm_y, adjusted_altitude

        except Exception as e:
            self.logger.error(f"Error applying camera position offset: {e}")
            return utm_x, utm_y, altitude

    def _convert_to_rc_orientation(self, heading_mag: float | None, pitch_vehicle: float | None,
                                   roll_vehicle: float | None, pitch_offset: float,
                                   yaw_offset: float, roll_offset: float,
                                   decl_deg: float) -> tuple[float | None, float | None, float | None]:
        """
        Convert vehicle orientation to RealityCapture conventions.

        Input conventions:
        - heading_mag: magnetic heading, 0=North, 90=East, 180=South, 270=West (clockwise)
        - pitch_vehicle: vehicle pitch from horizontal, negative=nose down
        - roll_vehicle: vehicle roll, negative=left wing down, positive=right wing down
        - pitch_offset: camera down angle from vehicle (positive = down)
        - yaw_offset: camera yaw mounting offset in degrees
        - roll_offset: camera roll mounting offset in degrees

        RealityCapture conventions (standard aerial photogrammetry):
        - Yaw: 0=North, 90=East, 180=South, 270=West
        - Pitch: 0=nadir (straight down), 90=horizontal, -90=straight up
        - Roll: 0=level, positive=right wing down
        """
        try:
            # Yaw: Convert magnetic heading to true north, apply yaw offset
            if heading_mag is not None:
                true_heading = heading_mag + decl_deg + yaw_offset
                rc_yaw = self._wrap360(true_heading)
            else:
                rc_yaw = None

            # Pitch: Convert vehicle pitch and camera offset to RC pitch
            if pitch_vehicle is not None:
                camera_pitch_from_horiz = pitch_vehicle - pitch_offset
                rc_pitch = 90.0 + camera_pitch_from_horiz
            else:
                rc_pitch = None

            # Roll: Apply roll mounting offset
            rc_roll = roll_vehicle + roll_offset if roll_vehicle is not None else roll_vehicle

            return rc_yaw, rc_pitch, rc_roll

        except Exception as e:
            self.logger.error(f"Error converting orientation: {e}")
            return None, None, None

    def __read_csv_data(self, filename):
        """Read and parse CSV data from a file, including sensor and position data."""
        if not filename:
            raise ValueError("CSV filename cannot be empty")

        if not os.path.exists(filename):
            raise FileNotFoundError(f"CSV file not found: {filename}")

        data_rows = []
        try:
            with open(filename, "r", newline='') as csvfile:
                sample = csvfile.read(4096)
                csvfile.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=',\t|;')
                except csv.Error:
                    dialect = csv.excel_tab  # fallback to tab
                reader = csv.reader(csvfile, dialect)

                try:
                    header = next(reader)
                except StopIteration:
                    raise ValueError(f"CSV file is empty: {filename}")

                # Validate required columns exist
                required_cols = ['Timestamp', 'kalman_lat', 'kalman_long', 'kalman_depth',
                                 'kalman_yaw_deg', 'kalman_pitch_deg', 'kalman_roll_deg']

                idx_map = {name: index for index, name in enumerate(header)}
                missing_cols = [col for col in required_cols if col not in idx_map]

                if missing_cols:
                    raise ValueError(f"CSV missing required columns: {missing_cols}")

                row_count = 0
                parse_errors = 0

                for row_num, row in enumerate(reader, start=2):
                    try:
                        if len(row) < len(header):
                            self.logger.warning(f"Row {row_num} has fewer columns than header, skipping")
                            continue

                        timestamp_str = row[idx_map['Timestamp']]
                        if not timestamp_str:
                            continue

                        data_rows.append({
                            "TIME": datetime.strptime(timestamp_str, self.TIMESTAMP_FORMAT),
                            "LAT": float(row[idx_map['kalman_lat']]) if row[idx_map['kalman_lat']] else None,
                            "LONG": float(row[idx_map['kalman_long']]) if row[idx_map['kalman_long']] else None,
                            "DEPTH": -abs(float(row[idx_map['kalman_depth']])) if row[
                                idx_map['kalman_depth']] else None,
                            "HEADING_MAG": float(row[idx_map['kalman_yaw_deg']]) if row[
                                idx_map['kalman_yaw_deg']] else None,
                            "PITCH": float(row[idx_map['kalman_pitch_deg']]) if row[
                                idx_map['kalman_pitch_deg']] else None,
                            "ROLL": float(row[idx_map['kalman_roll_deg']]) if row[idx_map['kalman_roll_deg']] else None
                        })
                        row_count += 1

                    except (ValueError, IndexError) as e:
                        parse_errors += 1
                        if parse_errors <= 5:
                            self.logger.warning(f"Error parsing row {row_num}: {e}")

                if parse_errors > 5:
                    self.logger.warning(f"Total parse errors: {parse_errors}")

                if not data_rows:
                    raise ValueError(f"No valid data rows found in CSV: {filename}")

                self.stats['csv_rows'] = len(data_rows)
                self.logger.info(f"Successfully loaded {len(data_rows)} data rows from CSV")

        except Exception as e:
            self.logger.error(f"Error processing CSV file {filename}: {e}")
            raise

        return data_rows

    def __convert_to_utm(self, lat, lon):
        """Convert latitude and longitude to UTM coordinates in the specified zone."""
        if lat is None or lon is None:
            return None, None

        try:
            # Validate coordinates are reasonable
            if not (-90 <= lat <= 90):
                self.logger.warning(f"Invalid latitude: {lat}")
                return None, None
            if not (-180 <= lon <= 180):
                self.logger.warning(f"Invalid longitude: {lon}")
                return None, None

            easting, northing, zone_number, zone_letter = utm.from_latlon(lat, lon)

            if self.utm_zone is None:
                self.utm_zone = f"{zone_number}{zone_letter}"
                self.logger.info(f"UTM zone detected: {self.utm_zone}")

            return easting, northing

        except Exception as e:
            self.logger.error(f"Failed to convert to UTM coordinates ({lat}, {lon}): {e}")
            return None, None

    def __is_image_file(self, filename, image_folder):
        """Verify that a file is a valid image."""
        try:
            full_path = os.path.join(image_folder, filename)
            if not os.path.exists(full_path):
                return False

            with Image.open(full_path) as im:
                im.verify()
            return True
        except Exception:
            return False

    def __parse_timestamp_from_filename(self, filename, data_type):
        """Extract and parse the timestamp from an image filename."""
        if not filename:
            self.logger.error("Empty filename provided to timestamp parser")
            return None

        try:
            if data_type == "All":
                try:
                    base_name = os.path.splitext(filename)[0]
                    timestamp_part = base_name.split('_')[1]
                    return datetime.strptime(timestamp_part, self.WCA2025_FILENAME_TIMESTAMP_FORMAT)
                except (IndexError, ValueError):
                    pass

                timestamp = parse_timestamp(filename)
                if timestamp is not None and timestamp != datetime(1970, 1, 1, 0, 0, 0):
                    return timestamp

                self.logger.error(f"Error parsing timestamp in filename: {filename}")
                return None

            elif data_type == "WCA2025":
                try:
                    base_name = os.path.splitext(filename)[0]
                    timestamp_part = base_name.split('_')[1]
                    return datetime.strptime(timestamp_part, self.WCA2025_FILENAME_TIMESTAMP_FORMAT)
                except (IndexError, ValueError) as e:
                    self.logger.error(f"Error parsing WCA2025 timestamp in filename: {filename} - {e}")
                    return None
            else:
                timestamp = parse_timestamp(filename)
                if timestamp is None or timestamp == datetime(1970, 1, 1, 0, 0, 0):
                    self.logger.error(f"Error parsing timestamp in filename: {filename}")
                    return None
                return timestamp

        except Exception as e:
            self.logger.error(f"Unexpected error parsing timestamp from {filename}: {e}")
            return None

    def __read_image_filenames(self, image_folder, data_type):
        """Read all JPEG image filenames from a folder and subdirectories, extracting their timestamps."""
        if not os.path.isdir(image_folder):
            raise ValueError(f"Image folder does not exist: {image_folder}")

        image_data = []
        jpeg_extensions = {'.jpg', '.jpeg', '.png'}

        jpeg_files = []
        for root, dirs, files in os.walk(image_folder):
            # Exclude batched_images_by_zone subdirectories from traversal
            dirs[:] = [d for d in dirs if d != 'batched_images_by_zone']

            for filename in files:
                if os.path.splitext(filename.lower())[1] in jpeg_extensions:
                    full_path = os.path.join(root, filename)
                    jpeg_files.append(full_path)

        total_files = len(jpeg_files)
        if total_files == 0:
            raise ValueError(f"No images found in {image_folder}")

        self.logger.info(f"Found {total_files} files to process")

        unreadable_files = 0
        ts_parse_failures = 0
        skipped_mask_files = 0

        bar = self._initialize_loading_bar(total_files, "Reading Image Data")
        for full_path in jpeg_files:
            filename = os.path.basename(full_path)

            # Skip mask files (e.g., camlower_20250524T061405Z.jpeg.mask.png)
            if '.mask.' in filename.lower():
                skipped_mask_files += 1
                self._update_loading_bar(bar, 1)
                continue

            image_dir = os.path.dirname(full_path)

            if self.__is_image_file(filename, image_dir):
                timestamp = self.__parse_timestamp_from_filename(filename, data_type)
                if timestamp:
                    image_data.append({
                        "FILENAME": filename,
                        "ABSOLUTE_PATH": full_path,
                        "TIMESTAMP": timestamp
                    })
                else:
                    ts_parse_failures += 1
            else:
                unreadable_files += 1
            self._update_loading_bar(bar, 1)

        self._finish_loading_bar(bar)

        self.stats['files_listed'] = total_files
        self.stats['files_unreadable'] = unreadable_files
        self.stats['timestamp_parse_failures'] = ts_parse_failures
        self.stats['images_with_valid_ts'] = len(image_data)
        self.stats['skipped_mask_files'] = skipped_mask_files

        if skipped_mask_files > 0:
            self.logger.info(f"Skipped {skipped_mask_files} mask files")

        if not image_data:
            raise ValueError(f"No images with valid timestamps found in {image_folder}")

        return image_data

    def __estimate_location(self, image_data, data_rows, input_type) -> int:
        """Estimate location and orientation for each image. Accept only matches within 2 seconds."""
        if not image_data:
            raise ValueError("No image data provided to estimate_location")

        if not data_rows:
            raise ValueError("No CSV data rows provided to estimate_location")

        MATCH_THRESHOLD_SEC = 2.0

        matches_made = 0
        exact_matches = 0
        matches_1_4 = 0
        matches_5_15 = 0
        matches_gt15 = 0
        rejected_time = 0
        rejected_no_csv = 0
        accepted_missing_utm = 0
        accepted_missing_orientation = 0

        logged_camera_offsets = set()

        bar = self._initialize_loading_bar(len(image_data), "Estimating Location")

        for image in image_data:
            filename = image.get("FILENAME", "unknown")
            image["ACCEPTED"] = False

            try:
                if data_rows:
                    closest_match = min(data_rows, key=lambda row: abs(row["TIME"] - image["TIMESTAMP"]))
                    time_diff = abs(closest_match["TIME"] - image["TIMESTAMP"])
                    diff_sec = time_diff.total_seconds()

                    if diff_sec == 0:
                        exact_matches += 1
                    elif 1 <= diff_sec <= 4:
                        matches_1_4 += 1
                    elif 5 <= diff_sec <= 15:
                        matches_5_15 += 1
                    elif diff_sec > 15:
                        matches_gt15 += 1

                    if diff_sec > MATCH_THRESHOLD_SEC:
                        rejected_time += 1
                        self._update_loading_bar(bar, 1)
                        continue

                    lat, lon = closest_match.get("LAT"), closest_match.get("LONG")
                    utm_x, utm_y = self.__convert_to_utm(lat, lon)

                    # Get camera position offsets
                    forward_m, lateral_m, down_m = self._get_camera_offsets(filename)

                    # Log camera GPS mounting offset once per camera type
                    cam_type = self._get_camera_type(filename)
                    if cam_type not in logged_camera_offsets:
                        logged_camera_offsets.add(cam_type)
                        self.logger.info(
                            "Applying camera GPS mounting offset for %s: forward=%.1fm, lateral=%.1fm, down=%.1fm",
                            filename, forward_m, lateral_m, down_m
                        )

                    # Apply position offsets to get camera location
                    camera_utm_x, camera_utm_y, camera_alt = self._apply_camera_position_offset(
                        utm_x, utm_y, closest_match.get("DEPTH"),
                        closest_match.get("HEADING_MAG"),
                        forward_m, lateral_m, down_m
                    )

                    image.update({
                        "LAT": lat,
                        "LONG": lon,
                        "UTM_X": camera_utm_x,
                        "UTM_Y": camera_utm_y,
                        "ALTITUDE_EST": camera_alt,
                        "HEADING_MAG": closest_match.get("HEADING_MAG"),
                        "PITCH_VEHICLE": closest_match.get("PITCH"),
                        "ROLL_VEHICLE": closest_match.get("ROLL"),
                        "ACCEPTED": True
                    })
                    matches_made += 1

                    if camera_utm_x is None or camera_utm_y is None:
                        accepted_missing_utm += 1

                    if (closest_match.get("HEADING_MAG") is None or
                            closest_match.get("PITCH") is None or
                            closest_match.get("ROLL") is None):
                        accepted_missing_orientation += 1

                else:
                    rejected_no_csv += 1

            except Exception as e:
                self.logger.error(f"Error processing image {filename}: {e}")
                rejected_no_csv += 1

            self._update_loading_bar(bar, 1)

        self._finish_loading_bar(bar)

        self.stats['examined_images'] = len(image_data)
        self.stats['accepted_images'] = matches_made
        self.stats['rejected_time'] = rejected_time
        self.stats['rejected_no_csv'] = rejected_no_csv
        self.stats['bucket_exact'] = exact_matches
        self.stats['bucket_1_4'] = matches_1_4
        self.stats['bucket_5_15'] = matches_5_15
        self.stats['bucket_gt15'] = matches_gt15
        self.stats['accepted_missing_utm'] = accepted_missing_utm
        self.stats['accepted_missing_orientation'] = accepted_missing_orientation
        total_rejected = rejected_time + rejected_no_csv
        self.stats['total_rejected'] = total_rejected
        self.stats['accept_rate_pct'] = (100.0 * matches_made / len(image_data)) if image_data else 0.0

        self.logger.info("Matching summary:")
        self.logger.info("  Examined images: %d", self.stats['examined_images'])
        self.logger.info("  Accepted <=2s:    %d (%.1f%%)", self.stats['accepted_images'], self.stats['accept_rate_pct'])
        self.logger.info("  Rejected >2s:    %d", self.stats['rejected_time'])
        self.logger.info("  Rejected no CSV: %d", self.stats['rejected_no_csv'])
        self.logger.info("  Time-delta buckets (all pairs, pre-threshold):")
        self.logger.info("    Exact: %d", self.stats['bucket_exact'])
        self.logger.info("    1-4s:  %d", self.stats['bucket_1_4'])
        self.logger.info("    5-15s: %d", self.stats['bucket_5_15'])
        self.logger.info("    >15s:  %d", self.stats['bucket_gt15'])
        self.logger.info("  Accepted field completeness:")
        self.logger.info("    Missing UTM:         %d", self.stats['accepted_missing_utm'])
        self.logger.info("    Missing orientation: %d", self.stats['accepted_missing_orientation'])

        return matches_made

    def _get_camera_offsets(self, filename: str) -> tuple[float, float, float]:
        """
        Return camera position offsets relative to vehicle center.

        Returns:
            (forward_offset, lateral_offset, down_offset) in meters
            - forward: positive = ahead of vehicle center
            - lateral: positive = right of vehicle center (not used currently)
            - down: positive = below vehicle center
        """
        profile = self._get_camera_profile(filename)
        if profile:
            offsets = profile.get("position_offsets", {})
            return (
                offsets.get("forward_m", 0.0),
                offsets.get("lateral_m", 0.0),
                offsets.get("down_m", 0.0),
            )
        self.logger.warning(f"Unknown camera type for {filename}, assuming no offset")
        return (0.0, 0.0, 0.0)

    def _get_camera_accuracy(self, filename: str) -> tuple[float, float, float]:
        """
        Return yaw, pitch, roll accuracy (degrees) for a camera based on its name.
        Default values: upper=10, mid=10, lower=5, zeuss=30
        """
        profile = self._get_camera_profile(filename)
        if profile:
            acc = profile.get("accuracy", {})
            return (
                acc.get("yaw_deg", 10.0),
                acc.get("pitch_deg", 10.0),
                acc.get("roll_deg", 10.0),
            )
        self.logger.warning(f"Unknown camera type for {filename}, using default accuracy 10 degrees")
        return 10.0, 10.0, 10.0

    def _get_camera_focal_length_mm(self, filename: str) -> float | None:
        """
        Return camera focal length in millimeters based on camera type.
        Mapping provided:
        - Zeuss: 24 mm
        - Upper: 13 mm
        - Mid:   14 mm
        - Lower: 15 mm
        Returns None if camera type is unknown.
        """
        profile = self._get_camera_profile(filename)
        if profile:
            return profile.get("focal_length_mm")
        self.logger.warning(f"Unknown camera type for {filename}, focal length will be omitted")
        return None

    def __generate_flight_log(self, accepted_images, output_path):
        """Generate flight log with absolute paths for zone disambiguation"""

        def fmt(value):
            """Format numeric value with 6 decimal places, empty string if None"""
            return f"{value:.6f}" if value is not None else ""

        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            f.write(
                "filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy;FocalLength;PrincipalU;PrincipalV\n")

            # Write data for all accepted images
            for image in accepted_images:
                # Use bare filename — downstream modules (batcher, RC interface) match on filenames
                flight_log_name = os.path.basename(
                    image.get("ABSOLUTE_PATH", image.get("FILENAME", ""))
                )

                # Extract position data
                utm_x = image.get("UTM_X")
                utm_y = image.get("UTM_Y")
                altitude = image.get("ALTITUDE_EST")

                # Extract accuracy data
                pos_x_acc = image.get("POS_X_ACC", 10.0)
                pos_y_acc = image.get("POS_Y_ACC", 10.0)
                alt_acc = image.get("ALT_ACC", 1.0)

                # Extract orientation data
                rc_yaw = image.get("RC_YAW")
                rc_pitch = image.get("RC_PITCH")
                rc_roll = image.get("RC_ROLL")

                # Extract orientation accuracy
                yaw_acc = image.get("YAW_ACC", 3.0)
                pitch_acc = image.get("PITCH_ACC", 5.0)
                roll_acc = image.get("ROLL_ACC", 3.0)

                # Build line (16 columns matching RC format GUID {B438A617...})
                line = ";".join([
                    flight_log_name,
                    fmt(utm_x),
                    fmt(utm_y),
                    fmt(altitude),
                    fmt(pos_x_acc),
                    fmt(pos_y_acc),
                    fmt(alt_acc),
                    fmt(rc_yaw),
                    fmt(rc_pitch),
                    fmt(rc_roll),
                    fmt(yaw_acc),
                    fmt(pitch_acc),
                    fmt(roll_acc),
                    fmt(image.get("FOCAL_LENGTH")),
                    fmt(image.get("PP_U", 0.0)),
                    fmt(image.get("PP_V", 0.0))
                ])

                f.write(line + "\n")

        # Get file stats
        file_size = os.path.getsize(output_path)
        lines_written = len(accepted_images)

        # Log results
        self.logger.info(f"Flight log: {output_path}")
        self.logger.info(f"  Lines written: {lines_written}")
        self.logger.info(f"  File size: {file_size} bytes")

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {"Success": False}

        try:
            flight_log = self.params['geo_input_flight_log'].get_value()
            if not flight_log:
                raise ValueError("Flight log path is empty")

            if 'geo_input_image_dir' in self.params:
                input_dir = self.params['geo_input_image_dir'].get_value()
            else:
                input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

            if not input_dir:
                raise ValueError("Input directory path is empty")

            input_type = self.params['geo_input_type'].get_value()
            if not input_type:
                raise ValueError("Input type is empty")

            output_data = {}

            data_rows = self.__read_csv_data(flight_log)
            image_data = self.__read_image_filenames(input_dir, input_type)
            matches_made = self.__estimate_location(image_data, data_rows, input_type)

            # Filter for accepted images only
            accepted_images = [img for img in image_data if img.get("ACCEPTED", False)]

            # Apply orientation conversions and camera-specific parameters
            mag_decl = self.params['magnetic_declination_deg'].get_value()
            for image in accepted_images:
                filename = image.get("FILENAME", "")

                # Convert orientation to RealityCapture conventions
                profile = self._get_camera_profile(filename)
                yaw_off = profile.get("yaw_offset_deg", 0.0) if profile else 0.0
                roll_off = profile.get("roll_offset_deg", 0.0) if profile else 0.0
                rc_yaw, rc_pitch, rc_roll = self._convert_to_rc_orientation(
                    image.get("HEADING_MAG"),
                    image.get("PITCH_VEHICLE"),
                    image.get("ROLL_VEHICLE"),
                    self._get_camera_pitch_offset(filename),
                    yaw_off,
                    roll_off,
                    mag_decl
                )

                # Get camera-specific accuracy values
                yaw_acc, pitch_acc, roll_acc = self._get_camera_accuracy(filename)

                # Get focal length
                focal_length = self._get_camera_focal_length_mm(filename)

                # Store all calculated values in image dict
                image.update({
                    "RC_YAW": rc_yaw,
                    "RC_PITCH": rc_pitch,
                    "RC_ROLL": rc_roll,
                    "YAW_ACC": yaw_acc,
                    "PITCH_ACC": pitch_acc,
                    "ROLL_ACC": roll_acc,
                    "FOCAL_LENGTH": focal_length,
                    "PP_U": profile.get("pp_u", 0.0) if profile else 0.0,
                    "PP_V": profile.get("pp_v", 0.0) if profile else 0.0,
                })

            # Generate output path with expedition_dive_UTM{zone}{hemisphere} naming
            project_title = self.__get_project_title()
            utm_suffix = self._get_utm_suffix()
            if project_title and utm_suffix:
                output_path = os.path.join(input_dir, f"{project_title}_{utm_suffix}.txt")
            elif project_title:
                output_path = os.path.join(input_dir, f"{project_title}.txt")
            elif utm_suffix:
                output_path = os.path.join(input_dir, f"{utm_suffix}.txt")
            else:
                output_path = os.path.join(input_dir, "flight_log.txt")

            # Generate flight log
            try:
                self.__generate_flight_log(accepted_images, output_path)
                self.stats['written_to_flight_log'] = len(accepted_images)
            except (ValueError, IOError) as e:
                self.logger.error(f"Failed to generate flight log: {e}")
                return {"Success": False, "Error": f"Flight log generation failed: {e}"}

            output_data['Success'] = True
            output_data['CSV Rows'] = int(self.stats.get('csv_rows', 0))
            output_data['Files Listed'] = int(self.stats.get('files_listed', 0))
            output_data['Files Unreadable'] = int(self.stats.get('files_unreadable', 0))
            output_data['Timestamp Parse Failures'] = int(self.stats.get('timestamp_parse_failures', 0))
            output_data['Images With Valid Timestamps'] = int(self.stats.get('images_with_valid_ts', 0))
            output_data['Images Examined'] = int(self.stats.get('examined_images', 0))
            output_data['Matched <=2s'] = matches_made
            output_data['Rejected >2s'] = int(self.stats.get('rejected_time', 0))
            output_data['Rejected No CSV'] = int(self.stats.get('rejected_no_csv', 0))
            output_data['Written To Flight Log'] = int(self.stats.get('written_to_flight_log', 0))
            output_data['Acceptance Rate %'] = float(f"{self.stats.get('accept_rate_pct', 0.0):.2f}")
            output_data['Delta Buckets'] = {
                "Exact": int(self.stats.get('bucket_exact', 0)),
                "1-4s": int(self.stats.get('bucket_1_4', 0)),
                "5-15s": int(self.stats.get('bucket_5_15', 0)),
                ">15s": int(self.stats.get('bucket_gt15', 0))
            }
            output_data['Accepted Field Gaps'] = {
                "Missing UTM": int(self.stats.get('accepted_missing_utm', 0)),
                "Missing Orientation": int(self.stats.get('accepted_missing_orientation', 0))
            }
            output_data['Output Flight Log'] = output_path

        except Exception as e:
            self.logger.error(f"Error processing data: {e}")
            return {"Success": False, "Error": str(e)}

        self.logger.info(f"CSV Rows: {output_data['CSV Rows']}")
        self.logger.info(f"Files Listed: {output_data['Files Listed']}")
        self.logger.info(f"Files Unreadable: {output_data['Files Unreadable']}")
        self.logger.info(f"Timestamp Parse Failures: {output_data['Timestamp Parse Failures']}")
        self.logger.info(f"Images With Valid Timestamps: {output_data['Images With Valid Timestamps']}")
        self.logger.info(f"Images Examined: {output_data['Images Examined']}")
        self.logger.info(f"Matched <=2s: {output_data['Matched <=2s']}")
        self.logger.info(f"Rejected >2s: {output_data['Rejected >2s']}")
        self.logger.info(f"Rejected No CSV: {output_data['Rejected No CSV']}")
        self.logger.info(f"Written To Flight Log: {output_data['Written To Flight Log']}")
        self.logger.info(f"Acceptance Rate %: {output_data['Acceptance Rate %']}")
        self.logger.info(f"Delta Buckets: {output_data['Delta Buckets']}")
        self.logger.info(f"Accepted Field Gaps: {output_data['Accepted Field Gaps']}")

        if self.utm_zone:
            self.logger.info(f"UTM Zone Detected: {self.utm_zone}")
        else:
            self.logger.warning("UTM Zone could not be determined (no valid GPS data found).")

        return output_data

    def validate_parameters(self) -> tuple[bool, str | None]:
        success, message = super().validate_parameters()
        if not success:
            return success, message

        # Validate input directory
        if 'geo_input_image_dir' in self.params:
            input_dir = self.params['geo_input_image_dir'].get_value()
        else:
            output_dir = self.params['output_dir'].get_value()
            if not output_dir:
                return False, 'Output directory parameter is not set'
            input_dir = os.path.join(output_dir, "raw_images")

        if not input_dir:
            return False, 'Input directory parameter is not set'

        if not os.path.isdir(input_dir):
            return False, f'Input directory does not exist: {input_dir}'

        # Validate flight log
        if 'geo_input_flight_log' not in self.params:
            return False, 'Flight log parameter not found'

        flight_log = self.params['geo_input_flight_log'].get_value()

        if not flight_log:
            return False, 'Flight log path is empty'

        if not os.path.isfile(flight_log):
            return False, f'Flight log file does not exist: {flight_log}'

        if os.path.splitext(flight_log)[1].lower() != '.csv':
            return False, f'Flight log is not a CSV file: {flight_log}'

        # Check file is readable
        try:
            with open(flight_log, 'r') as f:
                f.read(1)
        except Exception as e:
            return False, f'Cannot read flight log file: {e}'

        # Validate data type
        if 'geo_input_type' not in self.params:
            return False, 'Data type parameter not found'

        dtype = self.params['geo_input_type'].get_value()

        if not dtype:
            return False, 'Data type parameter is empty'

        dtype_lower = dtype.lower()
        if dtype_lower not in ["zeuss", "wca", "wca2025", "all"]:
            return False, f'Invalid data type specified: {dtype}. Must be Zeuss, WCA, WCA2025, or All'

        # Normalize data type
        if dtype_lower == "wca":
            self.params['geo_input_type'].set_value("WCA")
        elif dtype_lower == "zeuss":
            self.params['geo_input_type'].set_value("Zeuss")
        elif dtype_lower == "wca2025":
            self.params['geo_input_type'].set_value("WCA2025")
        elif dtype_lower == "all":
            self.params['geo_input_type'].set_value("All")

        # Validate magnetic declination is a reasonable value
        mag_decl = self.params['magnetic_declination_deg'].get_value()
        if not isinstance(mag_decl, (int, float)):
            return False, 'Magnetic declination must be a number'

        if abs(mag_decl) > 180:
            return False, f'Magnetic declination out of range: {mag_decl} degrees (must be between -180 and 180 degrees)'

        return True, None