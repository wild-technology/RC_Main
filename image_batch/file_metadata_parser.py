import sys
from datetime import datetime
from re import search


def extract_pattern(pattern, filename, default):
	"""
	Helper function to extract a pattern from a filename.
	"""
	if not filename or filename == '' or filename == ' ':
		return default
	
	if not pattern or pattern == '' or pattern == ' ':
		return default

	match = search(pattern, filename)

	return match.group() if match else default


def parse_unix_timestamp(filename):
	"""
	Extacts the unix timestamp from a filename (%Y%m%dT%H%M%SZ).
	"""
	pattern = r'\d{4}(0[1-9]|1[0-2])(0[1-9]|[1-2]\d|3[0-1])T([0-1]\d|2[0-3])[0-5]\d[0-5]\dZ'
	timestamp_str = extract_pattern(pattern, filename, datetime.max.strftime('%Y%m%dT%H%M%SZ'))

	return datetime.strptime(timestamp_str, '%Y%m%dT%H%M%SZ')


def parse_modified_unix_timestamp(filename):
	"""
	Extacts the modified unix timestamp from a filename (%Y%m%d%H%M%S).
	"""
	pattern = r'\d{4}(0[1-9]|1[0-2])(0[1-9]|[1-2]\d|3[0-1])([0-1]\d|2[0-3])[0-5]\d[0-5]\d'
	timestamp_str = extract_pattern(pattern, filename, datetime.max.strftime('%Y%m%d%H%M%S'))

	return datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')


def parse_frame_number(filename):
	"""
	Extracts a frame number from a filename.
	"""

	pattern = r'frame\d+'
	frame_number_str = extract_pattern(pattern, filename, None)

	if frame_number_str is None:
		return sys.maxsize

	return int(frame_number_str[5:])


def parse_segment(filename):
	"""
	Extracts a segment from a filename.
	"""
	pattern = r'\d+s_to_\d+s'
	segment_str = extract_pattern(pattern, filename, None)

	if segment_str is None:
		return sys.maxsize, sys.maxsize

	start_time = int(segment_str[:segment_str.find('s_to_')][:-1])
	end_time = int(segment_str[segment_str.find('s_to_') + 5:][:-1])

	return start_time, end_time
