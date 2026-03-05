"""Tests for the RealityCapture alignment delegation mode.

These tests mock subprocess to avoid requiring a running RC instance.
"""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from modules.rc_common.rc_delegation import RCDelegationClient
from modules.rc_common.rc_status import RCStatusParser


@pytest.fixture
def logger():
    return logging.getLogger("test_alignment_delegation")


class TestRCDelegationClient:
    """Tests for the delegation client with mocked subprocess."""

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_delegate(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        result = client.delegate("-newScene")
        assert result.returncode == 0
        # Verify delegateTo was used
        call_args = mock_run.call_args[0][0]
        assert "-delegateTo" in call_args
        assert "-newScene" in call_args

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_get_status_idle(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="id:0xffffffff progress:0.0% runtime:0.00sec endEstimation:0.00sec rev:473 lastError:0",
            stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        status = client.get_status()
        assert status["is_idle"] is True
        assert status["progress"] == 0.0
        assert status["rev"] == 473

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_get_status_active(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="id:0x10001 progress:57.5% runtime:120.50sec endEstimation:89.20sec rev:475 lastError:0",
            stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        status = client.get_status()
        assert status["is_idle"] is False
        assert status["progress"] == pytest.approx(57.5)

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_abort_instance(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        client.abort_instance()
        call_args = mock_run.call_args[0][0]
        assert "-abortInstance" in call_args

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_run_quick(self, mock_run, logger):
        # First call is delegate, second is waitCompleted
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        result = client.run_quick("Test Op", "-newScene")
        assert result.returncode == 0
        assert mock_run.call_count == 2  # delegate + waitCompleted

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_verify_connection_success(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="idle rev:100 lastError:0",
            stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        assert client.verify_connection() is True

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_verify_connection_failure(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=-1, stdout="", stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        assert client.verify_connection() is False

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_get_revision(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="idle rev:482 lastError:0",
            stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        assert client.get_revision() == 482

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_clear_queue(self, mock_run, logger):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        client.clear_queue()
        # Should have called abort_instance
        call_args = mock_run.call_args[0][0]
        assert "-abortInstance" in call_args

    @patch("modules.rc_common.rc_delegation.subprocess.run")
    def test_subprocess_error_handled(self, mock_run, logger):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=10)
        client = RCDelegationClient(rc_exe="/fake/RealityScan.exe", logger=logger)
        result = client.delegate("-test")
        assert result.returncode == -1  # Synthetic error result


class TestRCStatusParserIntegration:
    """Integration tests verifying status parser with delegation client."""

    def test_parse_all_fixture_statuses(self):
        """Parse all status outputs from the test fixture file."""
        fixture = Path(__file__).parent / "fixtures" / "sample_rc_status_outputs.txt"
        lines = fixture.read_text().splitlines()

        parsed_count = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result = RCStatusParser.parse(line)
            assert "is_idle" in result
            assert "progress" in result
            assert "rev" in result
            parsed_count += 1

        assert parsed_count == 9  # 9 non-comment, non-empty lines
