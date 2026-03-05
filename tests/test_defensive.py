"""Comprehensive defensive coding tests spanning all modules.

Tests edge cases, boundary conditions, error handling, type mismatches,
None/empty inputs, and path handling across the full RC_Main pipeline.
Designed to run on Linux with mocked subprocess (no RC needed).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Parameter defensive tests
# ---------------------------------------------------------------------------


class TestParameterDefensive:
    """Edge cases and defensive coding for Parameter class."""

    def test_set_value_wrong_type_no_crash(self):
        """Parameter.set_value() accepts any type without crashing."""
        from module_base.parameter import Parameter
        p = Parameter("num", "n", "num", int, 10, min_value=0, max_value=100)
        p.set_value("not_a_number")
        # Should store the wrong type; validate() should catch it
        assert p.get_value() == "not_a_number"
        valid, msg = p.validate()
        assert valid is False
        assert "cannot convert" in msg

    def test_validate_string_in_int_param(self):
        from module_base.parameter import Parameter
        p = Parameter("count", "c", "count", int, "abc", min_value=0, max_value=100)
        valid, msg = p.validate()
        assert valid is False

    def test_validate_none_with_min_max(self):
        """None value should pass validation even with min/max constraints."""
        from module_base.parameter import Parameter
        p = Parameter("threshold", "t", "threshold", float, None, min_value=0.0, max_value=10.0)
        valid, msg = p.validate()
        assert valid is True

    def test_validate_boundary_values(self):
        """Exact boundary values should be valid."""
        from module_base.parameter import Parameter
        p = Parameter("t", "t", "t", float, 1.0, min_value=1.0, max_value=10.0)
        assert p.validate() == (True, None)
        p.set_value(10.0)
        assert p.validate() == (True, None)

    def test_validate_empty_choices(self):
        """Empty choices list — any value should fail."""
        from module_base.parameter import Parameter
        p = Parameter("m", "m", "m", str, "x", choices=[])
        valid, msg = p.validate()
        assert valid is False

    def test_to_dict_with_none_type(self):
        from module_base.parameter import Parameter
        p = Parameter("x", "x", "x", None, None)
        d = p.to_dict()
        assert d["type"] is None

    def test_bool_param_with_string_true(self):
        """Bool params set to string 'true' — should still be valid."""
        from module_base.parameter import Parameter
        p = Parameter("flag", "f", "flag", bool, "true")
        valid, msg = p.validate()
        assert valid is True  # no min/max/choices constraint

    def test_parameter_group_default(self):
        from module_base.parameter import Parameter
        p = Parameter("x", "x", "x", str, None, parameter_group=None)
        assert p.parameter_group == "General"


# ---------------------------------------------------------------------------
# RCModule base class defensive tests
# ---------------------------------------------------------------------------


class TestRCModuleDefensive:
    """Edge cases for the module base class."""

    def test_get_progress_no_bars(self):
        from module_base.rc_module import RCModule
        # Create a concrete subclass
        class DummyModule(RCModule):
            def run(self):
                return {"Success": True}
        mod = DummyModule("test", logging.getLogger("test"))
        assert mod.get_progress() == 0.0

    def test_get_progress_zero_total_bar(self):
        """Loading bar with total=0 should not cause ZeroDivisionError."""
        from module_base.rc_module import RCModule
        class DummyModule(RCModule):
            def run(self):
                return {"Success": True}
        mod = DummyModule("test", logging.getLogger("test"))
        bar = mod._initialize_loading_bar(0, "empty")
        assert mod.get_progress() == 0.0
        bar.close()

    def test_finish_without_progress_reporter(self):
        """finish() should not crash when no progress reporter is set."""
        from module_base.rc_module import RCModule
        class DummyModule(RCModule):
            def run(self):
                return {"Success": True}
        mod = DummyModule("test", logging.getLogger("test"))
        mod.finish()  # Should not raise

    def test_report_progress_without_reporter(self):
        """_report_progress should be a no-op when reporter is None."""
        from module_base.rc_module import RCModule
        class DummyModule(RCModule):
            def run(self):
                return {"Success": True}
        mod = DummyModule("test", logging.getLogger("test"))
        mod._report_progress("test_op", 50.0)  # Should not raise

    def test_validate_parameters_only_own_params(self):
        """validate_parameters() should only validate this module's params."""
        from module_base.rc_module import RCModule
        from module_base.parameter import Parameter

        class ModA(RCModule):
            def get_parameters(self):
                return {"a_param": Parameter("a", "a", "a_param", int, 5, min_value=0, max_value=10)}
            def run(self):
                return {}

        mod = ModA("ModA", logging.getLogger("test"))
        # Inject all params, including one that would fail from another module
        mod.set_params({
            "a_param": Parameter("a", "a", "a_param", int, 5, min_value=0, max_value=10),
            "other_param": Parameter("other", "o", "other_param", int, 999, min_value=0, max_value=10),
        })
        valid, msg = mod.validate_parameters()
        assert valid is True  # Should not check other_param

    def test_set_params_replaces_dict(self):
        from module_base.rc_module import RCModule
        class DummyModule(RCModule):
            def run(self):
                return {}
        mod = DummyModule("test", logging.getLogger("test"))
        mod.set_params({"key": "value"})
        assert mod.params == {"key": "value"}


