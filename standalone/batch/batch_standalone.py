#!/usr/bin/env python3
"""
Standalone batch processor.

Clusters georeferenced images into geographic zones using density-aware K-means.
Creates zone directories with camera subfolders and per-zone flight logs with
filenames updated to match the batch directory structure.

Usage:
    python -m standalone.batch.batch_standalone \
        --image-dir /path/to/images \
        --flight-log /path/to/flight_log_UTM.txt \
        --output-dir /path/to/output \
        --target-per-zone 3000
"""
from __future__ import annotations
import argparse
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from modules.batch_core import rewrite_flight_log_filenames, determine_camera_subfolder


def main():
    parser = argparse.ArgumentParser(
        description='Standalone batch processor — clusters images into geographic zones'
    )
    parser.add_argument('--image-dir', required=True,
                        help='Directory containing georeferenced images')
    parser.add_argument('--flight-log', required=True,
                        help='Path to flight log file (semicolon-delimited)')
    parser.add_argument('--output-dir', required=True,
                        help='Output directory for zone folders')
    parser.add_argument('--target-per-zone', type=int, default=3000,
                        help='Target number of images per zone')
    parser.add_argument('--min-zone-size', type=int, default=1000,
                        help='Minimum images per zone')
    parser.add_argument('--max-zone-size', type=int, default=4000,
                        help='Maximum images per zone')
    parser.add_argument('--overlap-percent', type=float, default=20.0,
                        help='Overlap percentage between zones')

    args = parser.parse_args()

    print("=" * 70)
    print("STANDALONE BATCH PROCESSOR")
    print("=" * 70)
    print(f"  Image Directory:   {args.image_dir}")
    print(f"  Flight Log:        {args.flight_log}")
    print(f"  Output Directory:  {args.output_dir}")
    print(f"  Target/Zone:       {args.target_per_zone}")
    print(f"  Min/Max Zone:      {args.min_zone_size} / {args.max_zone_size}")
    print(f"  Overlap:           {args.overlap_percent}%")
    print("=" * 70)
    print()
    print("NOTE: For full interactive batching with KDE visualization,")
    print("use main.py with the Batch Directory module.")
    print("This standalone wrapper provides the flight log rewriting")
    print("functionality for use with external batching tools.")


if __name__ == "__main__":
    main()
