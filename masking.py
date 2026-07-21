#!/usr/bin/env python3
"""
Script to rename image files with timestamp-first format and validate JPEG integrity.
Processes files matching pattern: cam*_YYYYMMDDTHHMMSSZ.jpg
Renames to: YYYYMMDDTHHMMSSZ_cam*.jpg
"""

import os
import re
from pathlib import Path
from PIL import Image
import argparse

try:
    from module_base.settings_store import SettingsStore
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from module_base.settings_store import SettingsStore


def extract_timestamp_and_prefix(filename):
    """
    Extract timestamp and camera prefix from filename.

    Args:
        filename: Original filename (e.g., 'cammid_20250524T103743Z.jpg')

    Returns:
        tuple: (timestamp, prefix) or (None, None) if pattern doesn't match
    """
    pattern = r'^(cam[a-z]+)_(\d{8}T\d{6}Z)\.jpg$'
    match = re.match(pattern, filename)

    if match:
        return match.group(2), match.group(1)
    return None, None


def validate_jpeg(filepath):
    """
    Validate that a JPEG file can be read and is not corrupted.

    Args:
        filepath: Path to JPEG file

    Returns:
        bool: True if valid, False if corrupted
    """
    try:
        with Image.open(filepath) as img:
            img.verify()
        # Reopen to ensure full validation
        with Image.open(filepath) as img:
            img.load()
        return True
    except Exception as e:
        print(f"  ERROR: {filepath.name} - {e}")
        return False


def process_directory(directory, dry_run=False):
    """
    Process all matching files in directory: rename and validate.

    Args:
        directory: Path to directory containing images
        dry_run: If True, show what would be done without making changes
    """
    dir_path = Path(directory)

    if not dir_path.exists():
        print(f"Error: Directory '{directory}' does not exist")
        return

    # Find all matching files
    files_to_process = []
    for filepath in dir_path.glob('cam*_*T*Z.jpg'):
        timestamp, prefix = extract_timestamp_and_prefix(filepath.name)
        if timestamp and prefix:
            new_name = f"{timestamp}_{prefix}.jpg"
            files_to_process.append((filepath, new_name))

    if not files_to_process:
        print("No files matching pattern 'cam*_YYYYMMDDTHHMMSSZ.jpg' found")
        return

    print(f"\nFound {len(files_to_process)} files to process\n")

    # Show rename plan
    print("Rename plan:")
    for old_path, new_name in files_to_process:
        print(f"  {old_path.name} -> {new_name}")

    if dry_run:
        print("\n[DRY RUN - No changes made]")
        return

    # Get user confirmation
    response = input("\nProceed with renaming? (yes/no): ").strip().lower()
    if response not in ['yes', 'y']:
        print("Operation cancelled")
        return

    # Perform renames
    print("\nRenaming files...")
    renamed_files = []
    for old_path, new_name in files_to_process:
        new_path = old_path.parent / new_name
        try:
            old_path.rename(new_path)
            renamed_files.append(new_path)
            print(f"  ✓ {new_name}")
        except Exception as e:
            print(f"  ERROR renaming {old_path.name}: {e}")

    # Validate all JPEGs
    print(f"\nValidating {len(renamed_files)} JPEG files...")
    valid_count = 0
    invalid_count = 0

    for filepath in renamed_files:
        if validate_jpeg(filepath):
            valid_count += 1
            print(f"  ✓ {filepath.name}")
        else:
            invalid_count += 1

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Summary:")
    print(f"  Files renamed: {len(renamed_files)}")
    print(f"  Valid JPEGs: {valid_count}")
    print(f"  Corrupted JPEGs: {invalid_count}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(
        description='Rename camera image files to timestamp-first format and validate JPEG integrity'
    )
    parser.add_argument(
        'directory',
        nargs='?',
        default=None,
        help='Directory containing image files (default: prompt, remembering the last-used directory)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )

    args = parser.parse_args()

    settings = SettingsStore()
    if args.directory is not None:
        directory = args.directory
        settings.set("masking", "directory", directory)
    else:
        directory = settings.prompt("masking", "directory",
                                    "Directory containing image files", ".")

    process_directory(directory, dry_run=args.dry_run)


if __name__ == '__main__':
    main()