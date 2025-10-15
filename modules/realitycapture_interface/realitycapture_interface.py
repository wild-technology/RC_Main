from __future__ import annotations
from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import subprocess
import time
import os
import shutil
import glob
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
            default_value=True,
            description='Whether to display the RealityCapture output',
            prompt_user=False
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

        # TEMPORARILY COMMENTED OUT - Model generation not yet implemented
        # additional_params['rc_model_generate'] = Parameter(
        #     name='Generate Model',
        #     cli_short='r_m',
        #     cli_long='r_model_generate',
        #     type=bool,
        #     default_value=True,
        #     description='Whether to automatically generate the model',
        #     prompt_user=True
        # )

        # additional_params['rc_model_cull_poly'] = Parameter(
        #     name='Model Polygon Culling',
        #     cli_short='r_c',
        #     cli_long='r_model_cull_poly',
        #     type=bool,
        #     default_value=True,
        #     description='Whether to automatically cull large and floating polygons on the generated model',
        #     prompt_user=True
        # )

        # additional_params['rc_model_texture'] = Parameter(
        #     name='Model Texturing',
        #     cli_short='r_t',
        #     cli_long='r_model_texture',
        #     type=bool,
        #     default_value=True,
        #     description='Whether to automatically texture the generated model',
        #     prompt_user=True
        # )

        # additional_params['rc_model_simplify'] = Parameter(
        #     name='Model Simplification',
        #     cli_short='r_s',
        #     cli_long='r_model_simplify',
        #     type=bool,
        #     default_value=True,
        #     description='Whether to automatically simplify the generated model',
        #     prompt_user=True
        # )

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
        Returns the exit code.
        """
        self.__check_and_create_folder(os.path.join(cwd, log_folder))

        cur_time = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        output_path = os.path.join(cwd, log_folder, f"output_{cur_time}.txt")

        with open(output_path, "w") as output_file:
            result = subprocess.Popen(command, cwd=cwd, stdout=output_file, stderr=output_file,
                                      creationflags=subprocess.CREATE_NO_WINDOW if not display_output else subprocess.CREATE_NEW_CONSOLE)
            stdout, stderr = result.communicate()

        return_code = result.returncode

        if return_code != 0:
            self.logger.error(f"Command failed with exit code: {return_code}")
            self.logger.error(f"Check log file: {output_path}")

        return return_code

    def __get_flight_log_path(self, batch_path=None):
        """
        Returns the path to the flight log file.
        """

        # if batch_path is specified that means we are using batched images, so use the batched flight log
        # if it isn't specified, we are using a single folder of images, so we need to return the overall flight log
        if batch_path is not None:
            pattern = os.path.join(batch_path, "flight_log*_UTM.txt")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
            # Fallback to flight_log.txt
            fallback = os.path.join(batch_path, "flight_log.txt")
            if os.path.isfile(fallback):
                return fallback
            return None

        # if the flight log path is specified, use that
        if 'rc_flight_log_path' in self.params:
            return self.params['rc_flight_log_path'].get_value()

        # Geo module will output flight log to the output directory only if the extract images module is active
        # Otherwise it will output to the geo_input_image_dir directory
        if 'geo_input_image_dir' in self.params:
            return os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
        else:
            return os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")

    def __align_images(self, input_folder, output_folder, display_output=True):
        """
        Aligns images in a folder and saves the component file to the output folder.
        Batch script handles flight log discovery internally.
        """

        if not input_folder:
            raise ValueError("Input folder is not specified")

        if not os.path.isdir(input_folder):
            raise ValueError(f"Input folder {input_folder} is not a directory")

        if not os.path.isdir(output_folder):
            self.logger.info(f"Output folder does not exist. Creating folder: {output_folder}")
            os.mkdir(output_folder)

        self.__check_and_create_folder(output_folder)

        this_file_dir = os.path.dirname(os.path.realpath(__file__))
        scripts_dir = os.path.join(this_file_dir, 'RC_CLI', 'Scripts')
        log_dir = os.path.join(scripts_dir, "logs")

        exit_code = self.__run_subprocess(
            ["cmd", "/c", "AlignZonesSequentially.bat", input_folder, output_folder],
            scripts_dir,
            log_dir,
            display_output
        )

        # Check if batch script failed
        if exit_code != 0:
            self.logger.error(f"Batch script failed with exit code: {exit_code}")
            component_data = {'Success': False, 'Component Count': 0}
            scene_data = {'Success': False}
            return component_data, scene_data

        # Batch script already waits for RC to quit, so just give filesystem time to sync
        self.logger.info("Alignment complete, verifying outputs...")
        time.sleep(2)

        # Count exported component folders/files in output folder
        # RC exports component directories, not .rcalign files
        try:
            output_items = os.listdir(output_folder)
            # Count directories that start with "Component" or any .rcalign files
            component_dirs = [d for d in output_items if os.path.isdir(os.path.join(output_folder, d)) and d.startswith("Component")]
            component_files = [f for f in output_items if f.endswith(".rcalign")]
            outputted_component_count = len(component_dirs) + len(component_files)

            self.logger.info(f"Found {len(component_dirs)} component directories and {len(component_files)} component files")
        except Exception as e:
            self.logger.error(f"Error counting components: {e}")
            outputted_component_count = 0

        # Check for project file in input folder (where batch script saves it)
        try:
            project_files = [f for f in os.listdir(input_folder) if f.endswith(".rcproj")]
            outputted_scene = len(project_files) > 0
            if outputted_scene:
                self.logger.info(f"Project file saved: {project_files[0]}")
        except Exception as e:
            self.logger.error(f"Error checking for project file: {e}")
            outputted_scene = False

        component_data = {}
        component_data['Success'] = outputted_component_count > 0
        component_data['Component Count'] = outputted_component_count

        scene_data = {}
        scene_data['Success'] = outputted_scene
        return component_data, scene_data

    def __get_component_file_name(self, image_folder):
        """
        Gets the name of the component output file for a folder of images based on the start and end frame files.
        Recursively searches subfolders for images.
        """

        if image_folder is None or not os.path.isdir(image_folder):
            raise ValueError("Image folder is not specified or is invalid")

        # Recursively search for image files in all subfolders
        files = []
        for root, dirs, filenames in os.walk(image_folder):
            for f in filenames:
                if f.endswith((".png", ".heif", ".jpg", ".jpeg")):
                    files.append(f)

        if not files:
            raise ValueError(f"No image files found in {image_folder} or its subfolders")

        files.sort(key=lambda x: (parse_timestamp(x), parse_frame_number(x)))

        start_file = files[0]
        end_file = files[-1]

        start_timestamp = parse_timestamp_str(start_file)
        end_timestamp = parse_timestamp_str(end_file)

        timestamp_segment = f"{start_timestamp}-{end_timestamp}"

        component_metadata_ext = start_file.replace(start_timestamp, timestamp_segment)
        component_metadata_ext = component_metadata_ext.replace(f"_frame{parse_frame_number_str(start_file)}", "")
        component_metadata = os.path.splitext(component_metadata_ext)[0]

        component_name = f"{component_metadata}"

        return component_name

    def run(self):
        # Validate parameters
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        output_dir = os.path.join(self.params['output_dir'].get_value(), "aligned_components")
        display_output = self.params['rc_display_output'].get_value()

        this_file_dir = os.path.dirname(os.path.realpath(__file__))
        metadata_dir = os.path.join(this_file_dir, 'RC_CLI', 'Metadata')
        flight_log_params_path = os.path.join(metadata_dir, "FlightLogParams.xml")

        process_data = []

        def queue_folder_to_process(local_input_folder, local_output_dir, local_display_output):
            if not os.path.isdir(local_input_folder):
                raise ValueError(f"Input folder {local_input_folder} is not a directory")

            local_image_files = [f for f in os.listdir(local_input_folder) if
                                 f.endswith((".png", ".heif", ".jpg", ".jpeg"))]

            # only process the folder if there are image files in it
            if local_image_files and len(local_image_files) > 0:
                process_data.append({
                    'input_folder': local_input_folder,
                    'output_dir': local_output_dir,
                    'display_output': local_display_output
                })

            # queue all subfolders to be processed separately
            subfolders = [f for f in os.listdir(local_input_folder) if
                          os.path.isdir(os.path.join(local_input_folder, f))]
            for subfolder in subfolders:
                subfolder_path = os.path.join(local_input_folder, subfolder)
                queue_folder_to_process(subfolder_path, local_output_dir, local_display_output)

        # single folder input (not running after batched images module)
        if 'rc_input_image_dir' in self.params:
            input_folder = self.params['rc_input_image_dir'].get_value()

            try:
                queue_folder_to_process(input_folder, output_dir, display_output)
            except Exception as e:
                self.logger.error(f"Error queueing folder to process: {e}")
        # ----------------- MODIFIED LOGIC START -----------------
        # running after batched images module
        else:
            # Point to the correct directory created by BatchDirectory.py
            batch_directory = os.path.join(self.params['output_dir'].get_value(), "batched_images_by_zone")

            if not os.path.isdir(batch_directory):
                self.logger.error(f"Batch directory not found: {batch_directory}")
                self.logger.error("Please ensure the 'Batch Directory' module has been run successfully.")
                return {'Success': False, 'Message': 'Batch directory not found.'}

            # Find all subdirectories that contain 'zone' in their name
            batch_folders = [f for f in os.listdir(batch_directory)
                             if os.path.isdir(os.path.join(batch_directory, f)) and 'zone' in f.lower()]

            for batch_folder in batch_folders:
                batch_input_folder = os.path.join(batch_directory, batch_folder)
                # Create zone-specific output folder to avoid file collisions
                zone_output_dir = os.path.join(output_dir, batch_folder)

                try:
                    process_data.append({
                        'input_folder': batch_input_folder,
                        'output_dir': zone_output_dir,
                        'display_output': display_output
                    })
                except Exception as e:
                    self.logger.error(f"Error queueing folder to process: {e}")
        # ----------------- MODIFIED LOGIC END -----------------

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
            zone_output_dir = data['output_dir']
            display_output = data['display_output']

            zone_name = os.path.basename(input_folder)

            try:
                component_data, scene_data = self.__align_images(input_folder, zone_output_dir, display_output)

                output_data['Components'][zone_name] = component_data
                output_data['Scenes'][zone_name] = scene_data
            except Exception as e:
                self.logger.error(f"Error aligning images in {zone_name}: {e}")
                output_data['Components'][zone_name] = {'Success': False, 'Component Count': 0}
                output_data['Scenes'][zone_name] = {'Success': False}

            self._update_loading_bar(bar, 1)

        return output_data

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if not 'rc_display_output' in self.params:
            return False, 'Display output parameter not found'

        # Check if running after Batch Directory module (zones should exist)
        if 'rc_input_image_dir' not in self.params or self.params['rc_input_image_dir'].get_value() is None:
            # Running after Batch Directory - validate zones exist
            batch_directory = os.path.join(self.params['output_dir'].get_value(), 'batched_images_by_zone')

            if not os.path.isdir(batch_directory):
                return False, f'Batch directory not found: {batch_directory}. Please run Batch Directory module first.'

            # Check for zone folders
            zone_folders = [d for d in os.listdir(batch_directory)
                           if os.path.isdir(os.path.join(batch_directory, d)) and 'zone' in d.lower()]

            if not zone_folders:
                return False, f'No zone folders found in: {batch_directory}'

            self.logger.info(f"Found {len(zone_folders)} zones to process")

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