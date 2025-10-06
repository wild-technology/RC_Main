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

    @staticmethod
    def _wrap180(angle_deg: float) -> float:
        """Wrap angle to [-180, 180] range."""
        return ((angle_deg + 180.0) % 360.0) - 180.0

    @staticmethod
    def _wrap360(angle_deg: float) -> float:
        """Wrap angle to [0, 360) range."""
        return angle_deg % 360.0

    def _get_camera_pitch_accuracy(self, filename: str) -> float:
        """
        Return pitch accuracy (degrees) for a camera based on its name.
        Yaw and Roll are fixed at 3° for all cameras.
        """
        filename_lower = filename.lower()

        if filename_lower.startswith('camupper'):
            return 10.0
        elif filename_lower.startswith('cammid'):
            return 10.0
        elif filename_lower.startswith('camlower'):
            return 5.0
        elif '_herc_' in filename_lower:
            return 30.0
        else:
            self.logger.warning(f"Unknown camera type for {filename}, using default pitch accuracy 10°")
            return 10.0


    def _get_camera_pitch_offset(self, filename: str) -> float:
        """
        Return camera pitch offset (degrees down from vehicle forward axis).
        Positive values = camera pointing down relative to vehicle.
        """
        filename_lower = filename.lower()
        if 'cammid' in filename_lower:
            return 20.0  # pointing down 20°
        elif 'camupper' in filename_lower:
            return 70.0  # pointing down 70°
        elif 'camlower' in filename_lower:
            return 10.0  # pointing down 10°
        elif '_herc_' in filename_lower or 'zeuss' in filename_lower:
            return 30.0  # Zeuss pointing down 30°
        else:
            self.logger.warning(f"Unknown camera type for {filename}, assuming 0° pitch offset")
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

        # Convert heading to radians for trig functions
        heading_rad = math.radians(heading_deg)

        # Transform offsets from vehicle frame to world frame
        # In UTM: X=East, Y=North
        # Vehicle frame: forward along heading, right perpendicular to heading
        # Heading 0°=North, 90°=East (clockwise from North)

        # Forward offset contribution:
        # - East component: forward * sin(heading)
        # - North component: forward * cos(heading)
        east_offset = forward_m * math.sin(heading_rad)
        north_offset = forward_m * math.cos(heading_rad)

        # Lateral offset contribution (right side of vehicle):
        # - East component: lateral * cos(heading)
        # - North component: lateral * -sin(heading)
        east_offset += lateral_m * math.cos(heading_rad)
        north_offset += lateral_m * (-math.sin(heading_rad))

        # Apply offsets
        adjusted_utm_x = utm_x + east_offset
        adjusted_utm_y = utm_y + north_offset

        # Altitude offset (down is negative altitude)
        adjusted_altitude = altitude - down_m if altitude is not None else None

        return adjusted_utm_x, adjusted_utm_y, adjusted_altitude

    def _convert_to_rc_orientation(self, heading_mag: float | None, pitch_vehicle: float | None,
                                   roll_vehicle: float | None, camera_offset: float,
                                   decl_deg: float) -> tuple[float | None, float | None, float | None]:
        """
        Convert vehicle orientation to RealityCapture conventions.

        Input conventions:
        - heading_mag: magnetic heading, 0=North, 90=East, 180=South, 270=West (clockwise)
        - pitch_vehicle: vehicle pitch from horizontal, negative=nose down
        - roll_vehicle: vehicle roll, negative=left wing down, positive=right wing down
        - camera_offset: camera down angle from vehicle (positive = down)

        RealityCapture conventions (standard aerial photogrammetry):
        - Yaw: 0=North, 90=East, 180=South, 270=West
        - Pitch: 0=nadir (straight down), 90=horizontal, -90=straight up
        - Roll: 0=level, positive=right wing down
        """
        # Yaw: Convert magnetic heading to true north, then use directly as RC yaw
        if heading_mag is not None:
            true_heading = heading_mag + decl_deg
            rc_yaw = self._wrap360(true_heading)
        else:
            rc_yaw = None

        # Pitch: Convert vehicle pitch and camera offset to RC pitch
        # RC pitch: 0=nadir, 90=horizontal
        # Camera pitch from horizontal = vehicle_pitch - camera_offset
        # RC pitch = 90 + camera_pitch_from_horizontal
        if pitch_vehicle is not None:
            camera_pitch_from_horiz = pitch_vehicle - camera_offset
            rc_pitch = 90.0 + camera_pitch_from_horiz
        else:
            rc_pitch = None

        # Roll: Pass through directly (same convention)
        rc_roll = roll_vehicle

        return rc_yaw, rc_pitch, rc_roll

    def __read_csv_data(self, filename):
        """Read and parse CSV data from a file, including sensor and position data."""
        data_rows = []
        try:
            with open(filename, "r") as csvfile:
                reader = csv.reader(csvfile, delimiter=',')
                header = next(reader)
                idx_map = {name: index for index, name in enumerate(header)}
                for row in reader:
                    data_rows.append({
                        "TIME": datetime.strptime(row[idx_map['Timestamp']], self.TIMESTAMP_FORMAT),
                        "LAT": float(row[idx_map['kalman_lat']]) if row[idx_map['kalman_lat']] else None,
                        "LONG": float(row[idx_map['kalman_long']]) if row[idx_map['kalman_long']] else None,
                        "DEPTH": -abs(float(row[idx_map['kalman_depth']])) if row[idx_map['kalman_depth']] else None,
                        "HEADING_MAG": float(row[idx_map['kalman_yaw_deg']]) if row[
                            idx_map['kalman_yaw_deg']] else None,
                        "PITCH": float(row[idx_map['kalman_pitch_deg']]) if row[idx_map['kalman_pitch_deg']] else None,
                        "ROLL": float(row[idx_map['kalman_roll_deg']]) if row[idx_map['kalman_roll_deg']] else None
                    })
            self.stats['csv_rows'] = len(data_rows)
        except Exception as e:
            self.logger.error(f"Error processing CSV file: {e}")
            raise e
        return data_rows

    def __convert_to_utm(self, lat, lon):
        """Convert latitude and longitude to UTM coordinates in the specified zone."""
        if lat is None or lon is None:
            return None, None
        try:
            easting, northing, zone_number, zone_letter = utm.from_latlon(lat, lon)
            if self.utm_zone is None:
                self.utm_zone = f"{zone_number}{zone_letter}"
            return easting, northing
        except Exception as e:
            self.logger.error(f"Failed to convert to UTM coordinates: {e}")
            return None, None

    def __is_image_file(self, filename, image_folder):
        try:
            with Image.open(os.path.join(image_folder, filename)) as im:
                im.verify()
            return True
        except Exception:
            return False

    def __parse_timestamp_from_filename(self, filename, data_type):
        """Extract and parse the timestamp from an image filename."""
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

    def __read_image_filenames(self, image_folder, data_type):
        """Read all JPEG image filenames from a folder and subdirectories, extracting their timestamps."""
        image_data = []
        jpeg_extensions = {'.jpg', '.jpeg'}

        jpeg_files = []
        for root, dirs, files in os.walk(image_folder):
            for filename in files:
                if os.path.splitext(filename.lower())[1] in jpeg_extensions:
                    rel_path = os.path.relpath(os.path.join(root, filename), image_folder)
                    jpeg_files.append(rel_path)

        total_files = len(jpeg_files)
        unreadable_files = 0
        ts_parse_failures = 0

        bar = self._initialize_loading_bar(total_files, "Reading Image Data")
        for rel_path in jpeg_files:
            full_path = os.path.join(image_folder, rel_path)
            filename = os.path.basename(rel_path)

            if self.__is_image_file(filename, os.path.dirname(full_path)):
                timestamp = self.__parse_timestamp_from_filename(filename, data_type)
                if timestamp:
                    image_data.append({"FILENAME": filename, "TIMESTAMP": timestamp})
                else:
                    ts_parse_failures += 1
            else:
                unreadable_files += 1
            self._update_loading_bar(bar, 1)

        self.stats['files_listed'] = total_files
        self.stats['files_unreadable'] = unreadable_files
        self.stats['timestamp_parse_failures'] = ts_parse_failures
        self.stats['images_with_valid_ts'] = len(image_data)

        return image_data

    def __estimate_location(self, image_data, data_rows, input_type) -> int:
        """Estimate location and orientation for each image. Accept only matches within 2 seconds."""
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

        bar = self._initialize_loading_bar(len(image_data), "Estimating Location")
        for image in image_data:
            filename = image["FILENAME"]
            image["ACCEPTED"] = False

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

            self._update_loading_bar(bar, 1)

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

        print("Matching summary:")
        print(f"  Examined images: {self.stats['examined_images']}")
        print(f"  Accepted ≤2s:    {self.stats['accepted_images']} ({self.stats['accept_rate_pct']:.1f}%)")
        print(f"  Rejected >2s:    {self.stats['rejected_time']}")
        print(f"  Rejected no CSV: {self.stats['rejected_no_csv']}")
        print("  Time-delta buckets (all pairs, pre-threshold):")
        print(f"    Exact: {self.stats['bucket_exact']}")
        print(f"    1–4s:  {self.stats['bucket_1_4']}")
        print(f"    5–15s: {self.stats['bucket_5_15']}")
        print(f"    >15s:  {self.stats['bucket_gt15']}")
        print("  Accepted field completeness:")
        print(f"    Missing UTM:         {self.stats['accepted_missing_utm']}")
        print(f"    Missing orientation: {self.stats['accepted_missing_orientation']}")

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
        filename_lower = filename.lower()

        # Check specific camera types first (most specific to least specific)
        if filename_lower.startswith('camupper'):
            return (1.0, 0.0, 0.0)  # 1m forward, same depth
        elif filename_lower.startswith('cammid'):
            return (1.0, 0.0, 1.0)  # 1m forward, 1m down
        elif filename_lower.startswith('camlower'):
            return (1.0, 0.0, 1.0)  # 1m forward, 1m down
        elif '_herc_' in filename_lower:
            return (0.5, 0.0, 0.5)  # 0.5m forward, 0.5m down
        else:
            self.logger.warning(f"Unknown camera type for {filename}, assuming no offset")
            return (0.0, 0.0, 0.0)

    def _get_camera_pitch_offset(self, filename: str) -> float:
        """
        Return camera pitch offset (degrees down from vehicle forward axis).
        Positive values = camera pointing down relative to vehicle.
        """
        filename_lower = filename.lower()

        # Check specific camera types first (most specific to least specific)
        if filename_lower.startswith('camupper'):
            return 70.0  # pointing down 70°
        elif filename_lower.startswith('cammid'):
            return 20.0  # pointing down 20°
        elif filename_lower.startswith('camlower'):
            return 10.0  # pointing down 10°
        elif '_herc_' in filename_lower:
            return 30.0  # Zeuss pointing down 30°
        else:
            self.logger.warning(f"Unknown camera type for {filename}, assuming 0° pitch offset")
            return 0.0

    def _get_camera_accuracy(self, filename: str) -> tuple[float, float, float]:
        """
        Return yaw, pitch, roll accuracy (degrees) for a camera based on its name.
        Default values: upper=10, mid=10, lower=5, zeuss=30
        """
        filename_lower = filename.lower()

        # Check specific camera types first (most specific to least specific)
        if filename_lower.startswith('camupper'):
            return 10.0, 10.0, 10.0
        elif filename_lower.startswith('cammid'):
            return 10.0, 10.0, 10.0
        elif filename_lower.startswith('camlower'):
            return 5.0, 5.0, 5.0
        elif '_herc_' in filename_lower:
            return 30.0, 30.0, 30.0
        else:
            self.logger.warning(f"Unknown camera type for {filename}, using default accuracy 10°")
            return 10.0, 10.0, 10.0

    def __generate_flight_log(self, image_data, image_folder):
        """Generate a flight log file with position and orientation accuracy."""
        zone_suffix = self.utm_zone if self.utm_zone else "UNKNOWN"
        flight_log_filename = os.path.join(image_folder, f"flight_log_{zone_suffix}_UTM.txt")

        if os.path.exists(flight_log_filename):
            self.logger.warning(f"Flight log file already exists: {flight_log_filename}, overriding.")
            os.remove(flight_log_filename)

        accepted_images = [img for img in image_data if img.get("ACCEPTED", False)]
        decl_deg = self.params['magnetic_declination_deg'].get_value()

        # Fixed accuracy values
        pos_x_acc = 10.0
        pos_y_acc = 10.0
        alt_acc = 1.0
        yaw_acc = 3.0
        roll_acc = 3.0

        with open(flight_log_filename, "w") as f:
            f.write(
                "Name;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy\n"
            )

            for image in accepted_images:
                heading_mag = image.get("HEADING_MAG")
                pitch_vehicle = image.get("PITCH_VEHICLE")
                roll_vehicle = image.get("ROLL_VEHICLE")

                camera_pitch_offset = self._get_camera_pitch_offset(image["FILENAME"])
                rc_yaw, rc_pitch, rc_roll = self._convert_to_rc_orientation(
                    heading_mag, pitch_vehicle, roll_vehicle, camera_pitch_offset, decl_deg
                )

                pitch_acc = self._get_camera_pitch_accuracy(image["FILENAME"])

                def fmt(val):
                    return f"{val:.6f}" if val is not None else ""

                line = ";".join([
                    image["FILENAME"],
                    fmt(image.get("UTM_X")),
                    fmt(image.get("UTM_Y")),
                    fmt(image.get("ALTITUDE_EST")),
                    fmt(pos_x_acc),
                    fmt(pos_y_acc),
                    fmt(alt_acc),
                    fmt(rc_yaw),
                    fmt(rc_pitch),
                    fmt(rc_roll),
                    fmt(yaw_acc),
                    fmt(pitch_acc),
                    fmt(roll_acc)
                ])
                f.write(line + "\n")

        self.stats['written_to_flight_log'] = len(accepted_images)
        print(f"Flight log: {flight_log_filename}")
        print(f"  Lines written: {self.stats['written_to_flight_log']}")

        return flight_log_filename

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {"Success": False}

        flight_log = self.params['geo_input_flight_log'].get_value()
        if 'geo_input_image_dir' in self.params:
            input_dir = self.params['geo_input_image_dir'].get_value()
        else:
            input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

        input_type = self.params['geo_input_type'].get_value()
        output_data = {}

        try:
            data_rows = self.__read_csv_data(flight_log)
            image_data = self.__read_image_filenames(input_dir, input_type)
            matches_made = self.__estimate_location(image_data, data_rows, input_type)
            output_path = self.__generate_flight_log(image_data, input_dir)

            output_data['Success'] = True
            output_data['CSV Rows'] = int(self.stats.get('csv_rows', 0))
            output_data['Files Listed'] = int(self.stats.get('files_listed', 0))
            output_data['Files Unreadable'] = int(self.stats.get('files_unreadable', 0))
            output_data['Timestamp Parse Failures'] = int(self.stats.get('timestamp_parse_failures', 0))
            output_data['Images With Valid Timestamps'] = int(self.stats.get('images_with_valid_ts', 0))
            output_data['Images Examined'] = int(self.stats.get('examined_images', 0))
            output_data['Matched ≤2s'] = matches_made
            output_data['Rejected >2s'] = int(self.stats.get('rejected_time', 0))
            output_data['Rejected No CSV'] = int(self.stats.get('rejected_no_csv', 0))
            output_data['Written To Flight Log'] = int(self.stats.get('written_to_flight_log', 0))
            output_data['Acceptance Rate %'] = float(f"{self.stats.get('accept_rate_pct', 0.0):.2f}")
            output_data['Delta Buckets'] = {
                "Exact": int(self.stats.get('bucket_exact', 0)),
                "1–4s": int(self.stats.get('bucket_1_4', 0)),
                "5–15s": int(self.stats.get('bucket_5_15', 0)),
                ">15s": int(self.stats.get('bucket_gt15', 0))
            }
            output_data['Accepted Field Gaps'] = {
                "Missing UTM": int(self.stats.get('accepted_missing_utm', 0)),
                "Missing Orientation": int(self.stats.get('accepted_missing_orientation', 0))
            }
            output_data['Output Flight Log'] = output_path

        except Exception as e:
            self.logger.error(f"Error processing data: {e}")
            return {"Success": False}

        self.logger.info(f"CSV Rows: {output_data['CSV Rows']}")
        self.logger.info(f"Files Listed: {output_data['Files Listed']}")
        self.logger.info(f"Files Unreadable: {output_data['Files Unreadable']}")
        self.logger.info(f"Timestamp Parse Failures: {output_data['Timestamp Parse Failures']}")
        self.logger.info(f"Images With Valid Timestamps: {output_data['Images With Valid Timestamps']}")
        self.logger.info(f"Images Examined: {output_data['Images Examined']}")
        self.logger.info(f"Matched ≤2s: {output_data['Matched ≤2s']}")
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

        if 'geo_input_image_dir' in self.params:
            input_dir = self.params['geo_input_image_dir'].get_value()
        else:
            input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

        if 'geo_input_flight_log' not in self.params:
            return False, 'Flight log parameter not found'

        flight_log = self.params['geo_input_flight_log'].get_value()

        if not os.path.isdir(input_dir):
            return False, 'Input directory does not exist'
        if not os.path.isfile(flight_log):
            return False, 'Flight log file does not exist'
        if os.path.splitext(flight_log)[1].lower() != '.csv':
            return False, 'Flight log is not a CSV file'

        if 'geo_input_type' not in self.params:
            return False, 'Data type parameter not found'

        dtype = self.params['geo_input_type'].get_value().lower()
        if dtype not in ["zeuss", "wca", "wca2025", "all"]:
            return False, 'Invalid data type specified'

        if dtype == "wca":
            self.params['geo_input_type'].set_value("WCA")
        elif dtype == "zeuss":
            self.params['geo_input_type'].set_value("Zeuss")
        elif dtype == "wca2025":
            self.params['geo_input_type'].set_value("WCA2025")
        elif dtype == "all":
            self.params['geo_input_type'].set_value("All")

        return True, None