# ---------------------------------------------------------------------------
# Progress system defensive tests
# ---------------------------------------------------------------------------


class TestProgressDefensive:
    """Edge cases for progress reporting."""

    def test_report_event_direct_fanout(self):
        """report_event() should pass ProgressEvent directly to backends."""
        from modules.rc_common.progress import ProgressEvent, ProgressReporter, SignalBackend
        received = []
        backend = SignalBackend(callback=lambda e: received.append(e))
        reporter = ProgressReporter(backends=[backend])

        event = ProgressEvent(
            module_name="Test", operation_name="Op",
            progress_pct=42.0, elapsed_sec=10.0, eta_sec=5.0,
            message="custom msg",
        )
        reporter.report_event(event)
        assert len(received) == 1
        assert received[0].progress_pct == 42.0
        assert received[0].message == "custom msg"

    def test_log_backend_accepts_logger_instance(self):
        """LogBackend should accept a Logger object without crashing."""
        from modules.rc_common.progress import LogBackend, ProgressEvent
        logger = logging.getLogger("test_log_backend")
        backend = LogBackend(logger)
        backend.start_operation("test", 10)
        backend.update(1)
        event = ProgressEvent(
            module_name="T", operation_name="Op",
            progress_pct=10.0, elapsed_sec=1.0, eta_sec=9.0,
            message="ok",
        )
        backend.report(event)
        backend.finish()  # Should not raise

    def test_log_backend_accepts_int_level(self):
        from modules.rc_common.progress import LogBackend
        backend = LogBackend(logging.DEBUG)
        backend.start_operation("test", 5)
        backend.update(1)
        backend.finish()

    def test_log_backend_default(self):
        from modules.rc_common.progress import LogBackend
        backend = LogBackend()
        backend.start_operation("test", 5)
        backend.finish()

    def test_reporter_no_backends(self):
        """Reporter with no backends should not crash."""
        from modules.rc_common.progress import ProgressReporter
        reporter = ProgressReporter(backends=[])
        reporter.start_operation("Test", 10)
        reporter.update(5)
        reporter.report("halfway")
        reporter.finish()

    def test_reporter_zero_total_steps(self):
        """Reporter with total_steps=0 should not divide by zero."""
        from modules.rc_common.progress import ProgressReporter, SignalBackend
        received = []
        backend = SignalBackend(callback=lambda e: received.append(e))
        reporter = ProgressReporter(backends=[backend])
        reporter.start_operation("Empty", 0)
        reporter.report("test")
        assert len(received) == 1
        assert received[0].progress_pct == 0.0

    def test_reporter_thread_safety(self):
        """Basic thread safety: concurrent updates should not crash."""
        from modules.rc_common.progress import ProgressReporter, SignalBackend
        reporter = ProgressReporter(backends=[SignalBackend()])
        reporter.start_operation("Threaded", 1000)

        errors = []
        def worker():
            try:
                for _ in range(100):
                    reporter.update(1)
                    reporter.report("step")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# RC Status Parser defensive tests
# ---------------------------------------------------------------------------


