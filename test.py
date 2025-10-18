#!/usr/bin/env python3
"""
RealityScan Component Export and Rename Tool
Exports components and XMP metadata from all zones, then renames with expedition metadata
Requires: Python 3.13, RealityScan 2.0.2
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime
import shutil


def find_realityscan_exe():
    """
    Find RealityScan executable in common installation locations.

    Returns:
        Path to RealityScan.exe or None if not found
    """
    possible_paths = [
        Path("C:/Program Files/Epic Games/RealityScan_2.0/RealityScan.exe"),
        Path("C:/Program Files/Epic Games/RealityScan/RealityScan.exe"),
        Path("C:/Program Files (x86)/Epic Games/RealityScan_2.0/RealityScan.exe"),
        Path("C:/Program Files (x86)/Epic Games/RealityScan/RealityScan.exe"),
    ]

    # Check if it's in PATH
    if shutil.which("RealityScan.exe"):
        return Path(shutil.which("RealityScan.exe"))

    # Check common installation paths
    for path in possible_paths:
        if path.exists():
            return path

    return None


def delete_existing_xmp_files(base_dir):
    """
    Find and delete all existing .xmp files in base directory and subdirectories.

    Args:
        base_dir: Base directory to search for .xmp files

    Returns:
        Number of .xmp files deleted
    """
    print("Searching for existing .xmp files...")
    print()

    xmp_files = list(base_dir.rglob("*.xmp"))

    if not xmp_files:
        print("No existing .xmp files found.")
        print()
        return 0

    print(f"Found {len(xmp_files)} existing .xmp file(s)")
    print()

    # Ask for confirmation
    response = input(f"Delete {len(xmp_files)} existing .xmp file(s)? (yes/no): ").strip().lower()

    if response not in ['yes', 'y']:
        print("Skipping .xmp deletion.")
        print()
        return 0

    print()
    print("Deleting existing .xmp files...")

    deleted_count = 0
    for xmp_file in xmp_files:
        try:
            xmp_file.unlink()
            deleted_count += 1
            print(f"  Deleted: {xmp_file.relative_to(base_dir)}")
        except Exception as e:
            print(f"  ERROR deleting {xmp_file}: {str(e)}")

    print()
    print(f"Deleted {deleted_count} .xmp file(s)")
    print()

    return deleted_count


def run_realityscan_command(realityscan_exe, command_list):
    """
    Execute RealityScan CLI command and handle errors.

    Args:
        realityscan_exe: Path to RealityScan.exe
        command_list: List of command arguments for RealityScan

    Returns:
        CompletedProcess object
    """
    full_command = [str(realityscan_exe)] + command_list

    try:
        result = subprocess.run(
            full_command,
            check=True,
            capture_output=True,
            text=True
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"ERROR: RealityScan command failed with code {e.returncode}")
        if e.stdout:
            print(f"STDOUT: {e.stdout}")
        if e.stderr:
            print(f"STDERR: {e.stderr}")
        raise


def process_zone(realityscan_exe, project_path, output_dir, expedition, dive, zone_name, timestamp):
    """
    Process a single zone: export components and XMP, then rename files.

    Args:
        realityscan_exe: Path to RealityScan.exe
        project_path: Path to .rsproj file
        output_dir: Directory to save components
        expedition: Expedition name/number
        dive: Dive name/number
        zone_name: Zone name from folder
        timestamp: Timestamp string for naming

    Returns:
        Number of components exported
    """
    print(f"  Processing: {project_path.name}")
    print(f"  Zone: {zone_name}")

    # Build RealityScan command with XMP export settings
    command = [
        "-load", str(project_path),
        "-set", "xmpCamera=3",
        "-set", "xmpMerge=true",
        "-set", "xmpRig=true",
        "-set", "xmpCalibGroups=true",
        "-set", "xmpFlags=true",
        "-set", "xmpExGps=true",
        "-exportXMP",
        "-setMinComponentSize", "1",
        "-exportLatestComponents", str(output_dir) + "\\",
        "-quit"
    ]

    # Execute RealityScan export
    try:
        run_realityscan_command(realityscan_exe, command)
    except Exception as e:
        print(f"  ERROR: Failed to export components for {zone_name}")
        return 0

    # Find and rename all .rsalign files
    component_files = sorted(output_dir.glob("Component*.rsalign"))

    if not component_files:
        print(f"  WARNING: No component files found for {zone_name}")
        return 0

    # Rename components
    for counter, component_file in enumerate(component_files, start=1):
        new_name = f"{expedition}_{dive}_{zone_name}_{timestamp}_{counter}.rsalign"
        new_path = output_dir / new_name
        component_file.rename(new_path)
        print(f"    Renamed: {component_file.name} -> {new_name}")

    print(f"  Exported {len(component_files)} component(s)")
    print()

    return len(component_files)


def main():
    print("=" * 60)
    print("RealityScan Batch Component Export Tool")
    print("=" * 60)
    print()

    # Find RealityScan executable
    realityscan_exe = find_realityscan_exe()

    if not realityscan_exe:
        print("ERROR: RealityScan.exe not found in standard locations!")
        print()
        print("Please provide the full path to RealityScan.exe")
        print()
        manual_path = input("RealityScan.exe path: ").strip().strip('"')
        realityscan_exe = Path(manual_path)

        if not realityscan_exe.exists():
            print(f"ERROR: RealityScan not found at {realityscan_exe}")
            sys.exit(1)

    print(f"Using RealityScan: {realityscan_exe}")
    print()

    # Get expedition and dive info first
    expedition = input("Enter Expedition name/number: ").strip()
    dive = input("Enter Dive name/number: ").strip()

    print()

    # Get base directory containing zone subfolders
    base_dir_input = input("Enter base directory containing zone subfolders: ").strip().strip('"')
    base_dir = Path(base_dir_input)

    if not base_dir.exists():
        print(f"ERROR: Base directory not found: {base_dir}")
        sys.exit(1)

    if not base_dir.is_dir():
        print(f"ERROR: Path is not a directory: {base_dir}")
        sys.exit(1)

    print()
    print(f"Base directory: {base_dir}")
    print()

    # Delete existing .xmp files
    print("=" * 60)
    deleted_xmp_count = delete_existing_xmp_files(base_dir)
    print("=" * 60)
    print()

    # Get output directory
    print("Enter output directory for alignments:")
    print("  - For absolute path: C:\\path\\to\\alignments")
    print("  - For relative path: alignments")
    print()
    output_input = input("Output directory: ").strip().strip('"')

    # Determine if path is absolute or relative
    output_path = Path(output_input)
    if not output_path.is_absolute():
        output_dir = base_dir / output_input
    else:
        output_dir = output_path

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print(f"Output directory: {output_dir}")
    print()

    # Generate timestamp once for all exports
    timestamp = datetime.now().strftime("%m%d_%H%M")

    # Find all .rsproj files in subfolders
    project_files = list(base_dir.rglob("*.rsproj"))

    if not project_files:
        print("ERROR: No .rsproj files found in base directory or subfolders!")
        sys.exit(1)

    print(f"Found {len(project_files)} project(s) to process")
    print()
    print("=" * 60)
    print("Starting batch export...")
    print("=" * 60)
    print()

    # Process each project
    total_components = 0
    zone_summary = []

    for project_path in sorted(project_files):
        # Get zone name from parent folder
        zone_name = project_path.parent.name

        try:
            num_components = process_zone(
                realityscan_exe,
                project_path,
                output_dir,
                expedition,
                dive,
                zone_name,
                timestamp
            )
            total_components += num_components
            zone_summary.append({
                'zone': zone_name,
                'project': project_path.name,
                'components': num_components,
                'status': 'SUCCESS'
            })
        except Exception as e:
            print(f"  FAILED: {zone_name} - {str(e)}")
            print()
            zone_summary.append({
                'zone': zone_name,
                'project': project_path.name,
                'components': 0,
                'status': 'FAILED'
            })

    # Print summary
    print("=" * 60)
    print("EXPORT SUMMARY")
    print("=" * 60)
    print()
    print(f"Expedition: {expedition}")
    print(f"Dive: {dive}")
    print(f"Timestamp: {timestamp}")
    print(f"Output Location: {output_dir}")
    print()
    print(f"XMP files deleted: {deleted_xmp_count}")
    print(f"XMP files created: {total_components} (saved next to original images)")
    print()
    print(f"{'Zone':<20} {'Project':<30} {'Components':<12} {'Status':<10}")
    print("-" * 72)

    for item in zone_summary:
        print(f"{item['zone']:<20} {item['project']:<30} {item['components']:<12} {item['status']:<10}")

    print("-" * 72)
    print(f"{'TOTAL':<52} {total_components:<12}")
    print()
    print(f"Total components exported: {total_components}")
    print(f"Projects processed: {len(zone_summary)}")
    print(f"Successful: {sum(1 for x in zone_summary if x['status'] == 'SUCCESS')}")
    print(f"Failed: {sum(1 for x in zone_summary if x['status'] == 'FAILED')}")
    print()
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nProcess interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nFATAL ERROR: {str(e)}")
        sys.exit(1)
