import os
import csv
from datetime import datetime
import time

from module_base.rc_module import RCModule
from module_base.parameter import Parameter

class GeoreferenceImages(RCModule):
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
			description='Path to the flight log file',
			prompt_user=True
		)

		return {**super().get_parameters(), **additional_params}
	
	def run(self):
		# Validate parameters
		success, message = self.validate_parameters()
		if not success:
			self.logger.error(message)
			return
		
		bar = self._initialize_loading_bar(3, "Georeferencing Images")
		
		# Get parameters
		flight_log = self.params['geo_input_flight_log'].get_value()
		output_path = os.path.join(self.params['output_dir'].get_value(), "flight_log.csv")

		# Input directory is not specified if continuing from Extract Images, use it's output in that case
		input_dir = None
		if 'geo_input_image_dir' in self.params:
			input_dir = self.params['geo_input_image_dir'].get_value()
		else:
			input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")
		
		# Read TSV file and extract data
		data_rows = []
		try:
			with open(flight_log, "r") as tsvfile:
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
		
		# This usually runs faster than the loading bar can initialize, so sleep to make sure it updates
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

		# Process image files
		image_files = [filename for filename in os.listdir(input_dir) if filename.endswith((".png", ".heif", ".jpg", ".jpeg"))] # Add more extensions if needed

		# Extract timestamp from image filenames and match with data
		image_data = []
		for image_file in image_files:
			try:
				# Assuming filenames are in the format 'P001C0019_20231023212955.heif'
				timestamp_str = image_file.split('_')[1].split('.')[0]
				image_timestamp = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")

				image_data.append({
					"FILENAME": image_file,
					"TIMESTAMP": image_timestamp
				})
			except Exception as e:
				raise Exception(f"Error processing filenames: {e}")
			
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

		# Compare timestamps and estimate locations
		matches = 0
		reference_depth = 0  # Mean sea level
		for image in image_data:
			closest_match = min(data_rows, key=lambda row: abs(row["TIME"] - image["TIMESTAMP"]))
			image["LAT_EST"] = closest_match["LAT"]
			image["LONG_EST"] = closest_match["LONG"]
			image["ALTITUDE_EST"] = reference_depth - float(closest_match["DEPTH"])
			matches += 1

		# Generate flight log file
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
		
		time.sleep(0.1)
		self._update_loading_bar(bar, 1)

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
	