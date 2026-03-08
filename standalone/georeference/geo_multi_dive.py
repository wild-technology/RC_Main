#!/usr/bin/env python3
"""
Standalone multi-dive georeferencer.

Processes images from multiple dives against their respective ROV CSV datatables.
Copies matched images into dive-specific subdirectories organized by camera type.
Validates copied images and offers to remove corrupt files.

Uses shared core modules for camera config, coordinate math, and naming conventions.

Usage:
    python -m standalone.georeference.geo_multi_dive \
        --image-base-dir /path/to/sorted/images \
        --rov-data-dir /path/to/csv/datatables \
        --output-dir /path/to/output
"""
from __future__ import annotations
import argparse
import os
import sys
import glob
import shutil
from datetime import timedelta
from collections import defaultdict
from multiprocessing import Pool, cpu_count
from tqdm import tqdm
from PIL import Image

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from modules.geo_core import (
    read_csv_data,
    estimate_locations,
    generate_flight_log,
    parse_timestamp_from_filename,
    filter_images_by_time_range,
)
from modules.camera_config import detect_camera_type, camera_subfolder_name
from modules.naming import extract_dive_number, build_flight_log_name, parse_expedition_id

NUM_WORKERS = max(1, cpu_count() - 1)


def find_all_edt_directories(base_dir: str) -> list[str]:
    """Find all 'edt' subdirectories under the base directory."""
    edt_dirs = []
    for root, dirs, files in os.walk(base_dir):
        if 'edt' in dirs:
            edt_dirs.append(os.path.join(root, 'edt'))
    return edt_dirs


def find_rov_datafiles(data_dir: str) -> dict[str, str]:
    """Find all ROV datafiles and map dive numbers to file paths."""
    dive_files = {}
    for filepath in glob.glob(os.path.join(data_dir, "*.csv")):
        filename = os.path.basename(filepath)
        dive_number = extract_dive_number(filename)
        if dive_number:
            dive_files[dive_number] = filepath
        else:
            print(f"Warning: Could not extract dive number from {filename}")
    return dive_files


def read_image_filenames_from_edt(edt_dirs: list[str]) -> list[dict]:
    """Read all JPEG image filenames from edt directories with timestamps."""
    image_data = []
    jpeg_extensions = {'.jpg', '.jpeg'}
    seen_files = set()
    camera_groups = defaultdict(list)

    all_jpeg_files = []
    for edt_dir in set(edt_dirs):
        if not os.path.isdir(edt_dir):
            continue
        for filename in os.listdir(edt_dir):
            if filename.startswith("."):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext in jpeg_extensions:
                full_path = os.path.join(edt_dir, filename)
                if full_path not in seen_files:
                    seen_files.add(full_path)
                    all_jpeg_files.append((filename, full_path))

    print(f"\nReading {len(all_jpeg_files)} images from {len(set(edt_dirs))} edt directories...")

    ts_parse_failures = 0
    failed_examples = []

    for filename, full_path in tqdm(all_jpeg_files, desc="Reading Image Data"):
        timestamp = parse_timestamp_from_filename(filename)
        if timestamp:
            image_data.append({
                "FILENAME": filename,
                "FULL_PATH": full_path,
                "TIMESTAMP": timestamp,
            })
            cam_type = detect_camera_type(filename)
            camera_groups[cam_type].append(timestamp)
        else:
            ts_parse_failures += 1
            if len(failed_examples) < 5:
                failed_examples.append(filename)

    print("\nCamera Timestamp Summary (pre-dive processing):")
    for cam_type, timestamps in camera_groups.items():
        if timestamps:
            start_time = min(timestamps)
            end_time = max(timestamps)
            duration = end_time - start_time
            print(f"  {cam_type:15s}  Start: {start_time}, End: {end_time}, Duration: {duration}")

    print(f"\n  Valid images with timestamps: {len(image_data)}")
    print(f"  Timestamp parse failures: {ts_parse_failures}")
    if failed_examples:
        print("  Examples of failed filenames:")
        for fn in failed_examples:
            print(f"    {fn}")

    return image_data


def _copy_single_image(args: tuple) -> dict:
    """Copy a single image to dive/camera subdirectory. For multiprocessing."""
    image, dive_number, output_dir = args
    src_path = image["FULL_PATH"]
    subfolder = camera_subfolder_name(image["FILENAME"])
    camera_dir = os.path.join(output_dir, dive_number, subfolder)
    os.makedirs(camera_dir, exist_ok=True)

    dst_path = os.path.join(camera_dir, image["FILENAME"])
    try:
        if os.path.exists(dst_path):
            if os.path.getsize(src_path) == os.path.getsize(dst_path):
                return {'success': True, 'camera_type': subfolder, 'skipped': True}
        shutil.copy2(src_path, dst_path)
        return {'success': True, 'camera_type': subfolder, 'skipped': False}
    except Exception as e:
        return {'success': False, 'error': str(e), 'filename': image["FILENAME"]}


