"""
Core georeferencing logic as plain functions.

Not tied to the RCModule framework — can be used by both the module
(georeference_images.py) and standalone scripts (geo_multi_dive.py,
geo_standalone.py).
"""
from __future__ import annotations
import os
import csv
import re
import bisect
from datetime import datetime, timedelta
from PIL import Image

from .camera_config import (
    get_camera_pitch_offset,
    get_camera_pitch_accuracy,
    get_camera_position_offsets,
    camera_subfolder_name,
    DEFAULT_YAW_ACCURACY,
    DEFAULT_ROLL_ACCURACY,
    DEFAULT_POS_ACCURACY,
)
from .coordinate_utils import convert_to_utm, apply_camera_position_offset, convert_to_rc_orientation
from .file_metadata_parser import parse_timestamp

# Timestamp formats
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
WCA_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
WCA2025_FILENAME_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

# Pre-compiled regex patterns
_REGEX_WCA2025 = re.compile(r'(\d{8}T\d{6}Z)')
_REGEX_WCA_ZEUSS = re.compile(r'(\d{14})')


def read_csv_data(filename: str, timestamp_format: str = TIMESTAMP_FORMAT) -> list[dict]:
    """Read and parse ROV CSV data.

    Returns a list of dicts with keys: TIME, LAT, LONG, DEPTH, HEADING_MAG, PITCH, ROLL.
    """
    data_rows = []
    with open(filename, "r") as csvfile:
        reader = csv.reader(csvfile, delimiter=',', quotechar='"')
        header = next(reader)
        header = [h.strip().strip('"') for h in header]
        idx_map = {name: index for index, name in enumerate(header)}

        if 'Timestamp' not in idx_map:
            raise ValueError(f"'Timestamp' column not found in CSV. Available columns: {header}")

        for row in reader:
            row = [val.strip().strip('"') for val in row]
            try:
                data_rows.append({
                    "TIME": datetime.strptime(row[idx_map['Timestamp']], timestamp_format),
                    "LAT": float(row[idx_map['kalman_lat']]) if row[idx_map['kalman_lat']] else None,
                    "LONG": float(row[idx_map['kalman_long']]) if row[idx_map['kalman_long']] else None,
                    "DEPTH": -abs(float(row[idx_map['kalman_depth']])) if row[idx_map['kalman_depth']] else None,
                    "HEADING_MAG": float(row[idx_map['kalman_yaw_deg']]) if row[idx_map['kalman_yaw_deg']] else None,
                    "PITCH": float(row[idx_map['kalman_pitch_deg']]) if row[idx_map['kalman_pitch_deg']] else None,
                    "ROLL": float(row[idx_map['kalman_roll_deg']]) if row[idx_map['kalman_roll_deg']] else None,
                })
            except (ValueError, KeyError):
                continue

    return data_rows


def parse_timestamp_from_filename(filename: str, data_type: str = "All") -> datetime | None:
    """Extract and parse timestamp from an image filename.

    Supports data_type: 'All', 'WCA2025', 'WCA', 'Zeuss'.
    """
    base_name = os.path.splitext(filename)[0]

    if data_type in ("All", "WCA2025"):
        # Try WCA2025 format: cam_YYYYMMDDTHHMMSSZ or YYYYMMDDTHHMMSSZ
        try:
            parts = base_name.split('_')
            if len(parts) >= 2:
                timestamp_part = parts[1]
                result = datetime.strptime(timestamp_part, WCA2025_FILENAME_TIMESTAMP_FORMAT)
                return result
        except (IndexError, ValueError):
            pass

        m = _REGEX_WCA2025.search(base_name)
        if m:
            try:
                return datetime.strptime(m.group(1), WCA2025_FILENAME_TIMESTAMP_FORMAT)
            except ValueError:
                pass

    if data_type in ("All", "WCA", "Zeuss"):
        # Try WCA/Zeuss format: 14-digit YYYYMMDDHHMMSS
        m = _REGEX_WCA_ZEUSS.search(base_name)
        if m:
            try:
                return datetime.strptime(m.group(1), WCA_FILENAME_TIMESTAMP_FORMAT)
            except ValueError:
                pass

    if data_type == "All":
        # Fallback to shared parser
        timestamp = parse_timestamp(filename)
        if timestamp is not None and timestamp != datetime(1970, 1, 1, 0, 0, 0):
            return timestamp

    return None


