import os
import cv2
from datetime import datetime, timedelta
from image_batch.file_metadata_parser import parse_modified_unix_timestamp, parse_unix_timestamp

def extract_frames(video_path, output_folder, output_fps):
    if not os.path.isfile(video_path):
        raise ValueError("Video file does not exist")
    
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Video file could not be opened")
    
    video_timestamp = parse_modified_unix_timestamp(video_path)

    if video_timestamp > datetime.now():
        video_timestamp = parse_unix_timestamp(video_path)
        
    if video_timestamp > datetime.now():
        print("Could not parse timestamp from filename. Using current time.")
        video_timestamp = datetime.now()

    video_metadata = os.path.splitext(os.path.basename(video_path))[0]
    video_metadata = video_metadata[15:]

    video_fps = cap.get(cv2.CAP_PROP_FPS)

    output_frame_duration = timedelta(seconds=1) / output_fps
    skip_frames = round(video_fps / output_fps)

    current_frame_number = 0
    extracted_count = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        if current_frame_number % skip_frames != 0:
            current_frame_number += 1
            continue

        # Calculate the new timecode based on the current frame
        new_timestamp = video_timestamp + ((current_frame_number // skip_frames) * output_frame_duration)
        new_timestamp_str = new_timestamp.strftime("%Y%m%d%H%M%S")

        frame_index_in_second = current_frame_number % output_fps

        # Generate the filename for the current frame
        image_name = f"{new_timestamp_str}_{video_metadata}_frame{frame_index_in_second}.png"
        image_path = os.path.join(output_folder, image_name)

        # Save the frame as an image
        cv2.imwrite(image_path, frame)

        current_frame_number += 1
        extracted_count += 1

    cap.release()

    return extracted_count