import os
import csv
from datetime import datetime
import time
from ..file_metadata_parser import parse_timestamp

from module_base.rc_module import RCModule
from module_base.parameter import Parameter

class GeoreferenceImages(RCModule):
	IMAGE_FILE_EXTENSIONS = (".png", ".heif", ".jpg", ".jpeg") # Add more extensions if needed

	def __init__(self, logger):
		super().__init__("Georeference Images", logger)

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

		return {**super().get_parameters(), **additional_params}
	
	def __read_tsv_file(self, tsv_path) -> list[dict]:
		data_rows = []

		try:
			with open(tsv_path, "r") as tsvfile:
				reader = csv.reader(tsvfile, delimiter='\t')

				for row in reader:
					try:
						data_rows.append({
							"TIME": datetime.fromisoformat(row[0]),
							"LAT": row[1],
							"LONG": row[2],
							"DEPTH": row[3]
						})
					except Exception as e:
						raise Exception(f"Error reading data from TSV file: {e}")
		except Exception as e:
			raise Exception(f"Error opening TSV file: {e}")
		
		return data_rows
	
	def __get_image_filenames(self, image_dir) -> list[str]:
		image_files = []

		try:
			image_files = [filename for filename in os.listdir(image_dir) if filename.endswith(self.IMAGE_FILE_EXTENSIONS)]
		except Exception as e:
			raise Exception(f"Error reading image files: {e}")
		
		return image_files
	
	def __extract_image_data(self, image_file) -> dict:
		image_data = {}

		try:
			image_data = {
				"FILENAME": image_file,
				"TIMESTAMP": parse_timestamp(image_file)
			}
		except Exception as e:
			raise Exception(f"Error processing filenames: {e}")
		
		return image_data
	
	def __match_timestamps(self, image_data, data_rows) -> list[dict]:
		matches = []

		try:
			for image in image_data:
				closest_match = min(data_rows, key=lambda row: abs(row["TIME"] - image["TIMESTAMP"]))
				image["LAT_EST"] = closest_match["LAT"]
				image["LONG_EST"] = closest_match["LONG"]
				image["ALTITUDE_EST"] = closest_match["DEPTH"]
				matches.append(image)
		except Exception as e:
			raise Exception(f"Error matching timestamps: {e}")
		
		return matches
	
	def __generate_flight_log(self, image_data, output_path):
		unique_locations = set()

		try:
			with open(output_path, "w") as f:
				for image in image_data:
					line = "{};{};{};{}".format(image["FILENAME"], image["LAT_EST"], image["LONG_EST"], image["ALTITUDE_EST"])
					if line not in unique_locations:
						f.write(line + "\n")
						unique_locations.add(line)
		except Exception as e:
			raise Exception(f"Error writing to flight log file: {e}")
		
	def __georeference_images(self, input_dir, output_path, flight_log) -> dict[str, any]:
		bar = self._initialize_loading_bar(3, "Georeferencing Images")

		data_rows = self.__read_tsv_file(flight_log)
		
		# This usually runs faster than the loading bar can initialize, so sleep to make sure it updates
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

		# Process image files
		image_files = self.__get_image_filenames(input_dir)

		# Extract timestamp from image filenames and match with data
		raw_image_data = []
		for image_file in image_files:
			raw_image_data.append(self.__extract_image_data(image_file))
			
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

		# Compare timestamps and estimate locations
		matched_image_data = self.__match_timestamps(raw_image_data, data_rows)

		# Generate flight log file
		self.__generate_flight_log(matched_image_data, output_path)
		
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

		output_data = {}
		output_data['Success'] = True
		output_data['Input Log Rows Extracted'] = len(data_rows)
		output_data['Input Image Count'] = len(image_files)
		output_data['Matched Image Count'] = len(matched_image_data)
		output_data['Output Flight Log'] = output_path

		return output_data

	def run(self):
		# Validate parameters
		success, message = self.validate_parameters()
		if not success:
			self.logger.error(message)
			return {"Success": False}
		
		# Get parameters
		flight_log = self.params['geo_input_flight_log'].get_value()

		# If not continuing from Extract Images, set output directory to image directory
		# Otherwise, set output directory to the global output directory
		output_path = None
		if 'geo_input_image_dir' in self.params:
			output_path = os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
		else:
			output_path = os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")

		# Input directory is not specified if continuing from Extract Images, use it's output in that case
		input_dir = None
		if 'geo_input_image_dir' in self.params:
			input_dir = self.params['geo_input_image_dir'].get_value()
		else:
			input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

		overall_output_data = {}
		try:
			overall_output_data = self.__georeference_images(input_dir, output_path, flight_log)
		except Exception as e:
			self.logger.error(f"Error georeferencing images: {e}")
			return {"Success": False}

		if overall_output_data['Success']:
			num_data_rows = overall_output_data['Input Log Rows Extracted']
			num_image_files = overall_output_data['Input Image Count']
			num_matched_image_data = overall_output_data['Matched Image Count']

			self.logger.info(f"Input Log Rows Extracted: {num_data_rows}")
			self.logger.info(f"Images Examined: {num_image_files}")
			self.logger.info(f"Images Matched: {num_matched_image_data}")
		
		return overall_output_data

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
		
		if os.path.splitext(flight_log)[1].lower() != '.tsv':
			return False, 'Flight log is not an TSV file'

		return True, None
	