def is_valid_image(filepath: str) -> bool:
    """Check if a file is a valid JPEG image."""
    try:
        with Image.open(filepath) as im:
            im.verify()
        return True
    except Exception:
        return False


def read_image_filenames(
    image_folder: str,
    data_type: str = "All",
    validate_images: bool = True,
    progress_callback=None,
) -> list[dict]:
    """Read JPEG image filenames from a folder (recursively), extracting timestamps.

    Returns list of dicts with keys: FILENAME, FULL_PATH, TIMESTAMP.
    """
    jpeg_extensions = {'.jpg', '.jpeg'}
    image_data = []
    ts_parse_failures = 0

    jpeg_files = []
    for root, dirs, files in os.walk(image_folder):
        for filename in files:
            if os.path.splitext(filename.lower())[1] in jpeg_extensions:
                full_path = os.path.join(root, filename)
                jpeg_files.append((filename, full_path))

    for filename, full_path in jpeg_files:
        if validate_images and not is_valid_image(full_path):
            continue

        timestamp = parse_timestamp_from_filename(filename, data_type)
        if timestamp:
            image_data.append({
                "FILENAME": filename,
                "FULL_PATH": full_path,
                "TIMESTAMP": timestamp,
            })
        else:
            ts_parse_failures += 1

        if progress_callback:
            progress_callback(1)

    return image_data


def find_closest_timestamp_index(data_rows: list[dict], target_time: datetime) -> int:
    """Binary search for closest timestamp in sorted data_rows.

    Assumes data_rows is sorted by TIME. Returns index or -1 if empty.
    """
    if not data_rows:
        return -1

    times = [row["TIME"] for row in data_rows]
    idx = bisect.bisect_left(times, target_time)

    if idx == 0:
        return 0
    if idx == len(times):
        return len(times) - 1

    before = times[idx - 1]
    after = times[idx]
    if abs(target_time - before) <= abs(target_time - after):
        return idx - 1
    return idx


def estimate_locations(
    image_data: list[dict],
    data_rows: list[dict],
    match_threshold_sec: float = 2.0,
    magnetic_declination_deg: float = 0.0,
) -> tuple[list[dict], dict]:
    """Estimate location/orientation for each image using binary search.

    Args:
        image_data: List of image dicts with FILENAME, FULL_PATH, TIMESTAMP.
        data_rows: Sorted list of CSV row dicts with TIME, LAT, LONG, etc.
        match_threshold_sec: Max time difference for accepting a match.
        magnetic_declination_deg: Magnetic declination correction.

    Returns:
        (matched_images, stats_dict)
    """
    stats = {
        'matches_made': 0,
        'exact_matches': 0,
        'matches_1_4': 0,
        'matches_5_15': 0,
        'matches_gt15': 0,
        'rejected_time': 0,
        'accepted_missing_utm': 0,
        'accepted_missing_orientation': 0,
    }

    matched_images = []
    utm_zone_number = None
    utm_zone_letter = None

    if data_rows:
        data_rows.sort(key=lambda row: row["TIME"])

    for image in image_data:
        if not data_rows:
            continue

        closest_idx = find_closest_timestamp_index(data_rows, image["TIMESTAMP"])
        if closest_idx < 0:
            continue

        closest_match = data_rows[closest_idx]
        diff_sec = abs(closest_match["TIME"] - image["TIMESTAMP"]).total_seconds()

        if diff_sec == 0:
            stats['exact_matches'] += 1
        elif 1 <= diff_sec <= 4:
            stats['matches_1_4'] += 1
        elif 5 <= diff_sec <= 15:
            stats['matches_5_15'] += 1
        elif diff_sec > 15:
            stats['matches_gt15'] += 1

        if diff_sec > match_threshold_sec:
            stats['rejected_time'] += 1
            continue

        lat, lon = closest_match.get("LAT"), closest_match.get("LONG")
        easting, northing, zn, zl = convert_to_utm(lat, lon)
        if zn is not None and utm_zone_number is None:
            utm_zone_number = zn
            utm_zone_letter = zl

        forward_m, lateral_m, down_m = get_camera_position_offsets(image["FILENAME"])
        cam_x, cam_y, cam_alt = apply_camera_position_offset(
            easting, northing, closest_match.get("DEPTH"),
            closest_match.get("HEADING_MAG"),
            forward_m, lateral_m, down_m,
        )

        matched_image = {
            "FILENAME": image["FILENAME"],
            "FULL_PATH": image.get("FULL_PATH"),
            "TIMESTAMP": image["TIMESTAMP"],
            "LAT": lat,
            "LONG": lon,
            "UTM_X": cam_x,
            "UTM_Y": cam_y,
            "ALTITUDE_EST": cam_alt,
            "HEADING_MAG": closest_match.get("HEADING_MAG"),
            "PITCH_VEHICLE": closest_match.get("PITCH"),
            "ROLL_VEHICLE": closest_match.get("ROLL"),
        }

        matched_images.append(matched_image)
        stats['matches_made'] += 1

        if cam_x is None or cam_y is None:
            stats['accepted_missing_utm'] += 1
        if (closest_match.get("HEADING_MAG") is None or
                closest_match.get("PITCH") is None or
                closest_match.get("ROLL") is None):
            stats['accepted_missing_orientation'] += 1

    stats['utm_zone_number'] = utm_zone_number
    stats['utm_zone_letter'] = utm_zone_letter

    return matched_images, stats


