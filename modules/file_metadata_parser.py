#!/usr/bin/env python3
import sys
import re
from datetime import datetime

# ——————————————————————————————————————————————————————————————
# Timestamp extraction
# ——————————————————————————————————————————————————————————————

# Matches either:
#   • WCA/Zeuss style:  20250705T020039Z
#   • Modified style:   20250705020039
# Optionally preceded by camlower_, cammid_, or camupper_
_TIMESTAMP_REGEX = re.compile(r'(?:camlower_|cammid_|camupper_)?(\d{8}T\d{6}Z|\d{14})')

def parse_timestamp_str(filename: str) -> str:
    """
    Extract the timestamp string from a filename.
    Returns a string in YYYYMMDDTHHMMSSZ form.
    """
    default = "19700101T000000Z"
    match = _TIMESTAMP_REGEX.search(filename or "")
    if not match:
        return default

    ts = match.group(1)
    if len(ts) == 14:
        # plain YYYYMMDDHHMMSS → convert to standard
        dt = datetime.strptime(ts, "%Y%m%d%H%M%S")
        return dt.strftime("%Y%m%dT%H%M%SZ")
    # already in YYYYMMDDTHHMMSSZ
    return ts

def parse_timestamp(filename: str) -> datetime:
    """
    Extract the timestamp from a filename and return a UTC datetime.
    """
    ts_str = parse_timestamp_str(filename)
    return datetime.strptime(ts_str, "%Y%m%dT%H%M%SZ")

# ——————————————————————————————————————————————————————————————
# Frame‐number extraction (unchanged)
# ——————————————————————————————————————————————————————————————

def parse_frame_number_str(filename: str) -> str:
    """
    Extracts a frame number string from a filename (e.g. 'frame123').
    """
    match = re.search(r'frame(\d+)', filename or "")
    return match.group(1) if match else ""

def parse_frame_number(filename: str) -> int:
    """
    Extracts a frame number integer from a filename, or sys.maxsize if none.
    """
    s = parse_frame_number_str(filename)
    return int(s) if s.isdigit() else sys.maxsize
