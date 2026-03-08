#!/usr/bin/env python3
"""
Standalone single-dive georeferencer.

Uses the shared geo_core functions directly — no RCModule framework needed.
Georeferences images from a single dive against a single ROV CSV datatable.

Usage:
    python -m standalone.georeference.geo_standalone \
        --image-dir /path/to/images \
        --flight-log /path/to/datatable.csv \
        --output-dir /path/to/output \
        --data-type All
"""
from __future__ import annotations
import argparse
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from modules.geo_core import (
    read_csv_data,
    read_image_filenames,
    estimate_locations,
    generate_flight_log,
)
from modules.naming import build_flight_log_name, parse_expedition_id, parse_dive_id


def main():
    parser = argparse.ArgumentParser(
        description='Standalone single-dive georeferencer'
    )
    parser.add_argument('--image-dir', required=True,
                        help='Directory containing images to georeference')
    parser.add_argument('--flight-log', required=True,
                        help='Path to ROV CSV datatable')
    parser.add_argument('--output-dir', default=None,
                        help='Output directory for flight log (defaults to image-dir)')
    parser.add_argument('--data-type', default='All',
                        choices=['WCA', 'WCA2025', 'Zeuss', 'All'],
                        help='Image timestamp format type')
    parser.add_argument('--magnetic-declination', type=float, default=0.0,
                        help='Magnetic declination in degrees (east positive)')
    parser.add_argument('--match-threshold', type=float, default=2.0,
                        help='Max seconds between image and CSV timestamp for a match')
    parser.add_argument('--include-subfolder', action='store_true',
                        help='Include camera subfolder in flight log filenames')
    parser.add_argument('--expedition', default=None,
                        help='Expedition ID (e.g., NA173). Auto-detected from paths if not set.')
    parser.add_argument('--dive', default=None,
                        help='Dive ID (e.g., H2102). Auto-detected from CSV filename if not set.')

    args = parser.parse_args()

    output_dir = args.output_dir or args.image_dir

    # Auto-detect expedition/dive from paths
    expedition = args.expedition or parse_expedition_id(args.flight_log) or parse_expedition_id(args.image_dir)
    dive = args.dive or parse_dive_id(args.flight_log) or parse_dive_id(args.image_dir)

    print("=" * 70)
    print("STANDALONE GEOREFERENCE - SINGLE DIVE")
    print("=" * 70)
    print(f"  Image Directory:     {args.image_dir}")
    print(f"  Flight Log (CSV):    {args.flight_log}")
    print(f"  Output Directory:    {output_dir}")
    print(f"  Data Type:           {args.data_type}")
    print(f"  Match Threshold:     {args.match_threshold}s")
    print(f"  Mag Declination:     {args.magnetic_declination}")
    if expedition:
        print(f"  Expedition:          {expedition}")
    if dive:
        print(f"  Dive:                {dive}")
    print("=" * 70)

    # Read CSV data
    print("\nReading CSV data...")
    data_rows = read_csv_data(args.flight_log)
    data_rows.sort(key=lambda row: row["TIME"])
    print(f"  Loaded {len(data_rows)} data rows")
    if data_rows:
        print(f"  CSV time range: {data_rows[0]['TIME']} to {data_rows[-1]['TIME']}")

    # Read images
    print("\nReading image filenames...")
    image_data = read_image_filenames(args.image_dir, args.data_type, validate_images=True)
    print(f"  Found {len(image_data)} valid images with timestamps")

    if not image_data:
        print("Error: No valid images found!")
        return

    # Estimate locations
    print("\nEstimating locations...")
    matched_images, stats = estimate_locations(
        image_data, data_rows,
        match_threshold_sec=args.match_threshold,
        magnetic_declination_deg=args.magnetic_declination,
    )

    utm_zone_number = stats.get('utm_zone_number')
    utm_zone_letter = stats.get('utm_zone_letter')

    # Generate flight log
    flight_log_name = build_flight_log_name(expedition, dive, utm_zone_number, utm_zone_letter)
    flight_log_path = os.path.join(output_dir, flight_log_name)
    os.makedirs(output_dir, exist_ok=True)

    generate_flight_log(
        matched_images, flight_log_path,
        magnetic_declination_deg=args.magnetic_declination,
        include_subfolder=args.include_subfolder,
    )

    # Summary
    print(f"\n{'=' * 70}")
    print("RESULTS")
    print(f"{'=' * 70}")
    print(f"  Images examined:     {len(image_data)}")
    print(f"  Matched (≤{args.match_threshold}s):     {stats['matches_made']}")
    print(f"  Rejected (>{args.match_threshold}s):     {stats['rejected_time']}")
    print(f"  Acceptance rate:     {100.0 * stats['matches_made'] / len(image_data):.1f}%")
    print(f"  UTM Zone:            {utm_zone_number}{utm_zone_letter}" if utm_zone_number else "  UTM Zone:            UNKNOWN")
    print(f"  Flight log written:  {flight_log_path}")
    print(f"  Lines written:       {stats['matches_made']}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