def filter_images_by_time_range(
    all_images: list[dict],
    data_rows: list[dict],
    buffer_sec: float = 60.0,
) -> list[dict]:
    """Filter images to only those within the CSV data's time range (plus buffer).

    This prevents cross-dive false matching when processing multiple dives.
    """
    if not data_rows:
        return []

    sorted_rows = sorted(data_rows, key=lambda r: r["TIME"])
    csv_start = sorted_rows[0]["TIME"] - timedelta(seconds=buffer_sec)
    csv_end = sorted_rows[-1]["TIME"] + timedelta(seconds=buffer_sec)

    return [img for img in all_images if csv_start <= img["TIMESTAMP"] <= csv_end]


def generate_flight_log(
    matched_images: list[dict],
    output_path: str,
    magnetic_declination_deg: float = 0.0,
    include_subfolder: bool = False,
) -> str:
    """Write a RealityCapture-format flight log.

    Args:
        matched_images: List of matched image dicts.
        output_path: Full path for the output flight log file.
        magnetic_declination_deg: Magnetic declination for yaw correction.
        include_subfolder: If True, prefix filename with camera subfolder path.

    Returns:
        Path to the written flight log file.
    """
    if os.path.exists(output_path):
        os.remove(output_path)

    pos_x_acc, pos_y_acc, alt_acc = DEFAULT_POS_ACCURACY

    with open(output_path, "w") as f:
        f.write(
            "filename;X (East);Y (North);Alt;"
            "X Accuracy;Y Accuracy;Alt Accuracy;"
            "Yaw;Pitch;Roll;"
            "Yaw Accuracy;Pitch Accuracy;Roll Accuracy\n"
        )

        for image in matched_images:
            pitch_offset = get_camera_pitch_offset(image["FILENAME"])
            rc_yaw, rc_pitch, rc_roll = convert_to_rc_orientation(
                image.get("HEADING_MAG"),
                image.get("PITCH_VEHICLE"),
                image.get("ROLL_VEHICLE"),
                pitch_offset,
                magnetic_declination_deg,
            )

            pitch_acc = get_camera_pitch_accuracy(image["FILENAME"])

            def fmt(val):
                return f"{val:.6f}" if val is not None else ""

            if include_subfolder:
                subfolder = camera_subfolder_name(image["FILENAME"])
                name_field = f"{subfolder}/{image['FILENAME']}"
            else:
                name_field = image["FILENAME"]

            line = ";".join([
                name_field,
                fmt(image.get("UTM_X")),
                fmt(image.get("UTM_Y")),
                fmt(image.get("ALTITUDE_EST")),
                fmt(pos_x_acc),
                fmt(pos_y_acc),
                fmt(alt_acc),
                fmt(rc_yaw),
                fmt(rc_pitch),
                fmt(rc_roll),
                fmt(DEFAULT_YAW_ACCURACY),
                fmt(pitch_acc),
                fmt(DEFAULT_ROLL_ACCURACY),
            ])
            f.write(line + "\n")

    return output_path
