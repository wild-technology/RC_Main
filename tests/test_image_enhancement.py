"""Tests for the Image Enhancement module."""

import logging
from pathlib import Path

import cv2
import numpy as np
import pytest

from modules.image_enhancement.image_enhancement import ImageEnhancement
from module_base.parameter import Parameter


@pytest.fixture
def logger():
    return logging.getLogger("test_image_enhancement")


@pytest.fixture
def module(logger):
    return ImageEnhancement(logger)


@pytest.fixture
def sample_image(tmp_path):
    """Create a small test image."""
    img = np.random.randint(50, 200, (64, 64, 3), dtype=np.uint8)
    path = tmp_path / "input" / "camupper_20250705T034843Z.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)
    return path


@pytest.fixture
def sample_text_file(tmp_path):
    """Create a sample text file alongside images."""
    path = tmp_path / "input" / "metadata.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("sample metadata")
    return path


class TestImageEnhancementParameters:

    def test_get_parameters(self, module):
        params = module.get_parameters()
        assert "enhance_enabled" in params
        assert "enhance_clip_limit_fine" in params
        assert "enhance_clip_limit_coarse" in params
        assert "enhance_contrast_alpha" in params
        assert "enhance_jpeg_quality" in params

    def test_parameter_groups(self, module):
        params = module.get_parameters()
        for p in params.values():
            assert p.parameter_group == "Enhancement"

    def test_parameter_defaults(self, module):
        params = module.get_parameters()
        assert params["enhance_enabled"].default_value is False
        assert params["enhance_clip_limit_fine"].default_value == 1.5
        assert params["enhance_clip_limit_coarse"].default_value == 1.2
        assert params["enhance_contrast_alpha"].default_value == 1.15
        assert params["enhance_jpeg_quality"].default_value == 90


class TestImageEnhancementValidation:

    def test_validate_disabled(self, module):
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, False),
        })
        ok, msg = module.validate_parameters()
        assert ok is True

    def test_validate_missing_input_dir(self, module):
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, True),
        })
        ok, msg = module.validate_parameters()
        assert ok is False
        assert "required" in msg.lower()

    def test_validate_nonexistent_dir(self, module):
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, True),
            "enhance_input_dir": Parameter("d", "d", "d", str, "/nonexistent/dir"),
        })
        ok, msg = module.validate_parameters()
        assert ok is False
        assert "does not exist" in msg


class TestImageEnhancementRun:

    def test_skip_when_disabled(self, module):
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, False),
        })
        result = module.run()
        assert result["Success"] is True
        assert result["Skipped"] is True

    def test_process_image(self, module, sample_image, sample_text_file, tmp_path):
        input_dir = tmp_path / "input"
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, True),
            "enhance_input_dir": Parameter("d", "d", "d", str, str(input_dir)),
            "enhance_clip_limit_fine": Parameter("cf", "cf", "cf", float, 1.5),
            "enhance_clip_limit_coarse": Parameter("cc", "cc", "cc", float, 1.2),
            "enhance_contrast_alpha": Parameter("ca", "ca", "ca", float, 1.15),
            "enhance_jpeg_quality": Parameter("q", "q", "q", int, 90),
        })
        result = module.run()
        assert result["Success"] is True
        assert result["ImagesProcessed"] == 1
        assert result["TextFilesCopied"] == 1

        # Check output exists
        output_dir = input_dir.parent / "input_enhanced"
        assert output_dir.exists()
        enhanced_img = output_dir / "camupper_20250705T034843Z.jpg"
        assert enhanced_img.exists()
        copied_txt = output_dir / "metadata.txt"
        assert copied_txt.exists()

    def test_no_files(self, module, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        module.set_params({
            "enhance_enabled": Parameter("e", "e", "e", bool, True),
            "enhance_input_dir": Parameter("d", "d", "d", str, str(empty_dir)),
            "enhance_clip_limit_fine": Parameter("cf", "cf", "cf", float, 1.5),
            "enhance_clip_limit_coarse": Parameter("cc", "cc", "cc", float, 1.2),
            "enhance_contrast_alpha": Parameter("ca", "ca", "ca", float, 1.15),
            "enhance_jpeg_quality": Parameter("q", "q", "q", int, 90),
        })
        result = module.run()
        assert result["Success"] is True
        assert result["ImagesProcessed"] == 0


class TestEnhancePipeline:

    def test_enhance_image_static(self):
        """Test the static enhancement pipeline directly."""
        img = np.random.randint(30, 220, (64, 64, 3), dtype=np.uint8)
        enhanced = ImageEnhancement._enhance_image(img, 1.5, 1.2, 1.15)
        assert enhanced.shape == img.shape
        assert enhanced.dtype == np.uint8

    def test_enhance_preserves_shape(self):
        img = np.zeros((100, 80, 3), dtype=np.uint8) + 128
        enhanced = ImageEnhancement._enhance_image(img, 1.5, 1.2, 1.0)
        assert enhanced.shape == (100, 80, 3)
