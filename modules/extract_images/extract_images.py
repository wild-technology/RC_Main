from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import os
import cv2
import shutil
from datetime import datetime, timedelta
from ..file_metadata_parser import parse_modified_unix_timestamp_str, parse_unix_timestamp_str

class ExtractImages(RCModule):
	def __init__(self, logger):
		super().__init__("Extract Images", logger)

	def get_parameters(self) -> dict[str, Parameter]:
		additional_params = {}

		additional_params['image_input_video_file'] = Parameter(
			name='Input Video File',
			cli_short='i_i',
			cli_long='i_input',
			type=str,
			default_value=None,
			description='Path to the input video file',
			prompt_user=True
		)

		additional_params['image_output_fps'] = Parameter(
			name='Image Extraction Frames Per Second',
			cli_short='i_r',
			cli_long='i_output_fps',
			type=float,
			default_value=1.0,
			description='The number of frames per second to extract from the video file',
			prompt_user=True
		)

		return {**super().get_parameters(), **additional_params}
		
	def __get_video_timestamp(self, video_path):
		# Parse the timestamp from the video filename
		video_timestamp = parse_modified_unix_timestamp_str(video_path)
		
		# If the timestamp could not be parsed using the modified timestamp method, try the original method
		if video_timestamp == "19700101000000":
			video_timestamp = parse_unix_timestamp_str(video_path)
		
		# If the timestamp still could not be parsed, raise an error
		if video_timestamp == "19700101T000000Z":
			raise ValueError("Could not parse timestamp from filename.")
		
		return video_timestamp
	
	def run(self):
		# Validate parameters
		success, message = self.validate_parameters()
		if not success:
			self.logger.error(message)
			return
		
		# Get parameters
		video_path = self.params['image_input_video_file'].get_value()
		output_folder = os.path.join(self.params['output_dir'].get_value(), 'raw_images')
		output_fps = self.params['image_output_fps'].get_value()

		# Attempt to open video file
		cap = cv2.VideoCapture(video_path)
		if not cap.isOpened():
			self.logger.error("Video file could not be opened")
			return
		
		# Get the video's timestamp
		video_timestamp_str = self.__get_video_timestamp(video_path)
		video_timestamp = datetime.strptime(video_timestamp_str, "%Y%m%d%H%M%S")

		# Parse the metadata from the video filename
		video_filename = os.path.splitext(os.path.basename(video_path))[0]
		
		# Video's original FPS
		video_fps = cap.get(cv2.CAP_PROP_FPS)

		# Calculate how many frames to skip to get the desired output FPS
		output_frame_duration = timedelta(seconds=1) / output_fps
		skip_frames = round(video_fps / output_fps)

		current_frame_number = 0
		extracted_count = 0

		# initialize the loading bar
		bar = self._initialize_loading_bar(cap.get(cv2.CAP_PROP_FRAME_COUNT) // skip_frames, "Extracting Images")

		while cap.isOpened():
			ret, frame = cap.read()
			if not ret:
				break

			# Skip frames between desired output FPS
			if current_frame_number % skip_frames != 0:
				current_frame_number += 1
				continue

			# Calculate the new timecode based on the current frame
			new_timestamp = video_timestamp + ((current_frame_number // skip_frames) * output_frame_duration)
			new_timestamp_str = new_timestamp.strftime("%Y%m%d%H%M%S")

			frame_index_in_second = int(current_frame_number % output_fps)

			# Generate the filename for the current frame
			# replace the timestamp in the filename with the new timestamp
			image_name = video_filename.replace(video_timestamp_str, new_timestamp_str) + f"_frame{frame_index_in_second}.png"
			image_path = os.path.join(output_folder, image_name)

			# Save the frame as an image
			cv2.imwrite(image_path, frame)

			current_frame_number += 1
			extracted_count += 1

			self._update_loading_bar(bar, 1)

		cap.release()

	def validate_parameters(self) -> (bool, str):
		success, message = super().validate_parameters()
		if not success:
			return success, message
		
		if not 'image_input_video_file' in self.params:
			return False, 'Input video file parameter not found'
		
		if not 'output_dir' in self.params:
			return False, 'Output directory parameter not found'
		
		if not 'image_output_fps' in self.params:
			return False, 'Output FPS parameter not found'

		input_file = self.params['image_input_video_file'].get_value()
		output_dir = os.path.join(self.params['output_dir'].get_value(), 'raw_images')
		output_fps = self.params['image_output_fps'].get_value()

		if not os.path.isfile(input_file):
			return False, 'Input file does not exist'
		
		if os.path.splitext(input_file)[1].lower() != '.mov':
			return False, 'Input path is not an MOV file'
		
		if os.path.isdir(output_dir) and os.listdir(output_dir):
			self.logger.warning('Extracted images folder already exists. Overwrite? (y/n)')
			overwrite = input()

			if overwrite.lower() != 'y':
				return False, 'Extracted images folder not created'
			else:
				shutil.rmtree(output_dir)

		if not os.path.isdir(output_dir):
			os.makedirs(output_dir)

		if output_fps <= 0:
			return False, 'Output FPS must be greater than 0'

		return True, None
	