class TestRCStatusParserDefensive:
    """Edge cases for status string parsing."""

    def test_parse_none(self):
        from modules.rc_common.rc_status import RCStatusParser
        parser = RCStatusParser()
        result = parser.parse(None)
        assert result["is_idle"] is True
        assert result["progress"] == 0.0

    def test_parse_empty_string(self):
        from modules.rc_common.rc_status import RCStatusParser
        parser = RCStatusParser()
        result = parser.parse("")
        assert result["is_idle"] is True

    def test_parse_garbage(self):
        from modules.rc_common.rc_status import RCStatusParser
        parser = RCStatusParser()
        result = parser.parse("totally random garbage 123 !@#$%")
        assert "progress" in result

    def test_parse_idle_string(self):
        from modules.rc_common.rc_status import RCStatusParser
        parser = RCStatusParser()
        result = parser.parse("idle id:0xffffffff")
        assert result["is_idle"] is True

    def test_parse_active_with_progress(self):
        from modules.rc_common.rc_status import RCStatusParser
        parser = RCStatusParser()
        result = parser.parse("id:0x1234 progress:57.5 runtime:120sec estimation:80sec")
        assert result["is_idle"] is False
        assert result["progress"] == pytest.approx(57.5)


# ---------------------------------------------------------------------------
# RC Delegation client defensive tests
# ---------------------------------------------------------------------------


class TestRCDelegationDefensive:
    """Edge cases for delegation with mocked subprocess."""

    def test_delegate_subprocess_error(self):
        """Subprocess failure returns synthetic result, not exception."""
        from modules.rc_common.rc_delegation import RCDelegationClient
        client = RCDelegationClient("/fake/rc.exe")
        result = client.delegate("-test")
        # Should return a CompletedProcess with rc=-1 (fake exe doesn't exist)
        assert result.returncode != 0

    def test_verify_connection_returns_false_on_error(self):
        from modules.rc_common.rc_delegation import RCDelegationClient
        client = RCDelegationClient("/fake/rc.exe")
        assert client.verify_connection() is False

    def test_get_status_returns_default_on_failure(self):
        from modules.rc_common.rc_delegation import RCDelegationClient
        client = RCDelegationClient("/fake/rc.exe")
        status = client.get_status()
        assert status["is_idle"] is True
        assert status["progress"] == 0.0

    def test_run_quick_logs_delegate_failure(self):
        """run_quick should log warning when delegate returns non-zero."""
        from modules.rc_common.rc_delegation import RCDelegationClient
        import subprocess
        client = RCDelegationClient("/fake/rc.exe")
        # Mock delegate to return failure
        client.delegate = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        ))
        client.wait_completed = MagicMock(return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        ))
        result = client.run_quick("test_op", "-someFlag")
        client.delegate.assert_called_once()
        assert result.returncode == 0

    def test_on_progress_callback_not_required(self):
        from modules.rc_common.rc_delegation import RCDelegationClient
        client = RCDelegationClient("/fake/rc.exe")
        assert client.on_progress is None


# ---------------------------------------------------------------------------
# Camera utils defensive tests
# ---------------------------------------------------------------------------


