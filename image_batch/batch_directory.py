import sys
import os
import argparse
import logging
import shutil
from file_metadata_parser import parse_unix_timestamp, parse_segment, parse_frame_number

OVERWRITE_CONFIRMATION = 'y'
MIN_OVERLAP_PERCENT = 0
MAX_OVERLAP_PERCENT = 100


def get_image_files(logger, input_dir):
	"""
	Returns a list of image files from the input directory.
	"""
	files = os.listdir(input_dir)

	for file in files:
		extension = os.path.splitext(file)[1]

		if extension not in [".png", ".jpg", ".jpeg"]:
			logger.warning(f"{file} is not an accepted file type")
			files.remove(file)

	return files


def sort_files(files):
	"""
	Sorts files based on unix timestamp, segment start time, and frame number.
	"""
	files.sort(key=lambda x: (parse_unix_timestamp(x), parse_segment(x)[0], parse_frame_number(x)))
	return files


def calculate_batches(files, batch_size):
	"""
	Calculates the number of batches needed to batch the files.
	"""
	num_batches = len(files) // batch_size

	if len(files) % batch_size != 0:
		num_batches += 1

	return num_batches


def copy_files(input_dir, batch_folder_dir, files):
	"""
	Copies files from the input directory to the batch folder.
	"""
	for file in files:
		file_path = os.path.join(input_dir, file)
		output_path = os.path.join(batch_folder_dir, file)

		shutil.copy(file_path, output_path)


def calculate_indices(index, batch_size, overlap_size, files):
	"""
	Calculates the start and end indices for a batch.
	"""
	start_index = (index * batch_size) - (overlap_size if index > 0 else 0)
	end_index = min((index + 1) * batch_size, len(files) - 1)

	return start_index, end_index


def get_file_metadata(files, start_index, end_index):
	"""
	Returns the file metadata for a batch.
	"""
	start_timestamp = parse_unix_timestamp(files[start_index]).strftime("%Y%m%dT%H%M%SZ")
	end_timestamp = parse_unix_timestamp(files[end_index]).strftime("%Y%m%dT%H%M%SZ")

	start_file_metadata = files[start_index][len(start_timestamp) - 1:]

	return start_timestamp, end_timestamp, start_file_metadata


def get_batch_files(files, start_index, end_index):
	"""
	Returns the files for a batch.
	"""
	return files[start_index:end_index]


def create_batch_folder(logger, input_dir, batch_folder_dir, files):
	"""
	Creates a batch folder and copies files from the input directory to the batch folder.
	If the batch folder already exists, asks the user for confirmation to overwrite.
	"""
	if os.path.isdir(batch_folder_dir):
		logger.warning('Batch folder "%s" already exists. Overwrite? (y/n)', batch_folder_dir)
		overwrite = input()

		if overwrite.lower() != OVERWRITE_CONFIRMATION:
			logger.warning('Batch folder not created')
			return
		else:
			shutil.rmtree(batch_folder_dir)
		
	os.makedirs(batch_folder_dir)
	copy_files(input_dir, batch_folder_dir, files)


def get_flight_log_info(flight_log_path, files):
	"""
	Returns the flight log info (Dictionary of image path: flight log info).
	"""
	if flight_log_path is None:
		return None

	flight_log_info = {}

	flight_log_file = open(flight_log_path, 'r')

	for line in flight_log_file:
		for file in files:
			if file in line:
				flight_log_info[file] = line
				break

	flight_log_file.close()

	return flight_log_info


