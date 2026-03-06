from __future__ import annotations
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
			description='Path to the flight log file',
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
		"""
		self.__check_and_create_folder(os.path.join(cwd, log_folder))

		cur_time = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
		output_path = os.path.join(cwd, log_folder, f"output_{cur_time}.txt")

		output_file = open(output_path, "w")

		result = subprocess.Popen(command, cwd=cwd, stdout=output_file, stderr=output_file, 
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
		
		# if the flight log path is specified, use that
		if 'rc_flight_log_path' in self.params:
			return self.params['rc_flight_log_path'].get_value()

		# Geo module will output flight log to the output directory only if the extract images module is active
		# Otherwise it will output to the geo_input_image_dir directory
		if 'geo_input_image_dir' in self.params:
			return os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
		else:
			return os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")

	def __align_images(self, input_folder, output_folder, component_file_name, flight_log_path, flight_log_params_path, display_output=False, generate_model=True, cull_polygons=False, texture_model=False, simplify_model=False):
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

		if flight_log_params_path is None or not os.path.isfile(flight_log_params_path) or flight_log_path == "":
			flight_log_params_path = ""

		this_file_dir = os.path.dirname(os.path.realpath(__file__))
		scripts_dir = os.path.join(this_file_dir, 'RC_CLI', 'Scripts')

		generate_model_str = "true" if generate_model else "false"
		cull_polygons_str = "true" if cull_polygons else "false"
		texture_model_str = "true" if texture_model else "false"
		simplify_model_str = "true" if simplify_model else "false"

		log_dir = os.path.join(os.path.dirname(output_folder), "logs")

		self.__run_subprocess(["cmd", "/c", "AlignImagesFromFolder.bat", input_folder, output_folder, flight_log_path, flight_log_params_path, generate_model_str, cull_polygons_str, component_file_name, texture_model_str, simplify_model_str],
					scripts_dir, log_dir, display_output)

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
		outputted_scene = False

		if not generated_component_files or len(generated_component_files) == 0:
			return {'Success': False, 'Component Count': 0}, {'Success': False}

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

		generated_scene_files = [f for f in os.listdir(output_folder) if f.startswith("Scene") and f.endswith(".rcproj")]

		if generated_scene_files and len(generated_scene_files) == 1:
			generated_scene_path = os.path.join(output_folder, generated_scene_files[0])
			scene_path = f"{component_path_base}.rcproj"

			if os.path.exists(scene_path):
				self.logger.warning('Scene "%s" already exists. Overwrite? (y/n)', scene_path)
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
		display_output = self.params['rc_display_output'].get_value()
		generate_model = self.params['rc_model_generate'].get_value()
		cull_polygons = self.params['rc_model_cull_poly'].get_value()
		texture_model = self.params['rc_model_texture'].get_value()
		simplify_model = self.params['rc_model_simplify'].get_value()

		this_file_dir = os.path.dirname(os.path.realpath(__file__))
		metadata_dir = os.path.join(this_file_dir, 'RC_CLI', 'Metadata')
		flight_log_params_path = os.path.join(metadata_dir, "FlightLogParams.xml")

		process_data = []

		def queue_folder_to_process(local_input_folder, local_output_dir, local_flight_log_path, local_flight_log_params_path, local_display_output):
			if not os.path.isdir(local_input_folder):
				raise ValueError(f"Input folder {local_input_folder} is not a directory")
			
			local_image_files = [f for f in os.listdir(local_input_folder) if f.endswith((".png", ".heif", ".jpg", ".jpeg"))]

			# only process the folder if there are image files in it
			if local_image_files and len(local_image_files) > 0:
				local_component_file_name = self.__get_component_file_name(local_input_folder)

				process_data.append({
					'input_folder': local_input_folder,
					'output_dir': local_output_dir,
					'component_file_name': local_component_file_name,
					'flight_log_path': local_flight_log_path,
					'flight_log_params_path': local_flight_log_params_path,
					'display_output': local_display_output
				})

			# queue all subfolders to be processed separately
			subfolders = [f for f in os.listdir(local_input_folder) if os.path.isdir(os.path.join(local_input_folder, f))]
			for subfolder in subfolders:
				subfolder_path = os.path.join(local_input_folder, subfolder)

				queue_folder_to_process(subfolder_path, local_output_dir, local_flight_log_path, local_flight_log_params_path, local_display_output)

		# single folder input (not running after batched images module)
		if 'rc_input_image_dir' in self.params:
			input_folder = self.params['rc_input_image_dir'].get_value()
			overall_flight_log_path = self.__get_flight_log_path()

			try:
				queue_folder_to_process(input_folder, output_dir, overall_flight_log_path, flight_log_params_path, display_output)
			except Exception as e:
				self.logger.error(f"Error queueing folder to process: {e}")
		# running after batched images module
		else:
			batch_directory = os.path.join(self.params['output_dir'].get_value(), "batched_images")
			batch_folders = [f for f in os.listdir(batch_directory) if os.path.isdir(os.path.join(batch_directory, f))]

			for batch_folder in batch_folders:
				batch_input_folder = os.path.join(batch_directory, batch_folder)
				batch_flight_log_path = self.__get_flight_log_path(batch_input_folder)

				try:
					queue_folder_to_process(batch_input_folder, output_dir, batch_flight_log_path, flight_log_params_path, display_output)
				except Exception as e:
					self.logger.error(f"Error queueing folder to process: {e}")

		output_data = {}
		output_data['Success'] = True
		output_data['Output Directory'] = output_dir
		output_data['Component Count'] = len(process_data)
		output_data['Components'] = {}
		output_data['Scenes'] = {}

		bar = self._initialize_loading_bar(len(process_data), "Aligning Batches")

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
				component_data, scene_data = self.__align_images(input_folder, output_dir, component_file_name, flight_log_path, flight_log_params_path, display_output, generate_model, cull_polygons, texture_model, simplify_model)
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
			self.params['rc_model_cull_poly'] = False

		if not 'rc_model_texture' in self.params:
			self.params['rc_model_texture'] = False

		if not 'rc_model_simplify' in self.params:
			self.params['rc_model_simplify'] = False
			
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