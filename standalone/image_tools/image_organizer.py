#!/usr/bin/env python3
"""
Organize images into subdirectories by date extracted from filenames.

Processes files with pattern: YYYYMMDDTHHMMSS*
Moves them into subdirectories named like '29July'.

Usage:
    python -m standalone.image_tools.image_organizer /path/to/images
    python -m standalone.image_tools.image_organizer /path/to/images --dry-run
"""
import os
import re
import argparse
from pathlib import Path
from datetime import datetime
import shutil


def extract_date_from_filename(filename):
    """Extract date from filename pattern: 20250729T155918__DSC7725_ILCE-1.jpg"""
    pattern = r'^(\d{8})T\d{6}'
    match = re.match(pattern, filename)
    if match:
        return datetime.strptime(match.group(1), '%Y%m%d')
    return None


def get_human_readable_date(dt):
    """Convert datetime to human readable format like '29July'."""
    return dt.strftime('%d%B')


def organize_images_by_date(source_dir, dry_run=False):
    """Organize images in source_dir into subdirectories by date."""
    source_path = Path(source_dir)

    if not source_path.exists():
        print(f"Error: Directory '{source_dir}' does not exist")
        return

    image_extensions = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.raw', '.arw'}

    moved_count = 0
    skipped_count = 0

    for file_path in source_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
            dt = extract_date_from_filename(file_path.name)
            if dt:
                subdir_name = get_human_readable_date(dt)
                target_dir = source_path / subdir_name

                if dry_run:
                    print(f"Would move: {file_path.name} -> {subdir_name}/")
                    moved_count += 1
                else:
                    target_dir.mkdir(exist_ok=True)
                    target_path = target_dir / file_path.name
                    shutil.move(str(file_path), str(target_path))
                    print(f"Moved: {file_path.name} -> {subdir_name}/")
                    moved_count += 1
            else:
                print(f"Skipped (no date pattern): {file_path.name}")
                skipped_count += 1

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Complete: {moved_count} files {'would be ' if dry_run else ''}moved, {skipped_count} files skipped")


def main():
    parser = argparse.ArgumentParser(
        description='Organize images into subdirectories by date'
    )
    parser.add_argument('source_dir', help='Directory containing images')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done without making changes')

    args = parser.parse_args()
    organize_images_by_date(args.source_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
