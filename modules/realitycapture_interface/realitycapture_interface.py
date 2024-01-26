from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import subprocess
import time
import os
import shutil
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
			description='Directory containing the images to align',
			prompt_user=True,
			disable_when_module_active='Batch Directory'
		)

		additional_params['rc_flight_log_params'] = Parameter(
			name='Flight Log Parameters',
			cli_short='r_p',
			cli_long='r_flight_log_params',
			type=str,
			default_value=None,
			description='Path to the flight log parameters file (optional)',
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

		return {**super().get_parameters(), **additional_params}

	def __check_and_create_folder(self, path):
		"""
		Checks if a folder exists, if not, creates it.
		"""
		if not os.path.isdir(path):
			os.mkdir(path)
			self.logger.info(f"Created folder: {path}")

	def __run_subprocess(self, command, cwd, display_output=False):
		"""
		Runs a subprocess command and waits for it to finish.
		"""
		result = subprocess.Popen(command, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
								creationflags=subprocess.CREATE_NO_WINDOW if not display_output else subprocess.CREATE_NEW_CONSOLE)
		stdout, stderr = result.communicate()

		if stderr:
			self.logger.error(f"Command error: {stderr}")

	def __get_flight_log_path(self, batch_path=None):
		"""
		Returns the path to the flight log file.
		"""
		
		# if batch_path is specified that means we are using batched images, so use the batched flight log
		# if it isn't specified, we are using a single folder of images, so we need to return the overall flight log
		if batch_path is not None:
			return os.path.join(batch_path, "flight_log.txt")
		
		# Geo module will output flight log to the output directory only if the extract images module is active
		# Otherwise it will output to the geo_input_image_dir directory
		if 'geo_input_image_dir' in self.params:
			return os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
		else:
			return os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")

	def __align_images(self, input_folder, output_folder, component_file_name, flight_log_path, flight_log_params_path, display_output=False):
		"""
		Aligns images in a folder and saves the component file to the output folder.
		"""

		if not input_folder:
			raise ValueError("Input folder is not specified")

		if not os.path.isdir(input_folder):
			raise ValueError("Input folder {input_folder} is not a directory")
		
		if not os.path.isdir(output_folder):
			self.logger.info(f"Output folder does not exist. Creating folder: {output_folder}")
			os.mkdir(output_folder)

		self.__check_and_create_folder(output_folder)

		if flight_log_path is None or not os.path.isfile(flight_log_path):
			flight_log_path = ""

		if flight_log_params_path is None or not os.path.isfile(flight_log_params_path):
			flight_log_params_path = ""

		cwd = os.getcwd()
		scripts_dir = os.path.join(cwd, 'modules', 'realitycapture_interface', 'RC_CLI', 'Scripts')

		self.__run_subprocess(["cmd", "/c", "AlignImagesFromFolder.bat", input_folder, output_folder, flight_log_path, flight_log_params_path],
					scripts_dir, display_output)

		# subprocess returns early, wait for the program to finish before continuing
		while True:
			reality_capture_running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq RealityCapture.exe'],
													stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

			if 'RealityCapture.exe' not in reality_capture_running.stdout:
				break

			time.sleep(1)

		generated_component_files = [f for f in os.listdir(output_folder) if f.startswith("Component") and f.endswith(".rcalign")]
		component_path_base = os.path.join(output_folder, component_file_name)

		outputted_component_count = 0

		if not generated_component_files or len(generated_component_files) == 0:
			raise Exception("Error in RealityCapture alignment")

		# use index for loop so we can index the name
		for index, generated_component_file in enumerate(generated_component_files):
			generated_component_path = os.path.join(output_folder, generated_component_file)
			component_path = f"{component_path_base}_{index}.rcalign"

			if os.path.exists(component_path):
				self.logger.warning('Component "%s" already exists. Overwrite? (y/n)', component_path)
				overwrite = input()

				if overwrite.lower() != 'y':
					self.logger.warning('Component not created')
					os.remove(generated_component_path)
					continue
				else:
					os.remove(component_path)
				
			os.rename(generated_component_path, component_path)
			outputted_component_count += 1

		output_data = {}
		output_data['Success'] = True
		output_data['Component Count'] = outputted_component_count
		return output_data

	def __get_component_file_name(self, image_folder):
		"""
		Gets the name of the component output file for a folder of images based on the start and end frame files.
		"""

		if image_folder is None or not os.path.isdir(image_folder):
			raise ValueError("Image folder is not specified or is invalid")

		files = [f for f in os.listdir(image_folder) if f.endswith((".png", ".heif", ".jpg", ".jpeg"))]
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

	def run(self):
		# Validate parameters
		success, message = self.validate_parameters()
		if not success:
			self.logger.error(message)
			return {'Success': False}
		
		output_dir = os.path.join(self.params['output_dir'].get_value(), "aligned_components")
		flight_log_params_path = self.params['rc_flight_log_params'].get_value()
		display_output = self.params['rc_display_output'].get_value()

		output_data = {}
		output_data['Success'] = True
		output_data['Output Directory'] = output_dir

		# rc_input_image_dir is only specified if not using batched images
		# single folder input
		if 'rc_input_image_dir' in self.params:
			input_folder = self.params['rc_input_image_dir'].get_value()
			overall_flight_log_path = self.__get_flight_log_path()

			try:
				component_file_name = self.__get_component_file_name(input_folder)
			except Exception as e:
				self.logger.error(f"Error getting component file name: {e}")
				return {'Success': False}
			
			output_data['Component Count'] = 1
			output_data['Components'] = {}
			output_data['Components'][component_file_name] = self.__align_images(input_folder, output_dir, component_file_name, overall_flight_log_path, flight_log_params_path, display_output)
		# batched folder input
		else:
			batch_directory = os.path.join(self.params['output_dir'].get_value(), "batched_images")

			if not os.path.isdir(batch_directory):
				self.logger.error("Batched images folder does not exist")
				return
			
			batch_folders = [f for f in os.listdir(batch_directory) if os.path.isdir(os.path.join(batch_directory, f))]

			if not batch_folders or len(batch_folders) == 0:
				self.logger.error("No batch folders found")
				return
			
			bar = self._initialize_loading_bar(len(batch_folders), "Aligning Batches")

			output_data = {}
			output_data['Success'] = True
			output_data['Component Count'] = len(batch_folders)
			output_data['Components'] = {}

			for batch_folder in batch_folders:
				batch_input_folder = os.path.join(batch_directory, batch_folder)
				batch_flight_log_path = self.__get_flight_log_path(batch_input_folder)

				try:
					batch_component_file_name = self.__get_component_file_name(batch_input_folder)
				except Exception as e:
					self.logger.error(f"Error getting component file name: {e}")
					return {'Success': False}
				
				try:
					output_data['Components'][batch_component_file_name] = self.__align_images(batch_input_folder, output_dir, batch_component_file_name, batch_flight_log_path, flight_log_params_path, display_output)
				except Exception as e:
					self.logger.error(f"Error aligning images: {e}")
					return {'Success': False}
	
				self._update_loading_bar(bar, 1)

		return output_data

	def validate_parameters(self) -> (bool, str):
		success, message = super().validate_parameters()
		if not success:
			return success, message
		
		if not 'rc_flight_log_params' in self.params:
			return False, 'Flight log parameters file parameter not found'
		
		if not 'rc_display_output' in self.params:
			return False, 'Display output parameter not found'
		
		flight_log_params_path = self.params['rc_flight_log_params'].get_value()
		
		# don't need to check if the file exists because it's not required
		# if it doesn't exist, set the value to None so it's not passed to the subprocess
		if flight_log_params_path is not None:
			if not os.path.isfile(flight_log_params_path):
				self.params['rc_flight_log_params'].set_value(None)
			# make sure it's an XML file if it exists
			elif os.path.splitext(flight_log_params_path)[1].lower() != '.xml':
				return False, 'Flight log parameters file is not an XML file'
			
		# Validate output directory
		output_dir = os.path.join(self.params['output_dir'].get_value(), 'aligned_components')

		# if the output directory already exists and it's not empty, ask the user if they want to overwrite it
		if os.path.isdir(output_dir) and os.listdir(output_dir):
			self.logger.warning('Aligned components folder already exists. Overwrite? (y/n)')
			overwrite = input()

			if overwrite.lower() != 'y':
				return False, 'Aligned components folder not created'
			else:
				shutil.rmtree(output_dir)

		if not os.path.isdir(output_dir):
			os.makedirs(output_dir)
		
		return True, None
		
		# hard to validate the input folder/flight log because it could be a batched folder
		# validate those as they are used instead