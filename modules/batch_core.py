"""
Core batching logic as plain functions.

Handles reading flight logs, creating zone-specific flight logs with
updated filenames that match the batch directory structure, and
generating XMP sidecar files.

Not tied to the RCModule framework — can be used by both the module
(batch_directory.py) and standalone scripts (batch_standalone.py).
"""
from __future__ import annotations
import os
import re

import pandas as pd

from .camera_config import detect_camera_type, camera_subfolder_name


def read_flight_log(flight_log_path: str) -> tuple[pd.DataFrame | None, str]:
    """Read a flight log file into a DataFrame.

    Standardizes column names: 'Name' -> 'filename'.

    Returns:
        (dataframe, utm_zone_suffix) where utm_zone_suffix is extracted
        from the filename pattern flight_log_*_UTM_*.txt.
    """
    if flight_log_path is None or not os.path.isfile(flight_log_path):
        return None, ""

    basename = os.path.basename(flight_log_path)
    utm_zone_suffix = ""

    # Extract UTM zone info from filename
    # Supports both old format: flight_log_{zone}_UTM.txt
    # and new format: flight_log_{exp}_{dive}_UTM_{zone}.txt
    if "_UTM" in basename:
        zone_part = basename.replace("flight_log_", "").replace(".txt", "")
        utm_zone_suffix = f"_{zone_part}"

    df = pd.read_csv(flight_log_path, delimiter=';')

    if 'Name' in df.columns:
        df = df.rename(columns={'Name': 'filename'})

    return df, utm_zone_suffix


def rewrite_flight_log_filenames(
    flight_log_df: pd.DataFrame,
    zone_files: list[str],
) -> pd.DataFrame:
    """Create a zone-specific flight log with filenames updated to match
    the batch directory structure (camera_subfolder/basename).

    The input flight log may have filenames as:
        - bare: 'camlower_20250705T020039Z.jpg'
        - with subfolder: 'CamUpper/image.jpg'

    Output always uses standardized subfolder names from camera_config.

    Args:
        flight_log_df: Full flight log DataFrame with 'filename' index.
        zone_files: List of bare filenames belonging to this zone.

    Returns:
        DataFrame for this zone with updated filename index.
    """
    if flight_log_df is None or flight_log_df.empty:
        return pd.DataFrame()

    # Build a lookup: bare filename -> original index value
    bare_to_original = {}
    for idx_val in flight_log_df.index:
        bare = os.path.basename(str(idx_val))
        bare_to_original[bare] = idx_val

    # Filter to zone files and rewrite filenames
    rows = []
    new_index = []
    for bare_filename in zone_files:
        original_key = bare_to_original.get(bare_filename)
        if original_key is None:
            continue

        try:
            row = flight_log_df.loc[original_key]
        except KeyError:
            continue

        subfolder = camera_subfolder_name(bare_filename)
        new_name = f"{subfolder}/{bare_filename}"

        if isinstance(row, pd.DataFrame):
            # Duplicate entries — take first
            row = row.iloc[0]

        rows.append(row)
        new_index.append(new_name)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows, index=new_index)
    result.index.name = 'filename'
    return result


def determine_camera_subfolder(filename: str) -> str:
    """Determine camera subfolder for a file using shared camera config.

    This replaces the duplicated logic that existed in batch_directory.py.
    """
    return camera_subfolder_name(filename)


def parse_flight_log_utm_suffix(flight_log_path: str) -> str:
    """Extract UTM zone suffix from flight log filename for use in output naming."""
    if not flight_log_path:
        return ""

    basename = os.path.basename(flight_log_path)
    if "_UTM" in basename:
        # Remove flight_log_ prefix and .txt suffix
        zone_part = basename.replace("flight_log_", "").replace(".txt", "")
        return f"_{zone_part}"
    return ""
