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

def parse_unix_timestamp_str(filename):
	"""
	Extacts the unix timestamp from a filename (%Y%m%dT%H%M%SZ).
	"""
	pattern = r'\d{4}(0[1-9]|1[0-2])(0[1-9]|[1-2]\d|3[0-1])T([0-1]\d|2[0-3])[0-5]\d[0-5]\dZ'
	return extract_pattern(pattern, filename, "19700101T000000Z")

def parse_modified_unix_timestamp_str(filename):
	"""
	Extacts the modified unix timestamp from a filename (%Y%m%d%H%M%S).
	"""
	pattern = r'\d{4}(0[1-9]|1[0-2])(0[1-9]|[1-2]\d|3[0-1])([0-1]\d|2[0-3])[0-5]\d[0-5]\d'
	return extract_pattern(pattern, filename, "19700101000000")

def parse_unix_timestamp(filename):
	"""
	Extacts the unix timestamp from a filename (%Y%m%dT%H%M%SZ).
	"""
	unix_timestamp_str = parse_unix_timestamp_str(filename)
	return datetime.strptime(unix_timestamp_str, "%Y%m%dT%H%M%SZ")

def parse_modified_unix_timestamp(filename):
	"""
	Extacts the modified unix timestamp from a filename (%Y%m%d%H%M%S).
	"""
	modified_unix_timestamp_str = parse_modified_unix_timestamp_str(filename)
	return datetime.strptime(modified_unix_timestamp_str, "%Y%m%d%H%M%S")

def parse_timestamp_str(filename):
	"""
	Extacts the timestamp from a filename (%Y%m%dT%H%M%SZ or %Y%m%d%H%M%S).
	"""
	normal_timestamp_str = parse_unix_timestamp_str(filename)

	if normal_timestamp_str == "19700101T000000Z":
		return parse_modified_unix_timestamp_str(filename)
	
	return normal_timestamp_str
	
def parse_timestamp(filename):
	"""
	Extacts the timestamp from a filename (%Y%m%dT%H%M%SZ or %Y%m%d%H%M%S).
	"""
	normal_timestamp = parse_unix_timestamp(filename)

	if normal_timestamp == datetime(1970, 1, 1, 0, 0, 0):
		return parse_modified_unix_timestamp(filename)
	
	return normal_timestamp

def parse_frame_number_str(filename):
	"""
	Extracts a frame number from a filename.
	"""
	pattern = r'frame\d+'
	frame_number_str = extract_pattern(pattern, filename, None)

	if frame_number_str is None:
		return ""

	return frame_number_str[5:]

def parse_frame_number(filename):
	"""
	Extracts a frame number from a filename.
	"""
	frame_number_str = parse_frame_number_str(filename)

	if frame_number_str is None or frame_number_str == "":
		return sys.maxsize

	return int(frame_number_str)