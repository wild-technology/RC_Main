from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import os
import shutil
from ..file_metadata_parser import parse_timestamp, parse_frame_number

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

	def __get_input_dir(self):
		"""
		Returns the input directory.
		"""
		if 'batch_input_image_dir' in self.params:
			return self.params['batch_input_image_dir'].get_value()
		else:
			return os.path.join(self.params['output_dir'].get_value(), "raw_images")
		
	def __get_flight_log_path(self):
		"""
		Returns the path to the flight log file.
		"""
		if 'batch_flight_log_path' in self.params:
			return self.params['batch_flight_log_path'].get_value()
		else:
			return os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")


	def __get_image_files(self, input_dir):
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

	def __sort_files(self, files):
		"""
		Sorts files based on unix timestamp and frame number.
		"""
		files.sort(key=lambda x: (parse_timestamp(x), parse_frame_number(x)))
		return files

	def __calculate_batches(self, files, batch_size):
		"""
		Calculates the number of batches needed to batch the files.
		"""
		num_batches = len(files) // batch_size

		if len(files) % batch_size != 0:
			num_batches += 1

		return num_batches

	def __copy_files(self, input_dir, batch_folder_dir, files):
		"""
		Copies files from the input directory to the batch folder.
		"""
		for file in files:
			file_path = os.path.join(input_dir, file)
			output_path = os.path.join(batch_folder_dir, file)

			shutil.copy(file_path, output_path)

	def __calculate_indices(self, index, batch_size, overlap_size, files):
		"""
		Calculates the start and end indices for a batch.
		"""
		start_index = (index * batch_size) - (overlap_size if index > 0 else 0)
		end_index = min((index + 1) * batch_size, len(files) - 1)

		return start_index, end_index

	def __get_batch_files(self, files, start_index, end_index):
		"""
		Returns the files for a batch.
		"""
		return files[start_index:end_index]

	def __create_batch_folder(self, input_dir, batch_folder_dir, files):
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
		self.__copy_files(input_dir, batch_folder_dir, files)

	def __get_flight_log_info(self, flight_log_path, files):
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

	def __batch_files(self, input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path=None, prefix=None):
		if not files or len(files) == 0:
			raise ValueError('Input directory is not specified')
		if batch_size <= 0:
			raise ValueError('Batch size is not specified or is invalid')
		if overlap_percent < 0 or overlap_percent > 100:
			raise ValueError('Overlap percent is not specified or is invalid')
		if flight_log_path is not None and not os.path.isfile(flight_log_path):
			raise ValueError('Flight log path is not a valid file')
		
		files = self.__sort_files(files)

		num_batches = self.__calculate_batches(files, batch_size)
		overlap_size = int(batch_size * overlap_percent / 100)

		flight_log_info = None
		if flight_log_path is not None:
			flight_log_info = self.__get_flight_log_info(flight_log_path, files)

		bar = self._initialize_loading_bar(num_batches, 'Batching Images')

		for i in range(num_batches):
			batch_folder_dir = None
			if prefix is not None:
				batch_folder_dir = os.path.join(output_dir, prefix, 'batch_' + str(i + 1))
			else:
				batch_folder_dir = os.path.join(output_dir, 'batch_' + str(i + 1))

			start_index, end_index = self.__calculate_indices(i, batch_size, overlap_size, files)
			batch_files = self.__get_batch_files(files, start_index, end_index)

			self.__create_batch_folder(input_dir, batch_folder_dir, batch_files)
			self._update_loading_bar(bar, 1)

			if flight_log_info is not None:
				batch_flight_log_path = os.path.join(batch_folder_dir, 'flight_log.txt')
				batch_flight_log_file = open(batch_flight_log_path, 'w')

				for file in batch_files:
					if file in flight_log_info:
						batch_flight_log_file.write(flight_log_info[file])

				batch_flight_log_file.close()

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

	def __batch_folder(self, input_dir, output_dir, batch_size, overlap_percent, flight_log_path=None):
		"""
		Creates a batch folder and copies files from the input directory to the batch folder.
		Validates the inputs before proceeding.
		"""

		if not input_dir or not os.path.isdir(input_dir):
			raise ValueError('Input directory is not specified or is invalid')

		files = self.__get_image_files(input_dir)

		return self.__batch_files(input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path)
	
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

		input_dir = self.__get_input_dir()
		flight_log_path = self.__get_flight_log_path()

		# Process folder
		self.__batch_folder(input_dir, output_dir, batch_size, overlap_percent, flight_log_path)

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
		input_dir = self.__get_input_dir()

		if not os.path.isdir(input_dir):
			return False, 'Input directory does not exist'
		
		# Validate flight log
		flight_log_path = self.__get_flight_log_path()

		if not flight_log_path is None and not os.path.isfile(flight_log_path):
			# if continuing from Georeference Images, flight log is required
			if not 'batch_flight_log_path' in self.params:
				return False, 'Flight log does not exist'
			# if continuing from Extract Images, flight log is optional, set it to none if it doesn't exist
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