from __future__ import annotations
import os

from ..geo_core import (
    read_csv_data,
    read_image_filenames,
    estimate_locations,
    generate_flight_log,
    is_valid_image,
    parse_timestamp_from_filename,
)
from ..camera_config import (
    get_camera_pitch_offset,
    get_camera_pitch_accuracy,
    get_camera_position_offsets,
    get_camera_accuracy,
)
from ..coordinate_utils import (
    wrap180,
    wrap360,
    apply_camera_position_offset,
    convert_to_rc_orientation,
    convert_to_utm,
)
from ..naming import build_flight_log_name, parse_expedition_id, parse_dive_id
from module_base.rc_module import RCModule
from module_base.parameter import Parameter


class GeoreferenceImages(RCModule):

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

    def __read_image_filenames_with_progress(self, image_folder, data_type):
        """Read JPEG image filenames with progress bar, validating each image."""
        jpeg_extensions = {'.jpg', '.jpeg'}
        image_data = []

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

            if is_valid_image(full_path):
                timestamp = parse_timestamp_from_filename(filename, data_type)
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
        decl_deg = self.params['magnetic_declination_deg'].get_value()
        output_data = {}

        try:
            # Use shared core functions
            data_rows = read_csv_data(flight_log)
            self.stats['csv_rows'] = len(data_rows)

            image_data = self.__read_image_filenames_with_progress(input_dir, input_type)

            # Use binary search estimation from geo_core
            matched_images, match_stats = estimate_locations(
                image_data, data_rows,
                match_threshold_sec=2.0,
                magnetic_declination_deg=decl_deg,
            )
            matches_made = match_stats['matches_made']

            # Update stats from match results
            self.stats['examined_images'] = len(image_data)
            self.stats['accepted_images'] = matches_made
            self.stats['rejected_time'] = match_stats['rejected_time']
            self.stats['rejected_no_csv'] = 0
            self.stats['bucket_exact'] = match_stats['exact_matches']
            self.stats['bucket_1_4'] = match_stats['matches_1_4']
            self.stats['bucket_5_15'] = match_stats['matches_5_15']
            self.stats['bucket_gt15'] = match_stats['matches_gt15']
            self.stats['accepted_missing_utm'] = match_stats['accepted_missing_utm']
            self.stats['accepted_missing_orientation'] = match_stats['accepted_missing_orientation']
            self.stats['accept_rate_pct'] = (100.0 * matches_made / len(image_data)) if image_data else 0.0

            # Set UTM zone from match results
            utm_zone_number = match_stats.get('utm_zone_number')
            utm_zone_letter = match_stats.get('utm_zone_letter')
            if utm_zone_number is not None:
                self.utm_zone = f"{utm_zone_number}{utm_zone_letter}"

            # Print matching summary
            print("Matching summary:")
            print(f"  Examined images: {self.stats['examined_images']}")
            print(f"  Accepted ≤2s:    {self.stats['accepted_images']} ({self.stats['accept_rate_pct']:.1f}%)")
            print(f"  Rejected >2s:    {self.stats['rejected_time']}")
            print("  Time-delta buckets (all pairs, pre-threshold):")
            print(f"    Exact: {self.stats['bucket_exact']}")
            print(f"    1-4s:  {self.stats['bucket_1_4']}")
            print(f"    5-15s: {self.stats['bucket_5_15']}")
            print(f"    >15s:  {self.stats['bucket_gt15']}")
            print("  Accepted field completeness:")
            print(f"    Missing UTM:         {self.stats['accepted_missing_utm']}")
            print(f"    Missing orientation: {self.stats['accepted_missing_orientation']}")

            # Generate flight log using shared core
            zone_suffix = self.utm_zone if self.utm_zone else "UNKNOWN"
            flight_log_filename = os.path.join(input_dir, f"flight_log_{zone_suffix}_UTM.txt")
            output_path = generate_flight_log(
                matched_images, flight_log_filename,
                magnetic_declination_deg=decl_deg,
            )
            self.stats['written_to_flight_log'] = len(matched_images)
            print(f"Flight log: {output_path}")
            print(f"  Lines written: {self.stats['written_to_flight_log']}")

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
