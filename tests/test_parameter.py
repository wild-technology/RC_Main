"""Tests for the enhanced Parameter class."""

import pytest

from module_base.parameter import Parameter


class TestParameterBasics:
    """Basic Parameter functionality (pre-existing)."""

    def test_create_parameter(self):
        p = Parameter("test", "-t", "--test", str, "default", description="A test param")
        assert p.name == "test"
        assert p.cli_short == "-t"
        assert p.cli_long == "--test"
        assert p.type is str
        assert p.value == "default"
        assert p.default_value == "default"

    def test_get_set_value(self):
        p = Parameter("test", "-t", "--test", str, "default")
        assert p.get_value() == "default"
        p.set_value("new_value")
        assert p.get_value() == "new_value"

    def test_default_parameter_group(self):
        p = Parameter("test", "-t", "--test", str, "default")
        assert p.get_parameter_group() == "General"


class TestParameterNewFields:
    """Tests for the new fields: parameter_group, min_value, max_value, choices."""

    def test_parameter_group(self):
        p = Parameter("test", "-t", "--test", str, "default", parameter_group="Alignment")
        assert p.parameter_group == "Alignment"
        assert p.get_parameter_group() == "Alignment"

    def test_min_max_value(self):
        p = Parameter("threshold", "-th", "--threshold", float, 2.0,
                       min_value=1.0, max_value=10.0)
        assert p.min_value == 1.0
        assert p.max_value == 10.0

    def test_choices(self):
        p = Parameter("mode", "-m", "--mode", str, "delegation",
                       choices=["delegation", "direct", "hybrid"])
        assert p.choices == ["delegation", "direct", "hybrid"]

    def test_file_filter(self):
        p = Parameter("xml_path", "-x", "--xml-path", str, None,
                       file_filter="*.xml")
        assert p.file_filter == "*.xml"


class TestParameterValidation:
    """Tests for the new validate() method."""

    def test_validate_none_value(self):
        p = Parameter("test", "-t", "--test", str, None)
        valid, msg = p.validate()
        assert valid is True

    def test_validate_choices_valid(self):
        p = Parameter("mode", "-m", "--mode", str, "delegation",
                       choices=["delegation", "direct"])
        valid, msg = p.validate()
        assert valid is True

    def test_validate_choices_invalid(self):
        p = Parameter("mode", "-m", "--mode", str, "hybrid",
                       choices=["delegation", "direct"])
        valid, msg = p.validate()
        assert valid is False
        assert "not in choices" in msg

    def test_validate_min_value_valid(self):
        p = Parameter("threshold", "-t", "--threshold", float, 5.0,
                       min_value=1.0, max_value=10.0)
        valid, msg = p.validate()
        assert valid is True

    def test_validate_min_value_invalid(self):
        p = Parameter("threshold", "-t", "--threshold", float, 0.5,
                       min_value=1.0, max_value=10.0)
        valid, msg = p.validate()
        assert valid is False
        assert "below minimum" in msg

    def test_validate_max_value_invalid(self):
        p = Parameter("threshold", "-t", "--threshold", float, 15.0,
                       min_value=1.0, max_value=10.0)
        valid, msg = p.validate()
        assert valid is False
        assert "above maximum" in msg

    def test_validate_int_range(self):
        p = Parameter("count", "-c", "--count", int, 50,
                       min_value=1, max_value=100)
        valid, msg = p.validate()
        assert valid is True

    def test_validate_int_out_of_range(self):
        p = Parameter("count", "-c", "--count", int, 200,
                       min_value=1, max_value=100)
        valid, msg = p.validate()
        assert valid is False


class TestParameterSerialization:
    """Tests for the new to_dict() method."""

    def test_to_dict(self):
        p = Parameter("threshold", "-t", "--threshold", float, 2.0,
                       parameter_group="Model")
        d = p.to_dict()
        assert d["name"] == "threshold"
        assert d["value"] == 2.0
        assert d["default_value"] == 2.0
        assert d["type"] == "float"
        assert d["parameter_group"] == "Model"

    def test_to_dict_none_type(self):
        p = Parameter("test", "-t", "--test", None, None)
        d = p.to_dict()
        assert d["type"] is None
