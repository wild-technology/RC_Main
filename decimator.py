#!/usr/bin/env python3
"""
Image Decimation Script

Decimates a folder of images by copying a user-selected percentage
to a new destination folder.
"""

import os
import shutil
from pathlib import Path
from typing import List, Tuple

try:
    from module_base.settings_store import SettingsStore
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    from module_base.settings_store import SettingsStore


def get_image_files(directory: Path) -> List[Path]:
    """
    Get all image files from the specified directory.

    Args:
        directory: Path to the directory containing images

    Returns:
        List of Path objects for image files
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif'}
    image_files = []

    for file in directory.iterdir():
        if file.is_file() and file.suffix.lower() in image_extensions:
            image_files.append(file)

    return sorted(image_files)


def get_valid_directory(settings: SettingsStore, key: str, prompt: str,
                        must_exist: bool = True) -> Path:
    """
    Prompt user for a directory path and validate it.
    The last-entered value is offered as the default.

    Args:
        settings: SettingsStore holding the last-entered values
        key: Settings key under the "decimator" section
        prompt: Message to display to user
        must_exist: Whether directory must already exist

    Returns:
        Valid Path object
    """
    while True:
        path_str = settings.prompt("decimator", key, prompt)
        path = Path(path_str).expanduser().resolve()

        if must_exist and not path.exists():
            print(f"Error: Directory does not exist: {path}")
            continue

        if must_exist and not path.is_dir():
            print(f"Error: Path is not a directory: {path}")
            continue

        return path


def get_decimation_ratio(settings: SettingsStore) -> int:
    """
    Prompt user to select decimation ratio in 20% increments.
    The last-entered choice is offered as the default.

    Returns:
        Percentage of images to keep (20, 40, 60, 80, or 100)
    """
    print("\nSelect decimation ratio (percentage of images to keep):")
    print("  1) 10%")
    print("  2) 20%")
    print("  3) 30%")
    print("  4) 40%")
    print("  5) 50% (no decimation)")

    ratio_map = {1: 10, 2: 20, 3: 30, 4: 40, 5: 50}

    while True:
        try:
            choice = int(settings.prompt("decimator", "decimation_choice",
                                         "Enter choice (1-5)"))
            if choice in ratio_map:
                return ratio_map[choice]
            else:
                print("Error: Please enter a number between 1 and 5")
        except ValueError:
            print("Error: Please enter a valid number")


def select_images_to_copy(image_files: List[Path], keep_percentage: int) -> List[Path]:
    """
    Select which images to copy based on decimation ratio.

    Args:
        image_files: List of all image file paths
        keep_percentage: Percentage of images to keep (20, 40, 60, 80, or 100)

    Returns:
        List of image paths to copy
    """
    total_images = len(image_files)
    num_to_keep = round(total_images * keep_percentage / 100)

    # Evenly distribute selection across the entire set
    if num_to_keep >= total_images:
        return image_files

    step = total_images / num_to_keep
    selected_indices = [round(i * step) for i in range(num_to_keep)]

    return [image_files[i] for i in selected_indices if i < total_images]


def copy_images(source_files: List[Path], destination_dir: Path) -> int:
    """
    Copy selected images to destination directory.

    Args:
        source_files: List of image paths to copy
        destination_dir: Destination directory path

    Returns:
        Number of files successfully copied
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    for source_file in source_files:
        destination_file = destination_dir / source_file.name
        try:
            shutil.copy2(source_file, destination_file)
            copied_count += 1
        except Exception as e:
            print(f"Warning: Failed to copy {source_file.name}: {e}")

    return copied_count


def confirm_operation(original_count: int, selected_count: int,
                      keep_percentage: int, source_dir: Path,
                      dest_dir: Path) -> bool:
    """
    Display operation summary and get user confirmation.

    Args:
        original_count: Total number of source images
        selected_count: Number of images to be copied
        keep_percentage: Percentage being kept
        source_dir: Source directory path
        dest_dir: Destination directory path

    Returns:
        True if user confirms, False otherwise
    """
    print("\n" + "=" * 60)
    print("DECIMATION SUMMARY")
    print("=" * 60)
    print(f"Source directory:       {source_dir}")
    print(f"Destination directory:  {dest_dir}")
    print(f"Original image count:   {original_count}")
    print(f"Decimation ratio:       {keep_percentage}%")
    print(f"Images to copy:         {selected_count}")
    print(f"Images to skip:         {original_count - selected_count}")
    print("=" * 60)

    while True:
        response = input("\nProceed with copy operation? (yes/no): ").strip().lower()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            return False
        else:
            print("Please enter 'yes' or 'no'")


def main():
    """Main execution function."""
    print("Image Decimation Tool")
    print("=" * 60)

    settings = SettingsStore()

    # Get source directory
    source_dir = get_valid_directory(
        settings, "source_dir",
        "\nEnter path to source image folder",
        must_exist=True
    )

    # Find all images
    print(f"\nScanning for images in: {source_dir}")
    image_files = get_image_files(source_dir)

    if not image_files:
        print("Error: No image files found in source directory")
        return

    print(f"Found {len(image_files)} image files")

    # Get decimation ratio
    keep_percentage = get_decimation_ratio(settings)

    # Get destination directory
    dest_dir = get_valid_directory(
        settings, "dest_dir",
        "\nEnter path to destination folder",
        must_exist=False
    )

    # Select images based on decimation ratio
    selected_images = select_images_to_copy(image_files, keep_percentage)

    # Confirm operation
    if not confirm_operation(
            len(image_files),
            len(selected_images),
            keep_percentage,
            source_dir,
            dest_dir
    ):
        print("\nOperation cancelled by user")
        return

    # Perform copy operation
    print("\nCopying images...")
    copied_count = copy_images(selected_images, dest_dir)

    print(f"\nOperation complete: {copied_count} images copied successfully")
    if copied_count < len(selected_images):
        print(f"Warning: {len(selected_images) - copied_count} images failed to copy")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
    except Exception as e:
        print(f"\nError: {e}")