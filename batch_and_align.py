import sys
import argparse
import logging
import os

from image_batch.batch_directory import batch_file_dict
from image_batch.batch_directory import get_image_files
from image_batch.file_metadata_parser import extract_pattern

from rc_interface.realitycapture_interface import align_images


def validate_inputs(file_dict, output_dir, flight_log_path, flight_log_params, batch_size, overlap_percent):
	"""
	Validates the inputs to the process_folder function.
	Raises a ValueError if any of the inputs are invalid.
	"""
	if not file_dict or len(file_dict) == 0:
		raise ValueError("No files to process")
	if not output_dir or output_dir == '' or output_dir == ' ':
		raise ValueError("Output directory is not specified or is invalid")
	if not os.path.isdir(output_dir):
		raise ValueError("Output directory does not exist")
	if flight_log_path and not os.path.isfile(flight_log_path):
		raise ValueError("Flight log does not exist")
	if (flight_log_params or flight_log_path) and not os.path.isfile(flight_log_params):
		raise ValueError("Flight log params does not exist")
	if batch_size < 1:
		raise ValueError("Batch size is not specified or is invalid")
	if overlap_percent < 0 or overlap_percent > 100:
		raise ValueError("Overlap percent is not specified or is invalid")


def process_folder(logger, input_dir, output_dir, file_dict, flight_log_path, flight_log_params, batch_size, overlap_percent, display_output=False):
	"""
	Batches and aligns a folder of input images. Outputs the aligned component to the output directory.
	"""
	try:
		validate_inputs(file_dict, output_dir, flight_log_path, flight_log_params, batch_size, overlap_percent)
	except ValueError as error:
		logger.error(error)
		return

	# batch_path, start_timestamp, end_timestamp, file_metadata
	batches: list[list[str]] = batch_file_dict(logger, input_dir, output_dir, file_dict, batch_size, overlap_percent, flight_log_path)

	for batch_key in batches:
		for batch in batches[batch_key]:
			batch_path = batch[0]
			start_timestamp = batch[1]
			end_timestamp = batch[2]
			file_metadata = batch[3]

			logger.info("Processing batch: %s", batch_path)
			
			component_name = f"{batch_key}_{start_timestamp}-{end_timestamp}_{file_metadata}.rcalign"
			batched_flight_log_path = os.path.join(batch_path, "flight_log.txt")

			if os.path.isfile(batched_flight_log_path):
				flight_log_path = batched_flight_log_path

			separator_output_dir = os.path.join(output_dir, batch_key)

			align_images(logger, batch_path, separator_output_dir, component_name, flight_log_path, flight_log_params, display_output)


def load_files(logger, input_dir, separator_pattern):
	"""
	Loads the files from the input directory.
	"""
	file_dict = {}
	files = get_image_files(logger, input_dir)

	for file in files:
		pattern_group = extract_pattern(separator_pattern, file, "default")

		if pattern_group not in file_dict:
			file_dict[pattern_group] = []

		file_dict[pattern_group].append(file)

	return file_dict


def main(argv):
	# initialize logger
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger(__name__)

	# parse arguments
	parser = argparse.ArgumentParser()
	parser.add_argument('-i', '--input_dir', type=str, help='Input directory')
	parser.add_argument('-o', '--output_dir', type=str, help='Output directory')
	parser.add_argument('-f', '--flight_log', type=str, help='Flight log path')
	parser.add_argument('-fp','--flight_log_params', type=str, help='Flight log params path')
	parser.add_argument('-b', '--batch_size', type=int, help='Batch size')
	parser.add_argument('-p', '--overlap_percent', type=float, help='Overlap percent')
	parser.add_argument('-d', '--display_output', type=bool, help='Display output')
	parser.add_argument('-s', '--separator_pattern', type=str, help='Separator pattern')

	args = parser.parse_args()

	# get arguments from user if not specified
	if args.input_dir is None:
		args.input_dir = input("Input directory: ")

	if args.output_dir is None:
		args.output_dir = input("Output directory: ")
		
	if args.flight_log is None:
		args.flight_log = input("Flight log path (or empty if no flight log): ")

		if args.flight_log == '' or args.flight_log == ' ':
			args.flight_log = None

	if args.flight_log is not None and args.flight_log_params is None:
		args.flight_log_params = input("Flight log params path: ")
		
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

	if args.display_output is None:
		try:
			args.display_output = input("Display output (y/n): ").lower() == 'y'
		except ValueError:
			args.display_output = False

	if args.separator_pattern is None:
		args.separator_pattern = input("Regex separator pattern (or empty if no separator): ")

		if not args.separator_pattern or args.separator_pattern == '' or args.separator_pattern == ' ':
			args.separator_pattern = None

	# validate arguments
	if not os.path.isdir(args.input_dir):
		logger.warning('Input directory does not exist')
		return

	if not os.path.isdir(args.output_dir):
		logger.warning('Output directory does not exist. Creating directory: %s', args.output_dir)
		os.makedirs(args.output_dir)

	if args.flight_log is not None and not os.path.isfile(args.flight_log):
		logger.warning('Flight log does not exist')
		args.flight_log = None
	
	if args.flight_log is not None and not os.path.isfile(args.flight_log_params):
		logger.warning('Flight log params does not exist')
		args.flight_log_params = None
		return
		
	if args.batch_size < 1:
		logger.warning('Batch size is not specified or is invalid. Using default value 100')
		args.batch_size = 100

	if args.overlap_percent < 0 or args.overlap_percent > 100:
		logger.warning('Overlap percent is not specified or is invalid. Using default value 20%')
		args.overlap_percent = 20

	logger.info('Input directory (i): %s', args.input_dir)
	logger.info('Output directory (o): %s', args.output_dir)
	logger.info('Flight log path (f): %s', args.flight_log)
	logger.info('Flight log params path (fp): %s', args.flight_log_params)
	logger.info('Batch size (b): %s', args.batch_size)
	logger.info('Overlap percent (p): %s', args.overlap_percent)
	logger.info('Display output (d): %s', args.display_output)
	logger.info('Separator pattern (s): %s', args.separator_pattern)

	file_dict = load_files(logger, args.input_dir, args.separator_pattern)
	num_files = sum([len(file_dict[group]) for group in file_dict])
	num_groups = len(file_dict)

	logger.info(f"Loaded {num_files} files in {num_groups} group{'s' if num_groups > 1 else ''}")

	logger.info("Processing folder: %s", args.input_dir)
	process_folder(logger, args.input_dir, args.output_dir, file_dict, args.flight_log, args.flight_log_params, args.batch_size, args.overlap_percent, args.display_output)

	logger.info('Done')


if __name__ == '__main__':
	main(sys.argv[1:])
