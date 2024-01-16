import os
import cv2
from datetime import datetime, timedelta
import time

# Path to the folder containing .mov video files
folder_path = r'C:\Users\produ\OneDrive\Desktop\Process'

# Create a subfolder for the extracted images
output_folder = r'G:\Shared drives\Production\2024 Working Folder\RUMI\H2021\Zeuss\images'
os.makedirs(output_folder, exist_ok=True)

# Get a list of .mov files in the folder
video_files = [f for f in os.listdir(folder_path) if f.endswith('.mov')]

# Prompt for the frame extraction rate
fps = float(input("Enter the frame extraction rate (frames per second): "))

# Loop through each .mov file
for video_file in video_files:
    video_path = os.path.join(folder_path, video_file)

    # Read the video file
    cap = cv2.VideoCapture(video_path)

    # Get the timestamp from the file name
    timecode_str = video_file[:15]
    timecode = datetime.strptime(timecode_str, "%Y%m%dT%H%M%S")

    # Get the characters to append to the frame filenames
    append_chars = video_file[15:]

    # Get the total number of frames in the video
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Calculate the estimated number of extracted frames
    estimated_frames = round(total_frames / cap.get(cv2.CAP_PROP_FPS) * fps)

    # Print video metadata
    resolution = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    frame_rate = cap.get(cv2.CAP_PROP_FPS)
    length = total_frames / frame_rate

    print(f"Video metadata for {video_file}:")
    print(f"Resolution: {resolution[0]}x{resolution[1]}")
    print(f"Frame rate: {frame_rate} frames per second")
    print(f"Length: {length} seconds")
    print(f"Total frames: {total_frames} frames")
    print()
    print(f"Estimate for {video_file}: {estimated_frames} frames")

    # List the filenames that will be generated
    frame_interval = round(cap.get(cv2.CAP_PROP_FPS) / fps)
    frame_duration = timedelta(seconds=1) / fps

    current_frame = 0
    generated_filenames = []

    start_time = time.time()
    processed_frames = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # Calculate the new timecode based on the current frame
        new_timecode = timecode + (current_frame * frame_duration)
        new_timecode_str = new_timecode.strftime("%Y%m%dT%H%M%S")

        # Generate the filename for the current frame
        image_name = f"{new_timecode_str}{append_chars}.png"
        generated_filenames.append(image_name)

        current_frame += 1
        processed_frames += 1

        # Save the frame as an image
        image_path = os.path.join(output_folder, image_name)
        cv2.imwrite(image_path, frame)

        # Skip frames until the next desired frame
        skip_frames = frame_interval - 1
        for _ in range(skip_frames):
            cap.read()

        # Visual progress and estimated time remaining
        progress_percent = (processed_frames / estimated_frames) * 100
        elapsed_time = time.time() - start_time
        fps_processed = processed_frames / elapsed_time
        frames_remaining = estimated_frames - processed_frames
        estimated_time_remaining = frames_remaining / fps_processed

        print(f"Progress: {progress_percent:.2f}%  "
              f"Estimated Time Remaining: {estimated_time_remaining:.2f} seconds", end="\r")

    cap.release()

    # Print the filenames that will be generated
    print("\nFilenames:")
    for filename in generated_filenames:
        print(filename)

print("Image extraction complete!")