def copy_matched_images(matched_images: list[dict], dive_number: str, output_dir: str) -> tuple[int, int, dict]:
    """Copy matched images to dive-specific subdirectory using multiprocessing."""
    dive_image_dir = os.path.join(output_dir, dive_number)
    os.makedirs(dive_image_dir, exist_ok=True)

    print(f"Copying {len(matched_images)} matched images using {NUM_WORKERS} workers...")
    copy_args = [(img, dive_number, output_dir) for img in matched_images]

    copied_count = 0
    failed_count = 0
    camera_copy_counts = defaultdict(int)

    with Pool(NUM_WORKERS) as pool:
        results = list(tqdm(pool.imap(_copy_single_image, copy_args), total=len(copy_args), desc="Copying Images"))

    for result in results:
        if result['success']:
            copied_count += 1
            camera_copy_counts[result['camera_type']] += 1
        else:
            print(f"  Error copying {result['filename']}: {result['error']}")
            failed_count += 1

    return copied_count, failed_count, camera_copy_counts


def _validate_single_image(filepath: str) -> dict:
    """Validate a single image for corruption."""
    filename = os.path.basename(filepath)
    try:
        with Image.open(filepath) as img:
            img.verify()
        with Image.open(filepath) as img:
            img.load()
        return {'valid': True, 'filename': filename, 'filepath': filepath}
    except Exception as e:
        return {'valid': False, 'filename': filename, 'filepath': filepath, 'error': str(e)}


def validate_and_cleanup_images(output_dir: str, dive_numbers: list[str]) -> dict:
    """Validate all copied images and offer to delete corrupt ones."""
    print(f"\n{'=' * 80}")
    print("IMAGE VALIDATION AND CLEANUP")
    print(f"{'=' * 80}")

    all_corrupt_files = []
    validation_stats = {
        'total_checked': 0, 'valid_images': 0, 'corrupt_images': 0,
        'per_dive_corrupt': defaultdict(list),
    }

    all_image_files = []
    dive_file_map = {}

    for dive_number in dive_numbers:
        dive_image_dir = os.path.join(output_dir, dive_number)
        if not os.path.exists(dive_image_dir):
            continue
        for camera_subdir in os.listdir(dive_image_dir):
            camera_path = os.path.join(dive_image_dir, camera_subdir)
            if not os.path.isdir(camera_path):
                continue
            for f in os.listdir(camera_path):
                if f.lower().endswith(('.jpg', '.jpeg')):
                    fp = os.path.join(camera_path, f)
                    all_image_files.append(fp)
                    dive_file_map[fp] = dive_number

    if not all_image_files:
        print("No images found to validate.")
        return validation_stats

    validation_stats['total_checked'] = len(all_image_files)
    print(f"\nValidating {len(all_image_files)} images using {NUM_WORKERS} workers...")

    with Pool(NUM_WORKERS) as pool:
        results = list(tqdm(pool.imap(_validate_single_image, all_image_files),
                           total=len(all_image_files), desc="Validating Images"))

    for result in results:
        if result['valid']:
            validation_stats['valid_images'] += 1
        else:
            validation_stats['corrupt_images'] += 1
            dive = dive_file_map[result['filepath']]
            validation_stats['per_dive_corrupt'][dive].append(result['filename'])
            all_corrupt_files.append(result)

    print(f"\nTotal Checked: {validation_stats['total_checked']}")
    print(f"Valid: {validation_stats['valid_images']}")
    print(f"Corrupt: {validation_stats['corrupt_images']}")

    if all_corrupt_files:
        response = input("\nDelete all corrupt images? (yes/no): ").strip().lower()
        if response == 'yes':
            deleted = 0
            for cf in all_corrupt_files:
                try:
                    os.remove(cf['filepath'])
                    deleted += 1
                except Exception as e:
                    print(f"  Failed to delete {cf['filename']}: {e}")
            print(f"Deleted: {deleted}")
            validation_stats['deleted'] = deleted
    else:
        print("No corrupt images found!")

    return validation_stats


