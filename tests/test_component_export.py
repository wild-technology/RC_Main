"""Tests for the ComponentExport module.

Unit tests that do not require a running RealityScan instance.
"""

import logging

import pytest

from module_base.parameter import Parameter
from modules.component_export.component_export import ComponentExportModule


@pytest.fixture
def logger():
    return logging.getLogger("test_component_export")


@pytest.fixture
def module(logger):
    return ComponentExportModule(logger=logger)


class TestGetParameters:
    """Verify that the module declares the expected parameters."""

    def test_get_parameters_returns_expected_keys(self, module):
        params = module.get_parameters()
        expected = {
            "export_enabled",
            "export_output_dir",
            "export_base_name",
            "export_max_component_num",
            "export_min_component_size",
        }
        assert set(params.keys()) == expected

    def test_export_enabled_defaults_true(self, module):
        params = module.get_parameters()
        assert params["export_enabled"].get_value() is True

    def test_export_max_component_num_default(self, module):
        params = module.get_parameters()
        assert params["export_max_component_num"].get_value() == 66

    def test_export_min_component_size_default(self, module):
        params = module.get_parameters()
        assert params["export_min_component_size"].get_value() == 100

    def test_all_parameters_have_descriptions(self, module):
        params = module.get_parameters()
        for name, param in params.items():
            assert param.get_description(), f"Parameter '{name}' missing description"

    def test_all_parameters_in_component_export_group(self, module):
        params = module.get_parameters()
        for name, param in params.items():
            assert param.get_parameter_group() == "ComponentExport", (
                f"Parameter '{name}' not in ComponentExport group"
            )


class TestSkipWhenDisabled:
    """Verify that the module skips cleanly when disabled."""

    def test_skip_when_disabled(self, module):
        """When export_enabled is False, run() returns Success+Skipped."""
        params = module.get_parameters()
        params["export_enabled"].set_value(False)
        module.set_params(params)

        result = module.run()

        assert result is not None
        assert result["status"] == "Success+Skipped"
        assert result["component_count"] == 0
        assert result["exported_files"] == []

    def test_skip_validation_when_disabled(self, module):
        """Validation passes even without output_dir when disabled."""
        params = module.get_parameters()
        params["export_enabled"].set_value(False)
        # Deliberately leave export_output_dir as None.
        module.set_params(params)

        valid, msg = module.validate_parameters()
        assert valid is True
        assert msg is None

    def test_validation_fails_without_output_dir_when_enabled(self, module):
        """Validation fails when enabled but no output_dir is set."""
        params = module.get_parameters()
        params["export_enabled"].set_value(True)
        # export_output_dir defaults to None.
        module.set_params(params)

        valid, msg = module.validate_parameters()
        assert valid is False
        assert "export_output_dir" in msg
