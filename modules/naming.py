"""
Centralized expedition and dive naming utilities.

Enforces the naming convention: NAXXX_HXXXX_UTM_{zone}{letter}
Used across georeferencing, batching, and standalone scripts.
"""
from __future__ import annotations
import re
import os


# Patterns for extracting expedition and dive identifiers
_EXPEDITION_RE = re.compile(r'(NA\d{3,})')
_DIVE_RE = re.compile(r'(H\d{4}[a-z]?)')
_DIVE_NUMBER_RE = re.compile(r'(NA\d+)_(H\d{4}[a-z]?)')


def parse_expedition_id(text: str) -> str | None:
    """Extract expedition ID (e.g., 'NA173') from a path or string."""
    m = _EXPEDITION_RE.search(text)
    return m.group(1) if m else None


def parse_dive_id(text: str) -> str | None:
    """Extract dive ID (e.g., 'H2102' or 'H2103b') from a path or string."""
    m = _DIVE_RE.search(text)
    return m.group(1) if m else None


def parse_dive_number_from_csv(csv_filename: str) -> tuple[str | None, str | None]:
    """Extract (expedition, dive) from a CSV filename.

    Handles variants like:
        NA173_H2103b_final_datatable.csv -> ('NA173', 'H2103b')
        H2102_datatable.csv             -> (None, 'H2102')

    Returns:
        (expedition_id, dive_id) — either may be None if not found.
    """
    basename = os.path.basename(csv_filename)
    m = _DIVE_NUMBER_RE.search(basename)
    if m:
        return m.group(1), m.group(2)
    # Fallback: try just dive ID
    dive = parse_dive_id(basename)
    expedition = parse_expedition_id(basename)
    return expedition, dive


def extract_dive_number(filename: str) -> str | None:
    """Extract combined dive identifier (e.g., 'NA173_H2103b') from a filename.

    This is a compatibility wrapper used by geoall.py's CSV discovery.
    """
    m = re.match(r'(NA\d+_[^_]+)', filename)
    return m.group(1) if m else None


def build_flight_log_name(
    expedition: str | None,
    dive: str | None,
    utm_zone: int | str | None,
    utm_letter: str | None,
) -> str:
    """Build a standardized flight log filename.

    Pattern: flight_log_{expedition}_{dive}_UTM_{zone}{letter}.txt

    Falls back gracefully when parts are missing:
        flight_log_NA173_H2102_UTM_4N.txt  (full)
        flight_log_H2102_UTM_4N.txt        (no expedition)
        flight_log_NA173_H2102_UTM_UNKNOWN.txt  (no UTM zone)
    """
    parts = ['flight_log']
    if expedition:
        parts.append(expedition)
    if dive:
        parts.append(dive)
    parts.append('UTM')

    zone_str = f"{utm_zone}{utm_letter}" if utm_zone and utm_letter else "UNKNOWN"
    parts.append(zone_str)

    return '_'.join(parts) + '.txt'


def build_output_dirname(
    expedition: str | None,
    dive: str | None,
    utm_zone: int | str | None,
    utm_letter: str | None,
) -> str:
    """Build a standardized output directory name.

    Pattern: NAXXX_HXXXX_UTM_{zone}{letter}
    Example: NA173_H2102_UTM_4N
    """
    parts = []
    if expedition:
        parts.append(expedition)
    if dive:
        parts.append(dive)
    parts.append('UTM')

    zone_str = f"{utm_zone}{utm_letter}" if utm_zone and utm_letter else "UNKNOWN"
    parts.append(zone_str)

    return '_'.join(parts)


def parse_utm_from_zone_string(zone_string: str) -> tuple[int | None, str | None]:
    """Parse a UTM zone string like '4N' or '18S' into (zone_number, zone_letter).

    Returns:
        (zone_number, zone_letter) or (None, None) if parsing fails.
    """
    m = re.match(r'(\d+)([A-Za-z])', zone_string)
    if m:
        return int(m.group(1)), m.group(2).upper()
    return None, None
