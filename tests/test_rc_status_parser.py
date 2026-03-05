"""Comprehensive unit tests for RCStatusParser.

Tests cover idle detection, active-state parsing, completion semantics,
error states, long-running operations, keyword-based idle, and edge cases
(None / empty input).  All tests are pure-Python and run on Linux.
"""

import pytest

from modules.rc_common.rc_status import RCStatusParser


# ---- Fixture lines pulled from tests/fixtures/sample_rc_status_outputs.txt --

IDLE_LINE = "id:0xffffffff progress:0.0% runtime:0.00sec endEstimation:0.00sec rev:473 lastError:0"
ACTIVE_EARLY = "id:0x10001 progress:12.5% runtime:4.26sec endEstimation:3.40sec rev:474 lastError:0"
ACTIVE_MID = "id:0x10001 progress:57.5% runtime:120.50sec endEstimation:89.20sec rev:475 lastError:0"
NEAR_COMPLETE = "id:0x10001 progress:98.2% runtime:245.80sec endEstimation:4.50sec rev:476 lastError:0"
JUST_COMPLETED = "id:0xffffffff progress:100.0% runtime:250.30sec endEstimation:0.00sec rev:477 lastError:0"
ERROR_STATE = "id:0xffffffff progress:0.0% runtime:0.00sec endEstimation:0.00sec rev:478 lastError:1"
LONG_RUNNING = "id:0x20002 progress:3.1% runtime:1800.00sec endEstimation:56000.00sec rev:480 lastError:0"
IDLE_KEYWORD = "idle rev:482 lastError:0"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestRCStatusParser:
    """Tests for RCStatusParser.parse() and defaults()."""

    def test_parse_idle_state(self):
        """id:0xffffffff should be detected as idle with progress 0.0."""
        result = RCStatusParser.parse(IDLE_LINE)

        assert result["id"] == "0xffffffff"
        assert result["is_idle"] is True
        assert result["progress"] == pytest.approx(0.0)
        assert result["last_error"] == 0
        assert result["raw"] == IDLE_LINE

    def test_parse_active_early(self):
        """Early active state: id:0x10001, progress 12.5%, not idle."""
        result = RCStatusParser.parse(ACTIVE_EARLY)

        assert result["id"] == "0x10001"
        assert result["progress"] == pytest.approx(12.5)
        assert result["is_idle"] is False
        assert result["last_error"] == 0

    def test_parse_active_mid(self):
        """Mid-progress: 57.5%, runtime and estimation populated."""
        result = RCStatusParser.parse(ACTIVE_MID)

        assert result["progress"] == pytest.approx(57.5)
        assert result["runtime"] == pytest.approx(120.50)
        assert result["estimation"] == pytest.approx(89.20)
        assert result["is_idle"] is False

    def test_parse_near_complete(self):
        """Near-complete: 98.2% — still not idle (below 100%)."""
        result = RCStatusParser.parse(NEAR_COMPLETE)

        assert result["progress"] == pytest.approx(98.2)
        assert result["is_idle"] is False

    def test_parse_just_completed(self):
        """100% progress should mark state as idle."""
        result = RCStatusParser.parse(JUST_COMPLETED)

        assert result["progress"] == pytest.approx(100.0)
        assert result["is_idle"] is True

    def test_parse_error_state(self):
        """lastError:1 must be reflected as last_error=1."""
        result = RCStatusParser.parse(ERROR_STATE)

        assert result["last_error"] == 1
        assert result["is_idle"] is True  # id is 0xffffffff

    def test_parse_long_running(self):
        """Long model calculation: id:0x20002, low progress, high runtime."""
        result = RCStatusParser.parse(LONG_RUNNING)

        assert result["id"] == "0x20002"
        assert result["progress"] == pytest.approx(3.1)
        assert result["runtime"] == pytest.approx(1800.0)
        assert result["is_idle"] is False

    def test_parse_idle_keyword(self):
        """The bare 'idle' keyword (no id field) should be detected as idle."""
        result = RCStatusParser.parse(IDLE_KEYWORD)

        assert result["is_idle"] is True
        assert result["rev"] == 482
        assert result["last_error"] == 0

    def test_parse_none_input(self):
        """None input should return a defaults dict."""
        result = RCStatusParser.parse(None)

        expected = RCStatusParser.defaults()
        assert result == expected

    def test_parse_empty_string(self):
        """Empty string input should return a defaults dict."""
        result = RCStatusParser.parse("")

        expected = RCStatusParser.defaults()
        assert result == expected

    def test_defaults(self):
        """Verify all default keys are present with correct types."""
        defaults = RCStatusParser.defaults()

        assert isinstance(defaults["id"], str)
        assert isinstance(defaults["progress"], float)
        assert isinstance(defaults["runtime"], float)
        assert isinstance(defaults["estimation"], float)
        assert isinstance(defaults["is_idle"], bool)
        assert isinstance(defaults["rev"], int)
        assert isinstance(defaults["last_error"], int)
        assert isinstance(defaults["raw"], str)

        # Verify default values
        assert defaults["id"] == ""
        assert defaults["progress"] == 0.0
        assert defaults["runtime"] == 0.0
        assert defaults["estimation"] == 0.0
        assert defaults["is_idle"] is True
        assert defaults["rev"] == 0
        assert defaults["last_error"] == 0
        assert defaults["raw"] == ""
