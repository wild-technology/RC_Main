from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import os
import shutil
from datetime import datetime
from ..file_metadata_parser import parse_unix_timestamp, parse_frame_number

class BatchDirectory(RCModule):
	def __init__(self, logger):
		super().__init__("Batch Directory", logger)

	def get_parameters(self) -> dict[str, Parameter]:
		additional_params = {}

		additional_params['batch_batch_size'] = Parameter(
			name='Batch Size',
			cli_short='b_s',
			cli_long='b_batch_size',
			type=int,
			default_value=100,
			description='The number of images per batch',
			prompt_user=True
		)

		additional_params['batch_overlap_percent'] = Parameter(
			name='Overlap Percent',
			cli_short='b_p',
			cli_long='b_overlap_percent',
			type=float,
			default_value=20,
			description='The percent of overlap between batches',
			prompt_user=True
		)

		additional_params['batch_input_image_dir'] = Parameter(
			name='Input Image Folder',
			cli_short='b_i',
			cli_long='b_input',
			type=str,
			default_value=None,
			description='Directory containing the images to batch',
			prompt_user=True,
			disable_when_module_active='Extract Images'
		)

		additional_params['batch_flight_log_path'] = Parameter(
			name='Flight Log Path',
			cli_short='b_f',
			cli_long='b_flight_log_path',
			type=str,
			default_value=None,
			description='Path to the flight log file (optional)',
			prompt_user=True,
			disable_when_module_active='Georeference Images'
		)

		return {**super().get_parameters(), **additional_params}

	def get_image_files(self, input_dir):
		"""
		Returns a list of image files from the input directory.
		"""
		files = os.listdir(input_dir)

		for file in files:
			extension = os.path.splitext(file)[1]

			if extension not in [".png", ".jpg", ".jpeg"]:
				self.logger.warning(f"{file} is not an accepted file type")
				files.remove(file)

		return files

	def sort_files(self, files):
		"""
		Sorts files based on unix timestamp, segment start time, and frame number.
		"""
		files.sort(key=lambda x: (parse_unix_timestamp(x), parse_frame_number(x)))
		return files

	def calculate_batches(self, files, batch_size):
		"""
		Calculates the number of batches needed to batch the files.
		"""
		num_batches = len(files) // batch_size

		if len(files) % batch_size != 0:
			num_batches += 1

		return num_batches

	def copy_files(self, input_dir, batch_folder_dir, files):
		"""
		Copies files from the input directory to the batch folder.
		"""
		for file in files:
			file_path = os.path.join(input_dir, file)
			output_path = os.path.join(batch_folder_dir, file)

			shutil.copy(file_path, output_path)

	def calculate_indices(self, index, batch_size, overlap_size, files):
		"""
		Calculates the start and end indices for a batch.
		"""
		start_index = (index * batch_size) - (overlap_size if index > 0 else 0)
		end_index = min((index + 1) * batch_size, len(files) - 1)

		return start_index, end_index

	def get_file_metadata(self, files, start_index, end_index):
		"""
		Returns the file metadata for a batch.
		"""
		start_timestamp = parse_unix_timestamp(files[start_index]).strftime("%Y%m%dT%H%M%SZ")
		end_timestamp = parse_unix_timestamp(files[end_index]).strftime("%Y%m%dT%H%M%SZ")

		start_file_metadata = files[start_index][len(start_timestamp) - 1:]

		return start_timestamp, end_timestamp, start_file_metadata

	def get_batch_files(self, files, start_index, end_index):
		"""
		Returns the files for a batch.
		"""
		return files[start_index:end_index]

	def create_batch_folder(self, input_dir, batch_folder_dir, files):
		"""
		Creates a batch folder and copies files from the input directory to the batch folder.
		If the batch folder already exists, asks the user for confirmation to overwrite.
		"""
		if os.path.isdir(batch_folder_dir):
			self.logger.warning('Batch folder "%s" already exists. Overwrite? (y/n)', batch_folder_dir)
			overwrite = input()

			if overwrite.lower() != 'y':
				self.logger.warning('Batch folder not created')
				return
			else:
				shutil.rmtree(batch_folder_dir)
			
		os.makedirs(batch_folder_dir)
		self.copy_files(input_dir, batch_folder_dir, files)

	def get_flight_log_info(self, flight_log_path, files):
		"""
		Returns the flight log info (Dictionary of image path: flight log info).
		"""
		if flight_log_path is None:
			return None

		flight_log_info = {}

		flight_log_file = open(flight_log_path, 'r')

		bar = self._initialize_loading_bar(len(files), 'Extracting Flight Log Info')

		for line in flight_log_file:
			for file in files:
				if file in line:
					flight_log_info[file] = line
					self._update_loading_bar(bar, 1)
					break

		flight_log_file.close()

		return flight_log_info

	def batch_files(self, input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path=None, prefix=None):
		if not files or len(files) == 0:
			raise ValueError('Input directory is not specified')
		if batch_size <= 0:
			raise ValueError('Batch size is not specified or is invalid')
		if overlap_percent < 0 or overlap_percent > 100:
			raise ValueError('Overlap percent is not specified or is invalid')
		if flight_log_path is not None and not os.path.isfile(flight_log_path):
			raise ValueError('Flight log path is not a valid file')
		
		files = self.sort_files(files)

		num_batches = self.calculate_batches(files, batch_size)
		overlap_size = int(batch_size * overlap_percent / 100)

		flight_log_info = None
		if flight_log_path is not None:
			flight_log_info = self.get_flight_log_info(flight_log_path, files)

		bar = self._initialize_loading_bar(num_batches, 'Batching Images')

		batches = []

		for i in range(num_batches):
			batch_folder_dir = None
			if prefix is not None:
				batch_folder_dir = os.path.join(output_dir, prefix, 'batch_' + str(i + 1))
			else:
				batch_folder_dir = os.path.join(output_dir, 'batch_' + str(i + 1))

			start_index, end_index = self.calculate_indices(i, batch_size, overlap_size, files)
			start_timestamp, end_timestamp, start_file_metadata = self.get_file_metadata(files, start_index, end_index)
			batch_files = self.get_batch_files(files, start_index, end_index)

			self.create_batch_folder(input_dir, batch_folder_dir, batch_files)
			self._update_loading_bar(bar, 1)

			if flight_log_info is not None:
				batch_flight_log_path = os.path.join(batch_folder_dir, 'flight_log.txt')
				batch_flight_log_file = open(batch_flight_log_path, 'w')

				for file in batch_files:
					if file in flight_log_info:
						batch_flight_log_file.write(flight_log_info[file])

				batch_flight_log_file.close()

			# add batch info to batches list for later use
			batches.append([batch_folder_dir, start_timestamp, end_timestamp, start_file_metadata])

		return batches

	"""
	Don't think this is needed anymore, but keeping it just in case. This method allows you to batch multiple folders at once.
	def batch_file_dict(self, input_dir, output_dir, file_dict, batch_size, overlap_percent, flight_log_path=None):
		if not file_dict or len(file_dict) == 0:
			raise ValueError('Input directory is not specified')
		
		batches = {}
		
		for key in file_dict:
			self.logger.info('Batching folder type: %s', key)
			batches[key] = batch_files(input_dir, output_dir, file_dict[key], batch_size, overlap_percent, flight_log_path, key)

		return batches
	"""

	def batch_folder(self, input_dir, output_dir, batch_size, overlap_percent, flight_log_path=None):
		"""
		Creates a batch folder and copies files from the input directory to the batch folder.
		Validates the inputs before proceeding.
		"""

		if not input_dir or not os.path.isdir(input_dir):
			raise ValueError('Input directory is not specified or is invalid')

		files = self.get_image_files(input_dir)

		return self.batch_files(input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path)
	
	def run(self):
		# Validate parameters
		success, message = self.validate_parameters()
		if not success:
			self.logger.error(message)
			return
		
		# Get parameters
		batch_size = self.params['batch_batch_size'].get_value()
		overlap_percent = self.params['batch_overlap_percent'].get_value()
		output_dir = os.path.join(self.params['output_dir'].get_value(), 'batched_images')

		# Input directory is not specified if continuing from Extract Images, use it's output in that case
		input_dir = None
		if 'batch_input_image_dir' in self.params:
			input_dir = self.params['batch_input_image_dir'].get_value()
		else:
			input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

		# Flight log path is not specified if continuing from Georeference Images, use it's output in that case
		flight_log_path = None
		if 'batch_flight_log_path' in self.params:
			flight_log_path = self.params['batch_flight_log_path'].get_value()
		else:
			flight_log_path = os.path.join(self.params['output_dir'].get_value(), "flight_log.csv")

		# Process folder
		self.batch_folder(input_dir, output_dir, batch_size, overlap_percent, flight_log_path)

	def validate_parameters(self) -> (bool, str):
		success, message = super().validate_parameters()
		if not success:
			return success, message
		
		# Validate simple parameters
		if not 'batch_batch_size' in self.params:
			return False, 'Batch size parameter not found'
		
		if not 'batch_overlap_percent' in self.params:
			return False, 'Overlap percent parameter not found'
		
		batch_size = self.params['batch_batch_size'].get_value()
		overlap_percent = self.params['batch_overlap_percent'].get_value()

		if batch_size < 1:
			return False, 'Batch size is invalid'
		
		if overlap_percent < 0 or overlap_percent > 100:
			return False, 'Overlap percent is invalid'
		
		# Validate input directory
		input_dir = None
		if 'batch_input_image_dir' in self.params:
			input_dir = self.params['batch_input_image_dir'].get_value()
		else:
			input_dir = os.path.join(self.params['output_dir'].get_value(), "raw_images")

		if not os.path.isdir(input_dir):
			return False, 'Input directory does not exist'
		
		# Validate flight log
		flight_log_path = None
		if 'batch_flight_log_path' in self.params:
			flight_log_path = self.params['batch_flight_log_path'].get_value()
		else:
			flight_log_path = os.path.join(self.params['output_dir'].get_value(), "flight_log.csv")

		if not 'batch_flight_log_path' in self.params and not os.path.isfile(flight_log_path):
			return False, 'Flight log does not exist'
		# Flight log is optional when not continuing from Georeference Images, set it to None if it doesn't exist
		elif 'batch_flight_log_path' in self.params:
			self.params['batch_flight_log_path'].set_value(None)

		# Validate output directory
		output_dir = os.path.join(self.params['output_dir'].get_value(), 'batched_images')

		# if the output directory already exists and it's not empty, ask the user if they want to overwrite it
		if os.path.isdir(output_dir) and os.listdir(output_dir):
			self.logger.warning('Batched images folder already exists. Overwrite? (y/n)')
			overwrite = input()

			if overwrite.lower() != 'y':
				return False, 'Batched images folder not created'
			else:
				shutil.rmtree(output_dir)

		if not os.path.isdir(output_dir):
			os.makedirs(output_dir)

		return True, None