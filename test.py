# DEPRECATED: Use standalone/image_tools/image_organizer.py instead (parameterized paths).
import warnings
warnings.warn(
    "test.py is deprecated. Use standalone/image_tools/image_organizer.py instead.",
    DeprecationWarning, stacklevel=1
)

import os
import re
from pathlib import Path
from datetime import datetime
import shutil


def extract_date_from_filename(filename):
    """
    Extract date from filename pattern: 20250729T155918__DSC7725_ILCE-1.jpg
    Returns datetime object or None if pattern doesn't match
    """
    pattern = r'^(\d{8})T\d{6}'
    match = re.match(pattern, filename)

    if match:
        date_str = match.group(1)
        return datetime.strptime(date_str, '%Y%m%d')
    return None


def get_human_readable_date(dt):
    """
    Convert datetime to human readable format like '29July'
    """
    return dt.strftime('%d%B')


def organize_images_by_date(source_dir):
    """
    Organize images in source_dir into subdirectories by date
    """
    source_path = Path(source_dir)

    if not source_path.exists():
        print(f"Error: Directory '{source_dir}' does not exist")
        return

    # Image extensions to look for
    image_extensions = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.raw', '.arw'}

    # Track statistics
    moved_count = 0
    skipped_count = 0

    # Iterate through all files in the source directory
    for file_path in source_path.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in image_extensions:
            # Extract date from filename
            dt = extract_date_from_filename(file_path.name)

            if dt:
                # Create subdirectory name
                subdir_name = get_human_readable_date(dt)
                target_dir = source_path / subdir_name

                # Create subdirectory if it doesn't exist
                target_dir.mkdir(exist_ok=True)

                # Move file
                target_path = target_dir / file_path.name
                shutil.move(str(file_path), str(target_path))
                print(f"Moved: {file_path.name} -> {subdir_name}/")
                moved_count += 1
            else:
                print(f"Skipped (no date pattern): {file_path.name}")
                skipped_count += 1

    print(f"\nComplete: {moved_count} files moved, {skipped_count} files skipped")


if __name__ == "__main__":
    source_directory = r"D:\NA173 Shallow"
    organize_images_by_date(source_directory)