def batch_files(logger, input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path=None, prefix="default"):
	if not files or len(files) == 0:
		raise ValueError('Input directory is not specified')
	if batch_size <= 0:
		raise ValueError('Batch size is not specified or is invalid')
	if overlap_percent < MIN_OVERLAP_PERCENT or overlap_percent > MAX_OVERLAP_PERCENT:
		raise ValueError('Overlap percent is not specified or is invalid')
	if flight_log_path is not None and not os.path.isfile(flight_log_path):
		raise ValueError('Flight log path is not a valid file')
	
	files = sort_files(files)

	num_batches = calculate_batches(files, batch_size)
	overlap_size = int(batch_size * overlap_percent / 100)

	flight_log_info = None
	if flight_log_path is not None:
		logger.info('Extracting flight log info: %s', flight_log_path)
		flight_log_info = get_flight_log_info(flight_log_path, files)
		logger.info('Flight log info extracted, %s/%s images found', len(flight_log_info), len(files))

	batches = []

	for i in range(num_batches):
		batch_folder_dir = os.path.join(output_dir, prefix, 'batch_' + str(i + 1))

		start_index, end_index = calculate_indices(i, batch_size, overlap_size, files)
		start_timestamp, end_timestamp, start_file_metadata = get_file_metadata(files, start_index, end_index)
		batch_files = get_batch_files(files, start_index, end_index)

		create_batch_folder(logger, input_dir, batch_folder_dir, batch_files)
		logger.info('Batch %s created', i + 1)

		# should clean this up later, just want to get it working for now
		# creates a new flight log txt file containing only the flight log info for the batch
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


def batch_file_dict(logger, input_dir, output_dir, file_dict, batch_size, overlap_percent, flight_log_path=None):
	if not file_dict or len(file_dict) == 0:
		raise ValueError('Input directory is not specified')
	
	batches = {}
	
	for key in file_dict:
		logger.info('Batching folder type: %s', key)
		batches[key] = batch_files(logger, input_dir, output_dir, file_dict[key], batch_size, overlap_percent, flight_log_path, key)

	return batches


def batch_folder(logger, input_dir, output_dir, batch_size, overlap_percent, flight_log_path=None):
	"""
	Creates a batch folder and copies files from the input directory to the batch folder.
	Validates the inputs before proceeding.
	"""

	if not input_dir or not os.path.isdir(input_dir):
		raise ValueError('Input directory is not specified or is invalid')

	files = get_image_files(logger, input_dir)

	return batch_files(logger, input_dir, output_dir, files, batch_size, overlap_percent, flight_log_path)


def main(argv):
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger(__name__)

	# parse arguments
	parser = argparse.ArgumentParser(description='Batch a directory of images into smaller batches with overlap')
	parser.add_argument('-i', '--input_dir', type=str, help='Input directory')
	parser.add_argument('-o', '--output_dir', type=str, help='Output directory')
	parser.add_argument('-b', '--batch_size', type=int, help='Batch size')
	parser.add_argument('-p', '--overlap_percent', type=float, help='Overlap percent')

	args = parser.parse_args()

	# get arguments from user if not specified
	if args.input_dir is None:
		args.input_dir = input("Input directory: ")

	if args.output_dir is None:
		args.output_dir = input("Output directory: ")

	if args.batch_size is None:
		try:
			args.batch_size = int(input("Batch size: "))
		except ValueError:
			args.batch_size = 0

	if args.overlap_percent is None:
		try:
			args.overlap_percent = float(input("Overlap percent: "))
		except ValueError:
			args.overlap_percent = -1

	# validate arguments
	if not os.path.isdir(args.output_dir):
		logger.warning('Output directory does not exist. Creating directory: %s', args.output_dir)
		os.makedirs(args.output_dir)

	if args.batch_size < 1:
		logger.warning('Batch size is not specified or is invalid. Using default value 100')
		args.batch_size = 100

	if args.overlap_percent < 0 or args.overlap_percent > 100:
		logger.warning('Overlap percent is not specified or is invalid. Using default value 20%')
		args.overlap_percent = 20

	logger.info('Input directory (i): %s', args.input_dir)
	logger.info('Output directory (o): %s', args.output_dir)
	logger.info('Batch size (b): %s', args.batch_size)
	logger.info('Overlap percent (p): %s', args.overlap_percent)

	logger.info("Batching folder: %s", args.input_dir)
	batch_folder(logger, args.input_dir, args.output_dir, args.batch_size, args.overlap_percent)

	logger.info('Done')


if __name__ == '__main__':
	main(sys.argv[1:])
