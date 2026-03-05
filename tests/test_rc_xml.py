"""Tests for RealityScan XML parameter file utilities."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from modules.rc_common.rc_xml import (
    generate_rc_xml_string,
    merge_rc_xml,
    read_rc_xml,
    write_rc_xml,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestReadRcXml:
    """Tests for reading RC XML parameter files."""

    def test_read_valid_xml(self):
        params = read_rc_xml(FIXTURES_DIR / "sample_alignment_params.xml")
        assert isinstance(params, dict)
        assert "sfmFeatureDetectionQuality" in params
        assert params["sfmDistortionModel"] == "Brown3WithTangential2"
        assert params["sfmGPUAcceleration"] == "true"

    def test_read_all_entries(self):
        params = read_rc_xml(FIXTURES_DIR / "sample_alignment_params.xml")
        assert len(params) == 14

    def test_read_invalid_root(self, tmp_path):
        bad_xml = tmp_path / "bad.xml"
        bad_xml.write_text('<Settings><param name="x" value="1"/></Settings>')
        with pytest.raises(ValueError, match="expected 'Configuration'"):
            read_rc_xml(bad_xml)

    def test_read_nonexistent_file(self):
        with pytest.raises(FileNotFoundError):
            read_rc_xml("/nonexistent/file.xml")


class TestWriteRcXml:
    """Tests for writing RC XML parameter files."""

    def test_write_and_read_back(self, tmp_path):
        out = tmp_path / "test_params.xml"
        params = {"key1": "value1", "key2": "value2"}
        write_rc_xml(out, params)
        result = read_rc_xml(out)
        assert result == params

    def test_write_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "subdir" / "nested" / "params.xml"
        write_rc_xml(out, {"k": "v"})
        assert out.exists()

    def test_write_has_configuration_root(self, tmp_path):
        out = tmp_path / "params.xml"
        write_rc_xml(out, {"k": "v"})
        tree = ET.parse(out)
        assert tree.getroot().tag == "Configuration"

    def test_write_has_id_attribute(self, tmp_path):
        out = tmp_path / "params.xml"
        write_rc_xml(out, {"k": "v"})
        tree = ET.parse(out)
        root = tree.getroot()
        assert "id" in root.attrib
        assert root.attrib["id"].startswith("{")
        assert root.attrib["id"].endswith("}")

    def test_write_custom_id(self, tmp_path):
        out = tmp_path / "params.xml"
        custom_id = "{E377B69D-FB4B-4833-9CBE-FF747B7AF6D9}"
        write_rc_xml(out, {"k": "v"}, config_id=custom_id)
        tree = ET.parse(out)
        assert tree.getroot().attrib["id"] == custom_id

    def test_roundtrip_preserves_values(self, tmp_path):
        out = tmp_path / "params.xml"
        params = {
            "sfmFeatureDetectionQuality": "RealityScan.FeatureDetector.RSa1",
            "sfmMaxFeaturesPerImage": "0xc350",
            "sfmGPUAcceleration": "true",
            "sfmCameraPriorWeight": "10.0",
        }
        write_rc_xml(out, params)
        result = read_rc_xml(out)
        assert result == params


class TestGenerateRcXmlString:

    def test_contains_configuration(self):
        xml_str = generate_rc_xml_string({"k": "v"})
        assert "<Configuration" in xml_str
        assert "</Configuration>" in xml_str

    def test_contains_entries(self):
        xml_str = generate_rc_xml_string({"key1": "val1", "key2": "val2"})
        assert 'key="key1"' in xml_str
        assert 'value="val1"' in xml_str

    def test_parseable(self):
        xml_str = generate_rc_xml_string({"k": "v"})
        root = ET.fromstring(xml_str)
        assert root.tag == "Configuration"


class TestMergeRcXml:

    def test_merge_override(self):
        fixture = FIXTURES_DIR / "sample_alignment_params.xml"
        merged = merge_rc_xml(fixture, {"sfmGPUAcceleration": "false"})
        assert merged["sfmGPUAcceleration"] == "false"
        assert merged["sfmDistortionModel"] == "Brown3WithTangential2"

    def test_merge_add_new(self):
        fixture = FIXTURES_DIR / "sample_alignment_params.xml"
        merged = merge_rc_xml(fixture, {"newParam": "newValue"})
        assert merged["newParam"] == "newValue"
        assert "sfmFeatureDetectionQuality" in merged