def main():
    parser = argparse.ArgumentParser(description='Multi-dive georeferencer')
    parser.add_argument('--image-base-dir', required=True,
                        help='Base directory with sorted images (contains edt subdirs)')
    parser.add_argument('--rov-data-dir', required=True,
                        help='Directory containing ROV CSV datatables')
    parser.add_argument('--output-dir', required=True,
                        help='Output directory for dive subdirectories and flight logs')
    parser.add_argument('--expedition', default=None,
                        help='Expedition ID (e.g., NA173). Auto-detected if not set.')
    parser.add_argument('--magnetic-declination', type=float, default=0.0,
                        help='Magnetic declination in degrees (east positive)')
    parser.add_argument('--match-threshold', type=float, default=2.0,
                        help='Max seconds for timestamp matching')
    parser.add_argument('--data-type', default='All',
                        choices=['WCA', 'WCA2025', 'Zeuss', 'All'])
    parser.add_argument('--skip-validation', action='store_true',
                        help='Skip image validation after copying')

    args = parser.parse_args()

    expedition = args.expedition or parse_expedition_id(args.image_base_dir)

    print("=" * 80)
    print("GEOREFERENCE IMAGES - MULTI-DIVE PROCESSOR")
    print("=" * 80)
    print(f"Image Base Directory:  {args.image_base_dir}")
    print(f"ROV Data Directory:    {args.rov_data_dir}")
    print(f"Output Directory:      {args.output_dir}")
    print(f"Match Threshold:       {args.match_threshold} seconds")
    print(f"Magnetic Declination:  {args.magnetic_declination}")
    print(f"Worker Processes:      {NUM_WORKERS}")
    if expedition:
        print(f"Expedition:            {expedition}")
    print("=" * 80)

    print("\nSearching for 'edt' subdirectories...")
    edt_dirs = find_all_edt_directories(args.image_base_dir)
    print(f"Found {len(edt_dirs)} edt directories")
    if not edt_dirs:
        print("Error: No 'edt' subdirectories found!")
        return

    print("\nSearching for ROV datafiles...")
    dive_files = find_rov_datafiles(args.rov_data_dir)
    print(f"Found {len(dive_files)} dive datafiles:")
    for dive_num in sorted(dive_files.keys()):
        print(f"  {dive_num}: {os.path.basename(dive_files[dive_num])}")
    if not dive_files:
        print("Error: No ROV datafiles found!")
        return

    all_images = read_image_filenames_from_edt(edt_dirs)
    if not all_images:
        print("Error: No valid images with timestamps found!")
        return

    overall_stats = {
        'total_dives': len(dive_files),
        'total_matches': 0,
        'total_copied': 0,
        'total_failed_copy': 0,
    }
    processed_dives = []

    for dive_number, csv_path in sorted(dive_files.items()):
        print(f"\n{'#' * 80}")
        print(f"PROCESSING DIVE: {dive_number}")
        print(f"{'#' * 80}")

        try:
            print(f"Loading CSV: {os.path.basename(csv_path)}")
            data_rows = read_csv_data(csv_path)
            data_rows.sort(key=lambda row: row["TIME"])
            print(f"  Loaded {len(data_rows)} data rows")
            if data_rows:
                print(f"  CSV time range: {data_rows[0]['TIME']} to {data_rows[-1]['TIME']}")

            # Filter images to this dive's time range
            dive_images = filter_images_by_time_range(all_images, data_rows, buffer_sec=60)
            print(f"  Images in dive time range: {len(dive_images)} (of {len(all_images)} total)")

            matched_images, stats = estimate_locations(
                dive_images, data_rows,
                match_threshold_sec=args.match_threshold,
                magnetic_declination_deg=args.magnetic_declination,
            )

            utm_zone_number = stats.get('utm_zone_number')
            utm_zone_letter = stats.get('utm_zone_letter')

            if matched_images:
                copied_count, failed_count, camera_copy_counts = copy_matched_images(
                    matched_images, dive_number, args.output_dir
                )

                flight_log_name = build_flight_log_name(
                    expedition, dive_number.split('_')[-1] if '_' in dive_number else dive_number,
                    utm_zone_number, utm_zone_letter,
                )
                flight_log_path = os.path.join(args.output_dir, flight_log_name)
                generate_flight_log(
                    matched_images, flight_log_path,
                    magnetic_declination_deg=args.magnetic_declination,
                    include_subfolder=True,
                )

                overall_stats['total_matches'] += stats['matches_made']
                overall_stats['total_copied'] += copied_count
                overall_stats['total_failed_copy'] += failed_count
                processed_dives.append(dive_number)

                print(f"\n  Matched: {stats['matches_made']}, Copied: {copied_count}, Failed: {failed_count}")
                print(f"  Flight log: {flight_log_path}")
            else:
                print(f"\n  No images matched for dive {dive_number}")

        except Exception as e:
            print(f"Error processing dive {dive_number}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 80}")
    print("OVERALL SUMMARY")
    print(f"{'=' * 80}")
    print(f"Total Dives Processed:          {overall_stats['total_dives']}")
    print(f"Total Images Scanned:           {len(all_images)}")
    print(f"Total Matches Across All Dives: {overall_stats['total_matches']}")
    print(f"Total Images Copied:            {overall_stats['total_copied']}")
    print(f"Total Copy Failures:            {overall_stats['total_failed_copy']}")
    print(f"Output Directory:               {args.output_dir}")
    print(f"{'=' * 80}\n")

    if not args.skip_validation and processed_dives and overall_stats['total_copied'] > 0:
        validate_and_cleanup_images(args.output_dir, processed_dives)

    print("Processing complete!")


if __name__ == "__main__":
    main()
