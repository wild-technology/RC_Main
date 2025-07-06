from __future__ import annotations
import os
import csv
from datetime import datetime, timedelta
from PIL import Image
import sys
import pyproj  # Import the projection library
from pyproj import Proj, transform
from ..file_metadata_parser import parse_timestamp_str, parse_timestamp
import utm

from module_base.rc_module import RCModule
from module_base.parameter import Parameter


class GeoreferenceImages(RCModule):
    TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # Correct format for timestamps in cSV files
    WCA_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"  # WCA format for timestamps in filenames
    ZEUSS_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"  # Zeuss format for timestamps in filenames
    WCA2025_FILENAME_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

    def __init__(self, logger):
        super().__init__("Georeference Images", logger)
        self.utm_zone = None

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
            description='Type of data to process (Zeuss, WCA, or WCA2025)',
            prompt_user=True
        )

        return {**super().get_parameters(), **additional_params}

    def __read_csv_data(self, filename):
        """Read and parse cSV data from a file, including sensor and position data."""
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
                        "HEADING": float(row[idx_map['kalman_yaw_deg']]) if row[idx_map['kalman_yaw_deg']] else None,
                        "PITCH": float(row[idx_map['kalman_pitch_deg']]) if row[idx_map['kalman_pitch_deg']] else None,
                        "ROLL": float(row[idx_map['kalman_roll_deg']]) if row[idx_map['kalman_roll_deg']] else None
                    })
        except Exception as e:
            self.logger.error(f"Error processing CSV file: {e}")
            raise e
        return data_rows

    def __convert_to_utm(self, lat, lon):
        """Convert latitude and longitude to UTM coordinates in the specified zone."""
        if not lat or not lon:
            return None, None
        try:
            utm_values = utm.from_latlon(lat, lon)
            easting = utm_values[0]
            northing = utm_values[1]

            if self.utm_zone is None:
                zone_number = utm_values[2]
                zone_letter = utm_values[3]
                self.utm_zone = f"{zone_number}{zone_letter}"

            return easting, northing
        except Exception as e:
            self.logger.error(f"Failed to convert to UTM coordinates: {e}")
            return None, None

    def __is_image_file(self, filename, image_folder):
        try:
            Image.open(os.path.join(image_folder, filename)).verify()
            return True
        except IOError:
            return False

    def __parse_timestamp_from_filename(self, filename, data_type):
        """Extract and parse the timestamp from an image filename."""
        if data_type == "WCA2025":
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
        """Read all image filenames from a folder and extract their timestamps."""
        image_data = []
        image_files = os.listdir(image_folder)
        total_files = len(image_files)
        bar = self._initialize_loading_bar(total_files, "Reading Image Data")
        for filename in image_files:
            if self.__is_image_file(filename, image_folder):
                timestamp = self.__parse_timestamp_from_filename(filename, data_type)
                if timestamp:
                    image_data.append({
                        "FILENAME": filename,
                        "TIMESTAMP": timestamp
                    })
            self._update_loading_bar(bar, 1)
        return image_data

    def __estimate_location(self, image_data, data_rows, input_type):
        """Estimate geographical location and sensor data for each image based on its timestamp."""
        matches_made = 0
        exact_matches = 0
        matches_1_4 = 0
        matches_5_15 = 0
        matches_gt15 = 0
        no_matches = 0
        bar = self._initialize_loading_bar(len(image_data), "Estimating Location")
        for image in image_data:
            filename = image["FILENAME"]
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
                lat, lon = closest_match.get("LAT"), closest_match.get("LONG")
                utm_x, utm_y = self.__convert_to_utm(lat, lon)

                # MODIFIED: Pitch calculation logic updated for WCA2025
                final_pitch = None
                vehicle_pitch = closest_match.get("PITCH", 0)

                if input_type == "WCA2025":
                    camera_angle = 0
                    if filename.startswith("camlower"):
                        camera_angle = 0
                    elif filename.startswith("cammid"):
                        camera_angle = -10
                    elif filename.startswith("camupper"):
                        camera_angle = -45

                    # Apply vehicle pitch, camera angle, and the constant 90-degree offset
                    final_pitch = vehicle_pitch + camera_angle + 90
                else:  # Fallback to original logic for Zeuss and WCA
                    base_pitch = 30
                    if filename.startswith("P"):
                        base_pitch = 90
                    final_pitch = vehicle_pitch + base_pitch

                image.update({
                    "LAT": lat, "LONG": lon, "UTM_X": utm_x, "UTM_Y": utm_y,
                    "ALTITUDE_EST": closest_match.get("DEPTH"), "HEADING": closest_match.get("HEADING"),
                    "PITCH": final_pitch, "ROLL": closest_match.get("ROLL")
                })
                matches_made += 1
            else:
                no_matches += 1

                # MODIFIED: Default pitch calculation updated for WCA2025
                pitch_val = None
                if input_type == "WCA2025":
                    if filename.startswith("camlower"):
                        pitch_val = 0 + 90  # 90
                    elif filename.startswith("cammid"):
                        pitch_val = -10 + 90  # 80
                    elif filename.startswith("camupper"):
                        pitch_val = -45 + 90  # 45
                else:  # Fallback to original logic for Zeuss and WCA
                    if filename.startswith("P"):
                        pitch_val = 40

                image.update({
                    "LAT": None, "LONG": None, "UTM_X": None, "UTM_Y": None,
                    "ALTITUDE_EST": None, "HEADING": None,
                    "PITCH": pitch_val, "ROLL": None
                })
                print(f"Error: No matching CSV data within 2 seconds for image {image['FILENAME']}.")
            self._update_loading_bar(bar, 1)
        print("Matching results:")
        print(f"Exact matches: {exact_matches}")
        print(f"Matches 1-4 sec: {matches_1_4}")
        print(f"Matches 5-15 sec: {matches_5_15}")
        print(f"Matches >15 sec: {matches_gt15}")
        print(f"No matches: {no_matches}")

    def __generate_flight_log(self, image_data, image_folder):
        """Generate a flight log file from the image data."""
        flight_log_filename = os.path.join(image_folder, "flight_log.txt")
        if os.path.exists(flight_log_filename):
            self.logger.warning(f"Flight log file already exists: {flight_log_filename}, overriding.")
            os.remove(flight_log_filename)
        with open(flight_log_filename, "w") as f:
            coordinate_system = "UTM"
            if coordinate_system == "UTM":
                f.write("Name;X (East);Y (North);Alt;Yaw;Pitch;Roll\n")
                for image in image_data:
                    line = ";".join(str(x) for x in [
                        image["FILENAME"], image.get("UTM_X", ""), image.get("UTM_Y", ""),
                        image.get("ALTITUDE_EST", ""), image.get("HEADING", ""), image.get("PITCH", ""),
                        image.get("ROLL", "")
                    ])
                    f.write(line + "\n")
            else:
                f.write("Name;Lat;Long;Alt;Yaw;Pitch;Roll\n")
                for image in image_data:
                    line = ";".join(str(x) for x in [
                        image["FILENAME"], image.get("LAT", ""), image.get("LONG", ""),
                        image.get("ALTITUDE_EST", ""), image.get("HEADING", ""), image.get("PITCH", ""),
                        image.get("ROLL", "")
                    ])
                    f.write(line + "\n")
        print(f"Flight log generated successfully. Location: {flight_log_filename}")

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {"Success": False}
        flight_log = self.params['geo_input_flight_log'].get_value()
        output_path = None
        if 'geo_input_image_dir' in self.params:
            output_path = os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
        else:
            output_path = os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")
        input_dir = None
        if 'geo_input_image_dir' in self.params:
            input_dir = self.params['geo_input_image_dir'].get_value()
        else:
            input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")
        input_type = self.params['geo_input_type'].get_value()
        output_data = {}
        try:
            data_rows = self.__read_csv_data(flight_log)
            image_data = self.__read_image_filenames(input_dir, input_type)
            # MODIFIED: Pass input_type to the estimation function
            matches_made = self.__estimate_location(image_data, data_rows, input_type)
            self.__generate_flight_log(image_data, input_dir)
            output_data['Input Log Rows Extracted'] = len(data_rows)
            output_data['Input Image Count'] = len(image_data)
            output_data['Matched Image Count'] = matches_made
            output_data['Output Flight Log'] = output_path
        except Exception as e:
            self.logger.error(f"Error processing data: {e}")
            return {"Success": False}
        self.logger.info(f"Input Log Rows Extracted: {output_data['Input Log Rows Extracted']}")
        self.logger.info(f"Images Examined: {output_data['Input Image Count']}")
        self.logger.info(f"Images Matched: {output_data['Matched Image Count']}")

        if self.utm_zone:
            self.logger.info(f"UTM Zone Detected: {self.utm_zone}")
        else:
            self.logger.warning("UTM Zone could not be determined (no valid GPS data found).")

        return output_data

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message
        input_dir = None
        if 'geo_input_image_dir' in self.params:
            input_dir = self.params['geo_input_image_dir'].get_value()
        else:
            input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")
        if not 'geo_input_flight_log' in self.params:
            return False, 'Flight log parameter not found'
        flight_log = self.params['geo_input_flight_log'].get_value()
        if not os.path.isdir(input_dir):
            return False, 'Input directory does not exist'
        if not os.path.isfile(flight_log):
            return False, 'Flight log file does not exist'
        if os.path.splitext(flight_log)[1].lower() != '.csv':
            return False, 'Flight log is not an csv file'
        if not 'geo_input_type' in self.params:
            return False, 'Data type parameter not found'
        if self.params['geo_input_type'].get_value().lower() not in ["zeuss", "wca", "wca2025"]:
            return False, 'Invalid data type specified'
        if self.params['geo_input_type'].get_value().lower() == "wca":
            self.params['geo_input_type'].set_value("WCA")
        if self.params['geo_input_type'].get_value().lower() == "zeuss":
            self.params['geo_input_type'].set_value("Zeuss")
        if self.params['geo_input_type'].get_value().lower() == "wca2025":
            self.params['geo_input_type'].set_value("WCA2025")
        return True, None