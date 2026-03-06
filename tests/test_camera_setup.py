"""Tests for the Camera Setup module."""
import logging
from unittest.mock import MagicMock, patch

import pytest

from modules.camera_setup.camera_setup import (
    DISTORTION_CODES,
    CameraSetup,
)
from module_base.parameter import Parameter


# ---------------------------------------------------------------------------
# test_get_parameters
# ---------------------------------------------------------------------------

def test_get_parameters():
    """Verify that the module exposes the expected parameter names."""
    module = CameraSetup(logger=logging.getLogger("test"))
    params = module.get_parameters()

    assert "cam_setup_enabled" in params
    assert "cam_profiles_path" in params
    assert isinstance(params["cam_setup_enabled"], Parameter)
    assert isinstance(params["cam_profiles_path"], Parameter)
    assert params["cam_setup_enabled"].get_value() is False
    assert params["cam_setup_enabled"].type is bool
    assert params["cam_profiles_path"].type is str


# ---------------------------------------------------------------------------
# test_skip_when_disabled
# ---------------------------------------------------------------------------

def test_skip_when_disabled():
    """Module returns Success + skipped when cam_setup_enabled is False."""
    module = CameraSetup(logger=logging.getLogger("test"))
    params = module.get_parameters()
    module.set_params(params)

    result = module.run()

    assert result is not None
    assert result["status"] == "Success"
    assert result["skipped"] is True


def test_skip_when_enabled_param_missing():
    """Module returns Success + skipped when params dict is empty."""
    module = CameraSetup(logger=logging.getLogger("test"))
    module.set_params({})

    result = module.run()

    assert result is not None
    assert result["status"] == "Success"
    assert result["skipped"] is True


# ---------------------------------------------------------------------------
# test_distortion_code_mapping
# ---------------------------------------------------------------------------

def test_distortion_code_mapping():
    """Verify the distortion model name-to-code mapping."""
    assert DISTORTION_CODES["Division"] == 1
    assert DISTORTION_CODES["Brown3"] == 2
    assert DISTORTION_CODES["Brown3WithTangential2"] == 3
    assert len(DISTORTION_CODES) == 3


def test_distortion_codes_no_unknown():
    """All camera profiles use a known distortion model."""
    from modules.rc_common.camera_utils import load_camera_profiles

    profiles = load_camera_profiles()
    for cam in profiles["cameras"]:
        model = cam["distortion_model"]
        assert model in DISTORTION_CODES, (
            f"Camera '{cam['name']}' uses unknown distortion model '{model}'"
        )
