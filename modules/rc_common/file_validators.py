"""Input validation utilities for all file formats used in the RC_Main pipeline.

Provides early-and-often validation so modules can fail fast with clear error
messages rather than crashing mid-processing.
"""

from __future__ import annotations

import logging
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

FLIGHT_LOG_HEADERS = [
    "filename",
    "X (East)",
    "Y (North)",
    "Alt",
    "X Accuracy",
    "Y Accuracy",
    "Alt Accuracy",
    "Yaw",
    "Pitch",
    "Roll",
    "Yaw Accuracy",
    "Pitch Accuracy",
    "Roll Accuracy",
]

ROV_CSV_REQUIRED_COLUMNS = [
    "Timestamp",
    "kalman_lat",
    "kalman_long",
    "kalman_depth",
    "kalman_yaw_deg",
    "kalman_pitch_deg",
    "kalman_roll_deg",
]


def validate_flight_log(path: str | Path) -> tuple[bool, str | None]:
    """Validate a semicolon-delimited flight log file.

    Checks that the file exists, is readable, uses semicolon delimiters,
    contains the 13 required column headers, and has at least one data row.

    Args:
        path: Path to the flight log file.

    Returns:
        (True, None) on success, or (False, error_description) on failure.
    """
    try:
        path = Path(path)

        if not path.exists():
            return False, f"Flight log not found: {path}"

        if not path.is_file():
            return False, f"Flight log path is not a file: {path}"

        if not os.access(path, os.R_OK):
            return False, f"Flight log is not readable: {path}"

        lines = path.read_text(encoding="utf-8").splitlines()

        if not lines:
            return False, f"Flight log is empty: {path}"

        # Validate header row
        header_fields = [h.strip() for h in lines[0].split(";")]

        if len(header_fields) != 13:
            return False, (
                f"Flight log header has {len(header_fields)} fields "
                f"(expected 13 semicolon-delimited): {path}"
            )

        for required in FLIGHT_LOG_HEADERS:
            if required not in header_fields:
                return False, (
                    f"Flight log missing required header '{required}': {path}"
                )

        # Check at least 1 data row exists
        data_lines = [l for l in lines[1:] if l.strip()]
        if not data_lines:
            return False, f"Flight log has no data rows: {path}"

        # Validate first data row has correct field count
        first_row_fields = data_lines[0].split(";")
        if len(first_row_fields) != 13:
            return False, (
                f"Flight log first data row has {len(first_row_fields)} fields "
                f"(expected 13): {path}"
            )

        logger.info("Flight log validated: %s (%d data rows)", path, len(data_lines))
        return True, None

    except Exception as e:
        return False, f"Flight log validation error for {path}: {e}"


def validate_rov_csv(path: str | Path) -> tuple[bool, str | None]:
    """Validate a tab-delimited ROV data CSV file.

    Checks that the file exists, uses tab delimiters, contains the required
    columns, and has at least one data row.

    Args:
        path: Path to the ROV CSV file.

    Returns:
        (True, None) on success, or (False, error_description) on failure.
    """
    try:
        path = Path(path)

        if not path.exists():
            return False, f"ROV CSV not found: {path}"

        if not path.is_file():
            return False, f"ROV CSV path is not a file: {path}"

        if not os.access(path, os.R_OK):
            return False, f"ROV CSV is not readable: {path}"

        lines = path.read_text(encoding="utf-8").splitlines()

        if not lines:
            return False, f"ROV CSV is empty: {path}"

        # Validate header uses tab delimiter
        header_fields = [h.strip() for h in lines[0].split("\t")]

        if len(header_fields) < 2:
            return False, (
                f"ROV CSV header does not appear to be tab-delimited "
                f"(only {len(header_fields)} field(s) found): {path}"
            )

        missing = [
            col for col in ROV_CSV_REQUIRED_COLUMNS if col not in header_fields
        ]
        if missing:
            return False, (
                f"ROV CSV missing required columns: {', '.join(missing)}: {path}"
            )

        # Check at least 1 data row
        data_lines = [l for l in lines[1:] if l.strip()]
        if not data_lines:
            return False, f"ROV CSV has no data rows: {path}"

        logger.info("ROV CSV validated: %s (%d data rows)", path, len(data_lines))
        return True, None

    except Exception as e:
        return False, f"ROV CSV validation error for {path}: {e}"