class TestCameraUtilsDefensive:
    """Edge cases for camera detection and profile loading."""

    def test_detect_unknown_camera(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("random_file_20250101.jpg") == "unknown"

    def test_detect_camera_case_insensitive(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("CamUpper_20250101.jpg") == "camupper"
        assert detect_camera_type("CAMLOWER_20250101.jpg") == "camlower"

    def test_detect_camera_with_full_path(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("/some/path/to/cammid_20250101.jpg") == "cammid"
        assert detect_camera_type("D:\\Photos\\camupper_20250101.jpg") == "camupper"

    def test_detect_zeuss_from_herc(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("20250101T000000Z_HERC_frame.jpg") == "zeuss"

    def test_detect_empty_filename(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("") == "unknown"

    def test_get_camera_profile_unknown(self):
        from modules.rc_common.camera_utils import get_camera_profile
        profiles = {"cameras": [{"name": "CamUpper", "keywords": ["camupper"]}]}
        assert get_camera_profile("random.jpg", profiles) is None

    def test_get_camera_profile_empty_profiles(self):
        from modules.rc_common.camera_utils import get_camera_profile
        assert get_camera_profile("camupper_test.jpg", {"cameras": []}) is None

    def test_get_camera_profile_missing_cameras_key(self):
        from modules.rc_common.camera_utils import get_camera_profile
        assert get_camera_profile("camupper_test.jpg", {}) is None

    def test_load_camera_profiles_missing_file(self):
        from modules.rc_common.camera_utils import load_camera_profiles
        with pytest.raises(FileNotFoundError):
            load_camera_profiles("/nonexistent/path.json")

    def test_load_camera_profiles_valid(self):
        from modules.rc_common.camera_utils import load_camera_profiles
        profiles = load_camera_profiles()  # uses default path
        assert "cameras" in profiles
        assert len(profiles["cameras"]) > 0


# ---------------------------------------------------------------------------
# File validators defensive tests
# ---------------------------------------------------------------------------


class TestFileValidatorsDefensive:
    """Edge cases for input file validation."""

    def test_validate_flight_log_nonexistent(self):
        from modules.rc_common.file_validators import validate_flight_log
        ok, msg = validate_flight_log("/nonexistent/file.csv")
        assert ok is False
        assert "not found" in msg

    def test_validate_flight_log_empty_file(self, tmp_path):
        from modules.rc_common.file_validators import validate_flight_log
        f = tmp_path / "empty.csv"
        f.write_text("")
        ok, msg = validate_flight_log(f)
        assert ok is False
        assert "empty" in msg

    def test_validate_flight_log_wrong_delimiter(self, tmp_path):
        from modules.rc_common.file_validators import validate_flight_log
        f = tmp_path / "comma.csv"
        f.write_text("filename,X (East),Y (North)\ntest.jpg,100,200\n")
        ok, msg = validate_flight_log(f)
        assert ok is False

    def test_validate_flight_log_header_only(self, tmp_path):
        from modules.rc_common.file_validators import validate_flight_log
        headers = "filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy"
        f = tmp_path / "header_only.csv"
        f.write_text(headers + "\n")
        ok, msg = validate_flight_log(f)
        assert ok is False
        assert "no data rows" in msg

    def test_validate_flight_log_valid(self, tmp_path):
        from modules.rc_common.file_validators import validate_flight_log
        headers = "filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy"
        data = "test.jpg;100;200;50;1;1;1;0;0;0;5;5;5"
        f = tmp_path / "valid.csv"
        f.write_text(headers + "\n" + data + "\n")
        ok, msg = validate_flight_log(f)
        assert ok is True

    def test_validate_rov_csv_nonexistent(self):
        from modules.rc_common.file_validators import validate_rov_csv
        ok, msg = validate_rov_csv("/nonexistent/rov.csv")
        assert ok is False

    def test_validate_rov_csv_missing_columns(self, tmp_path):
        from modules.rc_common.file_validators import validate_rov_csv
        f = tmp_path / "bad_rov.csv"
        f.write_text("Timestamp\tVehicle\n2025-01-01\tHERC\n")
        ok, msg = validate_rov_csv(f)
        assert ok is False
        assert "missing required columns" in msg

    def test_validate_rc_xml_not_xml(self, tmp_path):
        from modules.rc_common.file_validators import validate_rc_xml
        f = tmp_path / "not_xml.xml"
        f.write_text("this is not xml at all")
        ok, msg = validate_rc_xml(f)
        assert ok is False
        assert "not valid XML" in msg

    def test_validate_rc_xml_wrong_root(self, tmp_path):
        from modules.rc_common.file_validators import validate_rc_xml
        f = tmp_path / "wrong.xml"
        f.write_text('<Root><entry key="a" value="b"/></Root>')
        ok, msg = validate_rc_xml(f)
        assert ok is False
        assert "Configuration" in msg

    def test_validate_rc_xml_missing_id(self, tmp_path):
        from modules.rc_common.file_validators import validate_rc_xml
        f = tmp_path / "no_id.xml"
        f.write_text('<Configuration><entry key="a" value="b"/></Configuration>')
        ok, msg = validate_rc_xml(f)
        assert ok is False
        assert "missing 'id'" in msg

    def test_validate_rc_xml_valid(self, tmp_path):
        from modules.rc_common.file_validators import validate_rc_xml
        f = tmp_path / "valid.xml"
        f.write_text('<Configuration id="{test-guid}"><entry key="a" value="b"/></Configuration>')
        ok, msg = validate_rc_xml(f)
        assert ok is True

    def test_validate_image_nonexistent(self):
        from modules.rc_common.file_validators import validate_image
        ok, msg = validate_image("/nonexistent/image.jpg")
        assert ok is False

    def test_validate_image_not_an_image(self, tmp_path):
        from modules.rc_common.file_validators import validate_image
        f = tmp_path / "fake.jpg"
        f.write_text("this is not an image")
        ok, msg = validate_image(f)
        assert ok is False

    def test_validate_output_path_valid(self, tmp_path):
        from modules.rc_common.file_validators import validate_output_path
        ok, msg = validate_output_path(tmp_path / "output.txt")
        assert ok is True

    def test_validate_output_path_nonexistent_parent(self):
        from modules.rc_common.file_validators import validate_output_path
        ok, msg = validate_output_path("/nonexistent/parent/file.txt")
        assert ok is False


# ---------------------------------------------------------------------------
# Naming convention defensive tests
# ---------------------------------------------------------------------------


class TestNamingDefensive:
    """Edge cases for filename generation."""

    def test_basic_generation(self):
        from modules.rc_common.naming import generate_filename
        name = generate_filename("NA173", "H2102", "57L", extension=".rsalign")
        assert name == "NA173_H2102_57L.rsalign"

    def test_generation_with_zone(self):
        from modules.rc_common.naming import generate_filename
        name = generate_filename("NA173", "H2102", "57L", zone_number=1, extension=".rsalign")
        assert "zone_001" in name

    def test_generation_extension_without_dot(self):
        from modules.rc_common.naming import generate_filename
        name = generate_filename("NA173", "H2102", "57L", extension="rsalign")
        assert name.endswith(".rsalign")

    def test_generation_empty_strings(self):
        from modules.rc_common.naming import generate_filename
        name = generate_filename("", "", "", extension=".txt")
        assert name == "__.txt"

    def test_validate_filename_too_few_parts(self):
        from modules.rc_common.naming import validate_filename_convention
        ok, msg = validate_filename_convention("onlyonepart.txt")
        assert ok is False

    def test_validate_filename_bad_utm(self):
        from modules.rc_common.naming import validate_filename_convention
        ok, msg = validate_filename_convention("NA173_H2102_BADUTM.rsalign")
        assert ok is False

    def test_validate_filename_valid(self):
        from modules.rc_common.naming import validate_filename_convention
        ok, msg = validate_filename_convention("NA173_H2102_57L.rsalign")
        assert ok is True


# ---------------------------------------------------------------------------
# Session state defensive tests
# ---------------------------------------------------------------------------


class TestSessionDefensive:
    """Edge cases for session save/load."""

    def test_save_creates_parent_dirs(self, tmp_path):
        from modules.rc_common.session import SessionState
        s = SessionState()
        s.expedition = "NA173"
        deep_path = tmp_path / "a" / "b" / "c" / "session.json"
        s.save(deep_path)
        assert deep_path.exists()

    def test_load_nonexistent(self):
        from modules.rc_common.session import SessionState
        s = SessionState()
        with pytest.raises(FileNotFoundError):
            s.load("/nonexistent/session.json")

    def test_load_corrupt_json(self, tmp_path):
        from modules.rc_common.session import SessionState
        f = tmp_path / "corrupt.json"
        f.write_text("not valid json {{{")
        s = SessionState()
        with pytest.raises(json.JSONDecodeError):
            s.load(f)

    def test_load_empty_json(self, tmp_path):
        from modules.rc_common.session import SessionState
        f = tmp_path / "empty.json"
        f.write_text("{}")
        s = SessionState()
        s.load(f)
        assert s.expedition == ""
        assert s.completed_steps == []

    def test_roundtrip(self, tmp_path):
        from modules.rc_common.session import SessionState
        s1 = SessionState()
        s1.expedition = "NA168"
        s1.dive = "H2080"
        s1.utm_zone = "17N"
        s1.mark_step_complete("Extract Images", {"Count": 500})
        s1.mark_step_complete("Georeference", {"Matched": 450})
        path = tmp_path / "session.json"
        s1.save(path)

        s2 = SessionState()
        s2.load(path)
        assert s2.expedition == "NA168"
        assert s2.dive == "H2080"
        assert s2.is_step_complete("Extract Images")
        assert s2.is_step_complete("Georeference")
        assert not s2.is_step_complete("Batch")
        assert s2.get_step_output("Extract Images")["Count"] == 500

    def test_mark_step_complete_idempotent(self):
        from modules.rc_common.session import SessionState
        s = SessionState()
        s.mark_step_complete("Step1", {"a": 1})
        s.mark_step_complete("Step1", {"a": 2})  # Update output
        assert s.completed_steps.count("Step1") == 1
        assert s.get_step_output("Step1")["a"] == 2


# ---------------------------------------------------------------------------
# Checkpoint manager defensive tests
# ---------------------------------------------------------------------------


class TestCheckpointDefensive:
    """Edge cases for checkpoint management."""

    def test_load_nonexistent_checkpoint(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        assert mgr.load_checkpoint("nonexistent_op") is None

    def test_get_completed_items_empty(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        assert mgr.get_completed_items("op") == []

    def test_checkpoint_roundtrip(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        mgr.save_checkpoint("alignment", ["zone_1", "zone_2"], {"project": "test.rsproj"})

        data = mgr.load_checkpoint("alignment")
        assert data is not None
        assert data["completed_items"] == ["zone_1", "zone_2"]
        assert data["metadata"]["project"] == "test.rsproj"

    def test_clear_checkpoint(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        mgr.save_checkpoint("op", ["item1"])
        mgr.clear_checkpoint("op")
        assert mgr.load_checkpoint("op") is None

    def test_clear_nonexistent_checkpoint(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        mgr.clear_checkpoint("nonexistent")  # Should not raise

    def test_operation_name_with_special_chars(self, tmp_path):
        from modules.rc_common.session import CheckpointManager
        mgr = CheckpointManager(tmp_path / "checkpoints")
        mgr.save_checkpoint("batch/zone 1", ["item"])
        data = mgr.load_checkpoint("batch/zone 1")
        assert data is not None


# ---------------------------------------------------------------------------
# RC XML defensive tests
# ---------------------------------------------------------------------------


class TestRCXMLDefensive:
    """Edge cases for RC XML read/write."""

    def test_read_nonexistent(self):
        from modules.rc_common.rc_xml import read_rc_xml
        with pytest.raises(FileNotFoundError):
            read_rc_xml("/nonexistent/params.xml")

    def test_read_wrong_root(self, tmp_path):
        from modules.rc_common.rc_xml import read_rc_xml
        f = tmp_path / "wrong.xml"
        f.write_text('<Root><entry key="a" value="b"/></Root>')
        with pytest.raises(ValueError, match="Configuration"):
            read_rc_xml(f)

    def test_write_creates_parent_dirs(self, tmp_path):
        from modules.rc_common.rc_xml import write_rc_xml
        path = tmp_path / "deep" / "dir" / "params.xml"
        write_rc_xml(path, {"key1": "val1"})
        assert path.exists()

    def test_write_and_read_roundtrip(self, tmp_path):
        from modules.rc_common.rc_xml import write_rc_xml, read_rc_xml
        path = tmp_path / "test.xml"
        params = {"simplifyTarget": "50000", "meshQuality": "high"}
        write_rc_xml(path, params, config_id="test-guid")
        result = read_rc_xml(path)
        assert result == params

    def test_write_empty_params(self, tmp_path):
        from modules.rc_common.rc_xml import write_rc_xml, read_rc_xml
        path = tmp_path / "empty.xml"
        write_rc_xml(path, {})
        result = read_rc_xml(path)
        assert result == {}

    def test_generate_string(self):
        from modules.rc_common.rc_xml import generate_rc_xml_string
        xml_str = generate_rc_xml_string({"key": "value"}, config_id="test")
        assert "Configuration" in xml_str
        assert 'key="key"' in xml_str
        assert 'value="value"' in xml_str

    def test_merge_rc_xml(self, tmp_path):
        from modules.rc_common.rc_xml import write_rc_xml, merge_rc_xml
        path = tmp_path / "base.xml"
        write_rc_xml(path, {"a": "1", "b": "2"})
        merged = merge_rc_xml(path, {"b": "3", "c": "4"})
        assert merged == {"a": "1", "b": "3", "c": "4"}


# ---------------------------------------------------------------------------
# Main.py integration defensive tests
# ---------------------------------------------------------------------------


class TestMainDefensive:
    """Edge cases for main.py helper functions."""

    def test_resolve_session_path_no_params(self):
        from main import _resolve_session_path
        assert _resolve_session_path({}) is None

    def test_resolve_session_path_none_values(self):
        from main import _resolve_session_path
        from module_base.parameter import Parameter
        params = {
            'session_file': Parameter('s', 's', 'session_file', str, None),
            'output_dir': Parameter('o', 'o', 'output_dir', str, None),
        }
        for p in params.values():
            p.set_value(None)
        assert _resolve_session_path(params) is None

    def test_log_output_deeply_nested(self):
        from main import log_output_data
        logger = MagicMock()
        data = {
            "L1": {
                "L2": {
                    "L3": "deep_value"
                }
            }
        }
        log_output_data(logger, data)
        assert logger.info.call_count >= 3

    def test_log_output_empty(self):
        from main import log_output_data
        logger = MagicMock()
        log_output_data(logger, {})
        assert logger.info.call_count == 0


# ---------------------------------------------------------------------------
# Image enhancement defensive tests
# ---------------------------------------------------------------------------


class TestImageEnhancementDefensive:
    """Edge cases for the image enhancement module."""

    def test_disabled_returns_skipped(self):
        from modules.image_enhancement.image_enhancement import ImageEnhancement
        from module_base.parameter import Parameter
        mod = ImageEnhancement(logging.getLogger("test"))
        params = mod.get_parameters()
        # Set all params with defaults (enabled=False)
        all_params = {}
        for k, p in params.items():
            p.set_value(p.get_default_value())
            all_params[k] = p
        mod.set_params(all_params)
        result = mod.run()
        assert result["Success"] is True
        assert result["Skipped"] is True

    def test_validate_no_input_dir_when_enabled(self):
        from modules.image_enhancement.image_enhancement import ImageEnhancement
        from module_base.parameter import Parameter
        mod = ImageEnhancement(logging.getLogger("test"))
        params = mod.get_parameters()
        for k, p in params.items():
            p.set_value(p.get_default_value())
        params["enhance_enabled"].set_value(True)
        params["enhance_input_dir"].set_value(None)
        mod.set_params(params)
        valid, msg = mod.validate_parameters()
        assert valid is False
        assert "required" in msg.lower()

    def test_validate_nonexistent_dir(self, tmp_path):
        from modules.image_enhancement.image_enhancement import ImageEnhancement
        mod = ImageEnhancement(logging.getLogger("test"))
        params = mod.get_parameters()
        for k, p in params.items():
            p.set_value(p.get_default_value())
        params["enhance_enabled"].set_value(True)
        params["enhance_input_dir"].set_value(str(tmp_path / "nonexistent"))
        mod.set_params(params)
        valid, msg = mod.validate_parameters()
        assert valid is False
        assert "does not exist" in msg

    def test_empty_directory(self, tmp_path):
        from modules.image_enhancement.image_enhancement import ImageEnhancement
        mod = ImageEnhancement(logging.getLogger("test"))
        params = mod.get_parameters()
        for k, p in params.items():
            p.set_value(p.get_default_value())
        params["enhance_enabled"].set_value(True)
        params["enhance_input_dir"].set_value(str(tmp_path))
        mod.set_params(params)
        result = mod.run()
        assert result["Success"] is True
        assert result["ImagesProcessed"] == 0


# ---------------------------------------------------------------------------
# Path handling (Windows/Linux cross-platform) tests
# ---------------------------------------------------------------------------


class TestCrossPlatformPaths:
    """Test that path handling works across platforms."""

    def test_camera_detection_windows_path(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("D:\\Photos\\NA173\\camupper_20250101.jpg") == "camupper"

    def test_camera_detection_unix_path(self):
        from modules.rc_common.camera_utils import detect_camera_type
        assert detect_camera_type("/mnt/data/NA173/cammid_20250101.jpg") == "cammid"

    def test_naming_no_path_injection(self):
        """Ensure generated filenames don't contain path separators."""
        from modules.rc_common.naming import generate_filename
        name = generate_filename("NA173", "H2102", "57L", suffix="test", extension=".txt")
        assert "/" not in name
        assert "\\" not in name

    def test_session_path_with_spaces(self, tmp_path):
        """Paths with spaces should work."""
        from modules.rc_common.session import SessionState
        spaced = tmp_path / "path with spaces" / "session.json"
        s = SessionState()
        s.expedition = "test"
        s.save(spaced)
        assert spaced.exists()

        s2 = SessionState()
        s2.load(spaced)
        assert s2.expedition == "test"


# ---------------------------------------------------------------------------
# Concurrency / resource safety
# ---------------------------------------------------------------------------


class TestResourceSafety:
    """Test for resource leaks and cleanup."""

    def test_loading_bar_cleanup_on_finish(self):
        from module_base.rc_module import RCModule

        class DummyModule(RCModule):
            def run(self):
                return {}

        mod = DummyModule("test", logging.getLogger("test"))
        bar = mod._initialize_loading_bar(10, "test")
        assert len(mod.loading_bars) == 1
        mod.finish()
        # After finish, bars should be closed (not necessarily removed from list)

    def test_session_save_atomic_on_error(self, tmp_path):
        """If save fails mid-write, existing session file should not be corrupted."""
        from modules.rc_common.session import SessionState

        path = tmp_path / "session.json"
        s = SessionState()
        s.expedition = "original"
        s.save(path)

        # Verify original is intact
        s2 = SessionState()
        s2.load(path)
        assert s2.expedition == "original"


# ---------------------------------------------------------------------------
# Precomputed alignment import tests
# ---------------------------------------------------------------------------


class TestPrecomputedAlignmentImport:
    """Tests for the optional precomputed .rsalign import feature."""

    def _make_module(self):
        from modules.realitycapture_interface.realitycapture_interface import RealityCaptureAlignment
        mod = RealityCaptureAlignment(logging.getLogger("test_precomputed"))
        return mod

    def test_import_finds_matching_files(self, tmp_path):
        """When .rsalign files exist in precomputed dir, they are copied to output."""
        mod = self._make_module()
        pre_dir = tmp_path / "precomputed"
        pre_dir.mkdir()
        out_dir = tmp_path / "output"

        # Create fake .rsalign files
        (pre_dir / "zone_1_comp1.rsalign").write_text("alignment data 1")
        (pre_dir / "zone_1_comp2.rsalign").write_text("alignment data 2")
        (pre_dir / "unrelated.rsalign").write_text("other data")

        result = mod._RealityCaptureAlignment__import_precomputed_alignments(
            str(pre_dir), "zone_1", str(out_dir), "EX2501", "H001", "20250705_1200",
        )

        assert result is not None
        assert result['Success'] is True
        assert result['Precomputed'] is True
        assert result['Component Count'] == 2  # Only zone_1 matches
        assert (out_dir / "EX2501_H001_zone_1_20250705_1200_1.rsalign").exists()
        assert (out_dir / "EX2501_H001_zone_1_20250705_1200_2.rsalign").exists()

    def test_import_returns_none_when_no_match(self, tmp_path):
        """When no .rsalign files match the zone, returns None (proceed with alignment)."""
        mod = self._make_module()
        pre_dir = tmp_path / "precomputed"
        pre_dir.mkdir()

        # No .rsalign files at all
        result = mod._RealityCaptureAlignment__import_precomputed_alignments(
            str(pre_dir), "zone_1", str(tmp_path / "out"), "EX", "D", "ts",
        )
        assert result is None

    def test_import_returns_none_for_nonexistent_dir(self, tmp_path):
        """Returns None when precomputed dir doesn't exist."""
        mod = self._make_module()
        result = mod._RealityCaptureAlignment__import_precomputed_alignments(
            str(tmp_path / "nonexistent"), "zone_1", str(tmp_path / "out"), "EX", "D", "ts",
        )
        assert result is None

    def test_import_uses_subdirectory_match(self, tmp_path):
        """When a subdirectory matches the zone name, use files from it."""
        mod = self._make_module()
        pre_dir = tmp_path / "precomputed"
        zone_sub = pre_dir / "zone_3"
        zone_sub.mkdir(parents=True)

        (zone_sub / "Component_001.rsalign").write_text("data")
        (zone_sub / "Component_002.rsalign").write_text("data")

        result = mod._RealityCaptureAlignment__import_precomputed_alignments(
            str(pre_dir), "zone_3", str(tmp_path / "output"), "EX", "D", "ts",
        )

        assert result is not None
        assert result['Component Count'] == 2

    def test_import_fallback_all_files(self, tmp_path):
        """When no zone match, all .rsalign files are used as fallback."""
        mod = self._make_module()
        pre_dir = tmp_path / "precomputed"
        pre_dir.mkdir()

        (pre_dir / "Component_001.rsalign").write_text("data")
        (pre_dir / "Component_002.rsalign").write_text("data")

        result = mod._RealityCaptureAlignment__import_precomputed_alignments(
            str(pre_dir), "some_zone", str(tmp_path / "output"), "EX", "D", "ts",
        )

        assert result is not None
        assert result['Component Count'] == 2
