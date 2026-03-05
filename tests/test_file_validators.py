"""Unit tests for modules.rc_common.file_validators."""

from __future__ import annotations

from pathlib import Path

import pytest

from modules.rc_common.file_validators import (
    validate_flight_log,
    validate_output_path,
    validate_rc_xml,
    validate_rov_csv,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# validate_flight_log
# ---------------------------------------------------------------------------


class TestValidateFlightLog:
    def test_valid_flight_log(self):
        path = FIXTURES / "flight_log_NA173_H2102_57L_UTM.txt"
        ok, err = validate_flight_log(path)
        assert ok is True
        assert err is None

    def test_flight_log_not_found(self):
        ok, err = validate_flight_log(FIXTURES / "nonexistent_file.txt")
        assert ok is False
        assert "not found" in err

    def test_flight_log_wrong_delimiter(self):
        path = FIXTURES / "bad_flight_log_comma_delimited.txt"
        ok, err = validate_flight_log(path)
        assert ok is False
        assert err is not None

    def test_flight_log_missing_columns(self):
        path = FIXTURES / "bad_flight_log_missing_columns.txt"
        ok, err = validate_flight_log(path)
        assert ok is False
        assert err is not None


# ---------------------------------------------------------------------------
# validate_rov_csv
# ---------------------------------------------------------------------------


class TestValidateRovCsv:
    def test_valid_rov_csv(self):
        path = FIXTURES / "NA173_H2102_final_datatable_sample.csv"
        ok, err = validate_rov_csv(path)
        assert ok is True
        assert err is None

    def test_rov_csv_not_found(self):
        ok, err = validate_rov_csv(FIXTURES / "nonexistent_rov.csv")
        assert ok is False
        assert "not found" in err

    def test_rov_csv_missing_kalman(self):
        path = FIXTURES / "bad_rov_csv_missing_kalman.csv"
        ok, err = validate_rov_csv(path)
        assert ok is False
        assert "missing required columns" in err.lower()


# ---------------------------------------------------------------------------
# validate_rc_xml
# ---------------------------------------------------------------------------


class TestValidateRcXml:
    def test_valid_rc_xml(self):
        path = FIXTURES / "sample_alignment_params.xml"
        ok, err = validate_rc_xml(path)
        assert ok is True
        assert err is None

    def test_rc_xml_not_found(self):
        ok, err = validate_rc_xml(FIXTURES / "nonexistent.xml")
        assert ok is False
        assert "not found" in err

    def test_rc_xml_wrong_format(self):
        path = FIXTURES / "bad_xml_wrong_format.xml"
        ok, err = validate_rc_xml(path)
        assert ok is False
        assert err is not None


# ---------------------------------------------------------------------------
# validate_output_path
# ---------------------------------------------------------------------------


class TestValidateOutputPath:
    def test_valid_output_path(self, tmp_path: Path):
        output_file = tmp_path / "output.txt"
        ok, err = validate_output_path(output_file)
        assert ok is True
        assert err is None

    def test_output_path_nonexistent_parent(self):
        bad_path = Path("/no/such/parent/dir/output.txt")
        ok, err = validate_output_path(bad_path)
        assert ok is False
        assert "does not exist" in err