def validate_rc_xml(path: str | Path) -> tuple[bool, str | None]:
    """Validate a RealityScan XML parameter file.

    Checks that the file exists, parses as valid XML, has a ``Configuration``
    root element with an ``id`` attribute, and all children are ``entry``
    elements with both ``key`` and ``value`` attributes.

    Args:
        path: Path to the XML parameter file.

    Returns:
        (True, None) on success, or (False, error_description) on failure.
    """
    try:
        path = Path(path)

        if not path.exists():
            return False, f"RC XML not found: {path}"

        if not path.is_file():
            return False, f"RC XML path is not a file: {path}"

        tree = ET.parse(path)
        root = tree.getroot()

        if root.tag != "Configuration":
            return False, (
                f"RC XML root element is '{root.tag}', expected 'Configuration': {path}"
            )

        if "id" not in root.attrib:
            return False, f"RC XML Configuration element missing 'id' attribute: {path}"

        for i, child in enumerate(root):
            if child.tag != "entry":
                return False, (
                    f"RC XML child element {i} is '{child.tag}', "
                    f"expected 'entry': {path}"
                )

            if "key" not in child.attrib:
                return False, (
                    f"RC XML entry element {i} missing 'key' attribute: {path}"
                )

            if "value" not in child.attrib:
                return False, (
                    f"RC XML entry element {i} missing 'value' attribute: {path}"
                )

        logger.info("RC XML validated: %s", path)
        return True, None

    except ET.ParseError as e:
        return False, f"RC XML is not valid XML: {e}: {path}"
    except Exception as e:
        return False, f"RC XML validation error for {path}: {e}"


def validate_image(path: str | Path) -> tuple[bool, str | None]:
    """Validate that a file is a readable image.

    Uses PIL to open and verify the image. This catches truncated files,
    corrupt headers, and unsupported formats.

    Args:
        path: Path to the image file.

    Returns:
        (True, None) on success, or (False, error_description) on failure.
    """
    try:
        path = Path(path)

        if not path.exists():
            return False, f"Image not found: {path}"

        if not path.is_file():
            return False, f"Image path is not a file: {path}"

        from PIL import Image

        with Image.open(path) as img:
            img.verify()

        logger.info("Image validated: %s", path)
        return True, None

    except ImportError:
        return False, "PIL/Pillow is not installed — cannot validate image"
    except Exception as e:
        return False, f"Image validation error for {path}: {e}"


def validate_output_path(
    path: str | Path, min_free_mb: int = 100
) -> tuple[bool, str | None]:
    """Validate that an output path is usable.

    Checks that the parent directory exists and is writable, and that the
    volume has at least ``min_free_mb`` megabytes of free space.

    Args:
        path: The intended output file or directory path.
        min_free_mb: Minimum required free disk space in megabytes.

    Returns:
        (True, None) on success, or (False, error_description) on failure.
    """
    try:
        path = Path(path)
        parent = path.parent

        if not parent.exists():
            return False, f"Output parent directory does not exist: {parent}"

        if not parent.is_dir():
            return False, f"Output parent path is not a directory: {parent}"

        if not os.access(parent, os.W_OK):
            return False, f"Output parent directory is not writable: {parent}"

        usage = shutil.disk_usage(parent)
        free_mb = usage.free / (1024 * 1024)

        if free_mb < min_free_mb:
            return False, (
                f"Insufficient disk space: {free_mb:.0f} MB free, "
                f"need at least {min_free_mb} MB on {parent}"
            )

        logger.info(
            "Output path validated: %s (%.0f MB free)", path, free_mb
        )
        return True, None

    except Exception as e:
        return False, f"Output path validation error for {path}: {e}"
