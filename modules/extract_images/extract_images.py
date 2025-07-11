#!/usr/bin/env python3
from __future__ import annotations
import sys
import csv
import time
import logging
from datetime import datetime
from tqdm import tqdm

from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import os
import cv2
import shutil
from datetime import datetime, timedelta
from ..file_metadata_parser import parse_timestamp_str, parse_timestamp
import numpy as np


# from decord import VideoReader
# from decord import cpu, gpu

class ExtractImages(RCModule):
    def __init__(self, logger):
        super().__init__("Extract Images", logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['image_input_video'] = Parameter(
            name='Input Video File',
            cli_short='i_i',
            cli_long='i_input',
            type=str,
            default_value=None,
            description='Path to the input video file/folder',
            prompt_user=True
        )

        additional_params['image_output_fpm'] = Parameter(
            name='Image Extraction Frames Per Minute',
            cli_short='i_r',
            cli_long='i_output_fpm',
            type=float,
            default_value=1.0,
            description='The number of frames per minute to extract from the video file',
            prompt_user=True
        )

        additional_params['image_output_mpx'] = Parameter(
            name='Output Megapixels',
            cli_short='i_m',
            cli_long='i_mpx',
            type=int,
            default_value=3,
            description='The maximum number of megapixels for the output images',
            prompt_user=True
        )

        return {**super().get_parameters(), **additional_params}

    def __get_video_timestamp_str(self, video_path):
        # Parse the timestamp from the video filename
        video_timestamp_str = parse_timestamp_str(video_path)

        # If the timestamp could not be parsed, raise an error
        if video_timestamp_str == "19700101T000000Z" or video_timestamp_str == "19700101000000":
            raise ValueError("Could not parse timestamp from filename.")

        return video_timestamp_str

    def __get_video_timestamp(self, video_path):
        video_timestamp = parse_timestamp(video_path)

        # If the timestamp could not be parsed, raise an error
        if video_timestamp == datetime(1970, 1, 1, 0, 0, 0):
            raise ValueError("Could not parse timestamp from filename.")

        return video_timestamp

    def __extract_video_decord(self, video_path, output_folder, output_fpm, output_mpx) -> dict[str, any]:
        """
        Extracts a video using the decord library.
        https://medium.com/@haydenfaulkner/extracting-frames-fast-from-a-video-using-opencv-and-python-73b9b7dc9661
        """
        video_path = os.path.normpath(video_path)
        output_folder = os.path.normpath(output_folder)

        video_dir, video_filename = os.path.split(video_path)
        video_filename, video_extension = os.path.splitext(video_filename)

        video_timestamp_str = self.__get_video_timestamp_str(video_path)
        video_timestamp = self.__get_video_timestamp(video_path)

        vr = VideoReader(video_path, ctx=cpu(0))

        video_frame_count = len(vr)
        video_fps = vr.get_avg_fps()

        output_fps = output_fpm / 60
        skip_frames = round(video_fps / output_fps)

        if skip_frames < 1:
            skip_frames = 1

        overall_frames_list = list(range(0, video_frame_count, skip_frames))

        saved_count = 0

        add_frame_index = output_fps > 1

        # initialize the loading bar
        bar = self._initialize_loading_bar(len(overall_frames_list) - 1, "Extracting Frames from Video")

        def __process_frame(index):
            nonlocal saved_count

            current_overall_frame_number = (index - 1) * skip_frames

            # get the timestamp of the current frame (in seconds since the start of the video)
            frame_seconds_arr = vr.get_frame_timestamp(current_overall_frame_number).tolist()
            time_difference = (frame_seconds_arr[0] + frame_seconds_arr[1]) / 2

            # get timestamp of the current frame (in datetime format)
            new_timestamp = video_timestamp + timedelta(seconds=time_difference)
            new_timestamp_str = new_timestamp.strftime("%Y%m%dT%H%M%SZ")

            # the index of the frame within the current second (if the video is 30fps, this will be between 0 and 29)
            frame_index_in_second = int(current_overall_frame_number % output_fps)

            # output image info
            image_name = video_filename.replace(video_timestamp_str, new_timestamp_str)

            if add_frame_index:
                image_name = image_name + f"_frame{frame_index_in_second}"

            image_name = image_name + ".png"

            image_path = os.path.join(output_folder, image_name)

            frame = vr.get_batch([index]).asnumpy()[0]

            # compress frame to input_mpx if necessary
            input_height, input_width, _ = frame.shape
            input_mpx = input_height * input_width / 1000000

            if input_mpx > output_mpx:
                output_height = int(input_height * np.sqrt(output_mpx / input_mpx))
                output_width = int(input_width * np.sqrt(output_mpx / input_mpx))
                frame = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA)

            cv2.imwrite(image_path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))  # save the extracted image
            saved_count += 1  # increment our counter by one
            self._update_loading_bar(bar, 1)

        for i in range(0, len(overall_frames_list)):
            __process_frame(i)

        self._finish_loading_bar(bar)

        output_data = {}
        output_data['Success'] = True
        output_data['Input Frame Count'] = video_frame_count
        output_data['Extracted Frame Count'] = saved_count
        output_data['Input FPM'] = round(video_fps * 60, 1)

        return output_data

    # Old method, uses OpenCV. Slower than decord on large videos. Faster when extracting a small amount of frames.
    def __extract_video_cv2(self, video_path, output_folder, output_fpm, output_mpx) -> dict[str, any]:
        output_data = {}

        # Attempt to open video file
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.logger.error(f"Video file {video_path} could not be opened")
            # --- FIXED LINE ---
            output_data['Success'] = False
            return output_data

        # Get the video's timestamp
        video_timestamp_str = self.__get_video_timestamp_str(video_path)
        video_timestamp = self.__get_video_timestamp(video_path)

        # Parse the metadata from the video filename
        video_filename = os.path.splitext(os.path.basename(video_path))[0]

        # Video's original FPS
        video_frame_count = round(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps = cap.get(cv2.CAP_PROP_FPS)

        # Calculate how many frames to skip to get the desired output FPS
        output_fps = output_fpm / 60
        output_frame_duration = timedelta(seconds=1) / output_fps
        skip_frames = round(video_fps / output_fps)

        # If the video's FPS is less than the desired output FPS, skip every frame
        if skip_frames < 1:
            skip_frames = 1

        current_frame_number = 0
        extracted_count = 0

        expected_frame_count = video_frame_count // skip_frames

        # initialize the loading bar
        bar = self._initialize_loading_bar(expected_frame_count, "Extracting Frames from Video")

        while cap.isOpened():
            # Skip to the next frame to extract, saves some time when extracting at a low FPS
            next_frame_number = current_frame_number + skip_frames
            cap.set(cv2.CAP_PROP_POS_FRAMES, next_frame_number)

            ret, frame = cap.read()
            if not ret:
                break

            # Skip frames between desired output FPS
            if current_frame_number % skip_frames != 0:
                current_frame_number += 1
                continue

            # Calculate the new timecode based on the current frame
            new_timestamp = video_timestamp + ((current_frame_number // skip_frames) * output_frame_duration)
            new_timestamp_str = new_timestamp.strftime("%Y%m%dT%H%M%SZ")

            frame_index_in_second = int(current_frame_number % output_fps)

            # Generate the filename for the current frame
            # replace the timestamp in the filename with the new timestamp
            image_name = video_filename.replace(video_timestamp_str,
                                                new_timestamp_str) + f"_frame{frame_index_in_second}.png"
            image_path = os.path.join(output_folder, image_name)

            # compress frame to input_mpx if necessary
            input_height, input_width, _ = frame.shape
            input_mpx = input_height * input_width / 1000000

            if input_mpx > output_mpx:
                output_height = int(input_height * np.sqrt(output_mpx / input_mpx))
                output_width = int(input_width * np.sqrt(output_mpx / input_mpx))
                frame = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA)

            # Save the frame as an image
            cv2.imwrite(image_path, frame)

            current_frame_number = next_frame_number
            extracted_count += 1

            self._update_loading_bar(bar, 1)

        self._finish_loading_bar(bar)

        cap.release()

        output_data['Success'] = True
        output_data['Input Frame Count'] = video_frame_count
        output_data['Extracted Frame Count'] = extracted_count
        output_data['Input FPM'] = round(video_fps * 60, 1)

        return output_data

    def run(self):
        # Validate parameters
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return

        # Get parameters
        input_path = self.params['image_input_video'].get_value()
        output_folder = os.path.join(self.params['output_dir'].get_value(), 'raw_images')
        output_fpm = self.params['image_output_fpm'].get_value()
        output_mpx = self.params['image_output_mpx'].get_value()

        mov_files = []

        if os.path.isfile(input_path):
            # One .MOV file was specified
            # Separate the filename from the path, and add it to the list of .MOV files
            input_video = os.path.basename(input_path)
            mov_files.append(input_video)

            # Set the input path to the directory of the .MOV file
            input_path = os.path.dirname(input_path)
        else:
            # A directory of .MOV files was specified
            mov_files = [filename for filename in os.listdir(input_path) if
                         os.path.splitext(filename)[1].lower() == ".mov"]

        bar = self._initialize_loading_bar(len(mov_files), "Extracting Videos")

        overall_output_data = {}
        overall_output_data['Success'] = False
        overall_output_data['Total Input Frame Count'] = 0
        overall_output_data['Total Extracted Frame Count'] = 0
        overall_output_data['Output FPM'] = output_fpm
        overall_output_data['Number of Videos'] = len(mov_files)
        overall_output_data['Videos'] = {}

        for mov_file in mov_files:
            mov_path = os.path.join(input_path, mov_file)
            file_extension = os.path.splitext(mov_path)[1].lower()

            if not os.path.isfile(mov_path) or file_extension != '.mov':
                continue

            # individual_output_data = self.__extract_video_decord(mov_path, output_folder, output_fpm, output_mpx)
            individual_output_data = self.__extract_video_cv2(mov_path, output_folder, output_fpm, output_mpx)
            self._update_loading_bar(bar, 1)

            if individual_output_data is not None and individual_output_data.get('Success') == True:
                overall_output_data['Success'] = True
                overall_output_data['Total Input Frame Count'] += individual_output_data['Input Frame Count']
                overall_output_data['Total Extracted Frame Count'] += individual_output_data['Extracted Frame Count']
                overall_output_data['Videos'][mov_path] = individual_output_data
            else:
                self.logger.error(f'Failed to extract video: {mov_path}')

        return overall_output_data

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if not 'image_input_video' in self.params:
            return False, 'Input video parameter not found'

        if not 'output_dir' in self.params:
            return False, 'Output directory parameter not found'

        if not 'image_output_fpm' in self.params:
            return False, 'Output FPM parameter not found'

        if not 'image_output_mpx' in self.params:
            return False, 'Output MPX parameter not found'

        input_video = self.params['image_input_video'].get_value()
        is_input_folder = os.path.isdir(input_video)

        output_dir = os.path.join(self.params['output_dir'].get_value(), 'raw_images')
        output_fpm = self.params['image_output_fpm'].get_value()
        output_mpx = self.params['image_output_mpx'].get_value()

        # input folder could either be a .mov file or a folder of .mov files
        if is_input_folder:
            if not os.listdir(input_video):
                return False, 'Input folder is empty'
        else:
            if not os.path.isfile(input_video):
                return False, 'Input file does not exist'

            if os.path.splitext(input_video)[1].lower() != '.mov':
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

        if output_fpm <= 0:
            return False, 'Output FPM must be greater than 0'

        if output_mpx <= 0:
            return False, 'Output MPX must be greater than 0'

        return True, None