import subprocess
import time
import os

OVERWRITE_CONFIRMATION = 'y'
DEFAULT_DISPLAY_OUTPUT = False


def check_and_create_folder(path, logger):
	"""
	Checks if a folder exists, if not, creates it.
	"""
	if not os.path.isdir(path):
		os.mkdir(path)
		logger.info(f"Created folder: {path}")


def run_subprocess(command, cwd, logger, display_output=DEFAULT_DISPLAY_OUTPUT):
	"""
	Runs a subprocess command and waits for it to finish.
	"""
	result = subprocess.Popen(command, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, 
							  creationflags=subprocess.CREATE_NO_WINDOW if not display_output else subprocess.CREATE_NEW_CONSOLE)
	stdout, stderr = result.communicate()

	if stderr:
		logger.error(f"Command error: {stderr}")


def align_images(logger, input_folder, output_folder, component_file_name, flight_log_path, flight_log_params_path, display_output=DEFAULT_DISPLAY_OUTPUT):
	"""
	Aligns images in a folder and saves the component file to the output folder.
	"""

	if not os.path.isdir(input_folder):
		logger.error("Input folder does not exist")
		return
	
	if not os.path.isdir(output_folder):
		logger.info("Output folder does not exist. Creating folder: %s", output_folder)
		os.mkdir(output_folder)

	check_and_create_folder(output_folder, logger)

	if flight_log_path is None:
		flight_log_path = ""

	cwd = os.getcwd()
	scripts_dir = os.path.join(cwd, 'RC_CLI', 'Scripts')

	run_subprocess(["cmd", "/c", "AlignImagesFromFolder.bat", input_folder, output_folder, flight_log_path, flight_log_params_path],
				   scripts_dir, logger, display_output)

	# subprocess returns early, wait for the program to finish before continuing
	while True:
		reality_capture_running = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq RealityCapture.exe'],
												 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

		if 'RealityCapture.exe' not in reality_capture_running.stdout:
			break

		time.sleep(1)

	logger.info("Finished aligning images and creating component file")

	generated_component_files = [f for f in os.listdir(output_folder) if f.startswith("Component") and f.endswith(".rcalign")]

	corrected_component_path = os.path.join(output_folder, component_file_name)

	if os.path.exists(corrected_component_path):
		logger.warning('Component "%s" already exists. Overwrite? (y/n)', corrected_component_path)
		overwrite = input()

		if overwrite.lower() != OVERWRITE_CONFIRMATION:
			logger.warning('Component not created')

			for generated_component_file in generated_component_files:
				generated_component_path = os.path.join(output_folder, generated_component_file)
				os.remove(generated_component_path)
			
			return
		else:
			os.remove(corrected_component_path)

	if not generated_component_files or len(generated_component_files) == 0:
		logger.error("Error in component creation")
		return
	
	generated_component_path = os.path.join(output_folder, generated_component_files[0])
	os.rename(generated_component_path, corrected_component_path)
