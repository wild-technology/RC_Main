import sys
import os
import logging
import argparse

from extract_images.extract_images_from_video3 import extract_frames
from georeference.georeference_images import georeference_images

def parse_arguments(argv, logger):
	parser = argparse.ArgumentParser()
	parser.add_argument('-i', '--input_path', type=str, help='Video input path')
	parser.add_argument('-o', '--output_dir', type=str, help='Output directory')
	parser.add_argument('-f', '--flight_log', type=str, help='Flight log path')
	parser.add_argument('-fp','--flight_log_params', type=str, help='Flight log params path')
	parser.add_argument('-b', '--batch_size', type=int, help='Batch size')
	parser.add_argument('-p', '--overlap_percent', type=float, help='Overlap percent')
	parser.add_argument('-d', '--display_output', type=bool, help='Display output')
	parser.add_argument('-r', '--fps', type=int, help='Video frames per second')
	#parser.add_argument('-s', '--separator_pattern', type=str, help='Separator pattern')

	args = parser.parse_args() 

	# get arguments from user if not specified
	if args.input_path is None:
		args.input_path = input("Video input path: ")

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

	if args.fps is None:
		try:
			args.fps = float(input("Video frames per second: "))
		except ValueError:
			args.fps = 0

	# validate arguments
	if not os.path.isfile(args.input_path):
		raise ValueError('Input path does not exist')
	
	if os.path.splitext(args.input_path)[1].lower() != '.mov':
		raise ValueError('Input path is not an MOV file')
	
	if not os.path.isdir(args.output_dir):
		logger.warning('Output directory does not exist. Creating directory: %s', args.output_dir)
		os.makedirs(args.output_dir)

	if args.flight_log is not None and not os.path.isfile(args.flight_log):
		logger.warning('Flight log path does not exist')
		args.flight_log = None
		args.flight_log_params = None

	if args.flight_log is not None and os.path.splitext(args.flight_log)[1].lower() != '.tsv':
		raise ValueError('Flight log path is not a TSV file')

	#if args.flight_log is not None and not os.path.isfile(args.flight_log_params):
	#	raise ValueError('Flight log params path does not exist')
	
	if args.batch_size < 1:
		logger.warning('Batch size is not specified or is invalid. Using default value 100')
		args.batch_size = 100

	if args.overlap_percent < 0 or args.overlap_percent > 100:
		logger.warning('Overlap percent is not specified or is invalid. Using default value 20%')
		args.overlap_percent = 20

	if args.fps < 0:
		logger.warning('Video frames per second is not specified or is invalid. Using default value 30')
		args.fps = 30

	return args

def main(argv):
	# initialize logger
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger(__name__)
	
	# parse arguments
	args = parse_arguments(argv, logger)

	logger.info('Video input path (i): %s', args.input_path)
	logger.info('Output directory (o): %s', args.output_dir)
	logger.info('Flight log path (f): %s', args.flight_log)
	logger.info('Flight log params path (fp): %s', args.flight_log_params)
	logger.info('Batch size (b): %s', args.batch_size)
	logger.info('Overlap percent (p): %s', args.overlap_percent)
	logger.info('Display output (d): %s', args.display_output)
	logger.info('Video frames per second (r): %s', args.fps)

	image_dir = os.path.join(args.output_dir, 'images')

	# extract frames
	logger.info('Extracting frames from video...')
	frames = extract_frames(args.input_path, image_dir, args.fps)
	logger.info('Extracted %s frames from video', frames)

	# geo-reference images
	logger.info('Geo-referencing images...')
	georeference_images(args.flight_log, image_dir, args.output_dir)

	# batch images
	
	# align images


if __name__ == '__main__':
	main(sys.argv[1:])