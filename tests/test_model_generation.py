"""Tests for the Model Generation module."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modules.model_generation.model_generation import ModelGeneration
from module_base.parameter import Parameter


@pytest.fixture
def logger():
    return logging.getLogger("test_model_generation")


@pytest.fixture
def module(logger):
    return ModelGeneration(logger)


class TestModelGenerationParameters:

    def test_get_parameters(self, module):
        params = module.get_parameters()
        assert "model_enabled" in params
        assert "model_alignment_dir" in params
        assert "model_export_dir" in params
        assert "model_project_prefix" in params
        assert "model_test_mode" in params
        assert "model_texture_params" in params

    def test_parameter_groups(self, module):
        params = module.get_parameters()
        for p in params.values():
            assert p.parameter_group == "Model Generation"

    def test_parameter_defaults(self, module):
        params = module.get_parameters()
        assert params["model_enabled"].default_value is True
        assert params["model_test_mode"].default_value is True
        assert params["model_project_prefix"].default_value == "model"

    def test_parameter_count(self, module):
        params = module.get_parameters()
        assert len(params) == 6  # 6 parameters after refactor


class TestModelGenerationValidation:

    def test_validate_disabled(self, module):
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, False),
        })
        ok, msg = module.validate_parameters()
        assert ok is True

    def test_validate_missing_alignment_dir(self, module):
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
        })
        ok, msg = module.validate_parameters()
        assert ok is False
        assert "alignment directory" in msg.lower()

    def test_validate_nonexistent_alignment_dir(self, module):
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
            "model_alignment_dir": Parameter("d", "d", "d", str, "/nonexistent/dir"),
        })
        ok, msg = module.validate_parameters()
        assert ok is False
        assert "not found" in msg.lower()

    def test_validate_missing_export_dir(self, module, tmp_path):
        align_dir = tmp_path / "alignments"
        align_dir.mkdir()
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
            "model_alignment_dir": Parameter("d", "d", "d", str, str(align_dir)),
        })
        ok, msg = module.validate_parameters()
        assert ok is False
        assert "export directory" in msg.lower()

    def test_validate_success(self, module, tmp_path):
        align_dir = tmp_path / "alignments"
        align_dir.mkdir()
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
            "model_alignment_dir": Parameter("d", "d", "d", str, str(align_dir)),
            "model_export_dir": Parameter("e", "e", "e", str, str(tmp_path / "exports")),
        })
        ok, msg = module.validate_parameters()
        assert ok is True


class TestModelGenerationRun:

    def test_skip_when_disabled(self, module):
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, False),
        })
        result = module.run()
        assert result["Success"] is True
        assert result["Skipped"] is True

    def test_no_rsalign_files(self, module, tmp_path):
        align_dir = tmp_path / "empty_align"
        align_dir.mkdir()
        export_dir = tmp_path / "exports"
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
            "model_alignment_dir": Parameter("d", "d", "d", str, str(align_dir)),
            "model_export_dir": Parameter("e", "e", "e", str, str(export_dir)),
            "model_project_prefix": Parameter("p", "p", "p", str, "test"),
            "model_test_mode": Parameter("t", "t", "t", bool, True),
        })
        result = module.run()
        assert result["Success"] is False
        assert "No .rsalign" in result["Error"]

    @patch("modules.model_generation.model_generation.RCDelegationClient")
    def test_process_component_with_mock(self, MockClient, module, tmp_path):
        """Test the full pipeline with mocked delegation client."""
        align_dir = tmp_path / "alignments"
        align_dir.mkdir()
        (align_dir / "comp_001.rsalign").write_text("mock alignment data")

        export_dir = tmp_path / "exports"

        # Mock the delegation client
        mock_client = MagicMock()
        mock_client.verify_connection.return_value = True
        mock_client.delegate.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mock_client.run_quick.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mock_client.wait_idle_two_phase.return_value = None
        mock_client.get_completed_items = MagicMock(return_value=[])
        MockClient.return_value = mock_client

        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
            "model_alignment_dir": Parameter("d", "d", "d", str, str(align_dir)),
            "model_export_dir": Parameter("e", "e", "e", str, str(export_dir)),
            "model_project_prefix": Parameter("p", "p", "p", str, "test"),
            "model_test_mode": Parameter("t", "t", "t", bool, True),
            "model_texture_params": Parameter("txml", "txml", "txml", str, None),
            "rc_executable_path": Parameter("rc", "rc", "rc", str, "/fake/RealityScan.exe"),
            "rc_instance_name": Parameter("ri", "ri", "ri", str, "*"),
            "rc_checkpoint_dir": Parameter("ckpt", "ckpt", "ckpt", str, str(tmp_path / "ckpt")),
        })

        result = module.run()
        assert result["Success"] is True
        assert result["ComponentsProcessed"] == 1
        assert result["Successful"] == 1
        assert result["Failed"] == 0

        # Verify delegation calls were made
        assert mock_client.delegate.call_count > 0 or mock_client.run_quick.call_count > 0


class TestModelGenerationHelpers:

    def test_get_param_with_value(self, module):
        module.set_params({
            "model_enabled": Parameter("e", "e", "e", bool, True),
        })
        assert module._get_param("model_enabled") is True

    def test_get_param_missing(self, module):
        module.set_params({})
        assert module._get_param("nonexistent", "fallback") == "fallback"

    def test_get_param_none_value(self, module):
        module.set_params({
            "model_simplify_params": Parameter("s", "s", "s", str, None),
        })
        assert module._get_param("model_simplify_params", "default") == "default"
