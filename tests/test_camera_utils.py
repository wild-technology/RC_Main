"""Tests for camera_utils: detection, profile loading, and grouping."""
import pytest

from modules.rc_common.camera_utils import (
    detect_camera_type,
    get_camera_groups,
    get_camera_profile,
    load_camera_profiles,
)


# ---------------------------------------------------------------------------
# detect_camera_type
# ---------------------------------------------------------------------------

def test_detect_camupper():
    assert detect_camera_type("camupper_20250705T034843Z.jpg") == "camupper"


def test_detect_cammid():
    assert detect_camera_type("cammid_20250705T034843Z.jpg") == "cammid"


def test_detect_camlower():
    assert detect_camera_type("camlower_20250705T034843Z.jpg") == "camlower"


def test_detect_zeuss_herc():
    assert detect_camera_type(
        "20250705T013705Z_0018_HERC_H.264_H2102_NA173_prob4_frame0.jpg"
    ) == "zeuss"


def test_detect_unknown():
    assert detect_camera_type("random_photo.jpg") == "unknown"


# ---------------------------------------------------------------------------
# load_camera_profiles
# ---------------------------------------------------------------------------

def test_load_camera_profiles():
    profiles = load_camera_profiles()
    assert "cameras" in profiles
    assert len(profiles["cameras"]) == 4


# ---------------------------------------------------------------------------
# get_camera_profile
# ---------------------------------------------------------------------------

def test_get_camera_profile_camupper():
    profiles = load_camera_profiles()
    profile = get_camera_profile("camupper_20250705T034843Z.jpg", profiles)
    assert profile is not None
    assert profile["focal_length_mm"] == 12
    assert profile["calibration_group"] == 3


def test_get_camera_profile_unknown():
    profiles = load_camera_profiles()
    profile = get_camera_profile("random_photo.jpg", profiles)
    assert profile is None


# ---------------------------------------------------------------------------
# get_camera_groups
# ---------------------------------------------------------------------------

def test_get_camera_groups():
    profiles = load_camera_profiles()
    groups = get_camera_groups(profiles)
    assert len(groups) == 4
    assert "CamLower" in groups
    assert "CamMid" in groups
    assert "CamUpper" in groups
    assert "Zeuss" in groups
