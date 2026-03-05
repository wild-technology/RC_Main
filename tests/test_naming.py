"""Tests for naming: filename generation and convention validation."""
from datetime import datetime

import pytest

from modules.rc_common.naming import generate_filename, validate_filename_convention


# ---------------------------------------------------------------------------
# generate_filename
# ---------------------------------------------------------------------------

def test_basic_filename():
    result = generate_filename("NA173", "H2102", "57L", extension=".rsalign")
    assert result == "NA173_H2102_57L.rsalign"


def test_with_zone():
    result = generate_filename("NA173", "H2102", "57L", zone_number=1, extension=".rsalign")
    assert "zone_001" in result


def test_with_timestamp():
    ts = datetime(2025, 7, 5, 3, 47)
    result = generate_filename("NA173", "H2102", "57L", timestamp=ts, extension=".rsalign")
    assert "20250705_0347" in result


def test_with_component():
    result = generate_filename("NA173", "H2102", "57L", component="comp01", extension=".fbx")
    assert "comp01" in result


def test_with_suffix():
    result = generate_filename("NA173", "H2102", "57L", suffix="HighPoly", extension=".fbx")
    assert "HighPoly" in result


def test_full_filename():
    ts = datetime(2025, 7, 5, 3, 47)
    result = generate_filename(
        "NA173",
        "H2102",
        "57L",
        zone_number=1,
        component="comp01",
        suffix="HighPoly",
        timestamp=ts,
        extension=".fbx",
    )
    assert result == "NA173_H2102_57L_zone_001_comp01_HighPoly_20250705_0347.fbx"


# ---------------------------------------------------------------------------
# validate_filename_convention
# ---------------------------------------------------------------------------

def test_validate_valid():
    valid, reason = validate_filename_convention("NA173_H2102_57L_zone_001.rsalign")
    assert valid is True
    assert reason is None


def test_validate_invalid_too_few_parts():
    valid, reason = validate_filename_convention("file.txt")
    assert valid is False
    assert reason is not None


def test_validate_invalid_utm():
    valid, reason = validate_filename_convention("NA173_H2102_ABC.txt")
    assert valid is False
    assert reason is not None
