"""Integration tests for main.py pipeline orchestration."""

import json
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Test the helper functions and pipeline logic


class TestSessionResolution:
    """Test session path resolution."""

    def test_resolve_session_path_from_param(self, tmp_path):
        from main import _resolve_session_path
        from module_base.parameter import Parameter

        params = {
            'session_file': Parameter('s', 's', 'session_file', str, str(tmp_path / 'my_session.json')),
        }
        params['session_file'].set_value(str(tmp_path / 'my_session.json'))
        assert _resolve_session_path(params) == str(tmp_path / 'my_session.json')

    def test_resolve_session_path_auto(self, tmp_path):
        from main import _resolve_session_path
        from module_base.parameter import Parameter

        params = {
            'session_file': Parameter('s', 's', 'session_file', str, None),
            'output_dir': Parameter('o', 'o', 'output_dir', str, str(tmp_path)),
            'expedition_name': Parameter('exp', 'exp', 'expedition_name', str, 'NA173'),
            'dive_name': Parameter('dive', 'dive', 'dive_name', str, 'H2102'),
        }
        for p in params.values():
            p.set_value(p.get_default_value())
        result = _resolve_session_path(params)
        assert result is not None
        assert 'NA173_H2102_session.json' in result

    def test_resolve_session_path_no_output_dir(self):
        from main import _resolve_session_path
        from module_base.parameter import Parameter

        params = {
            'session_file': Parameter('s', 's', 'session_file', str, None),
        }
        params['session_file'].set_value(None)
        assert _resolve_session_path(params) is None


class TestLoadOrCreateSession:
    """Test session load/create logic."""

    def test_create_new_session(self, tmp_path):
        from main import _load_or_create_session
        from module_base.parameter import Parameter

        logger = logging.getLogger("test")
        params = {
            'session_file': Parameter('s', 's', 'session_file', str, None),
            'expedition_name': Parameter('exp', 'exp', 'expedition_name', str, 'NA173'),
            'dive_name': Parameter('dive', 'dive', 'dive_name', str, 'H2102'),
        }
        for p in params.values():
            p.set_value(p.get_default_value())

        session = _load_or_create_session(params, logger)
        assert session.expedition == 'NA173'
        assert session.dive == 'H2102'
        assert session.completed_steps == []

    def test_load_existing_session(self, tmp_path):
        from main import _load_or_create_session
        from module_base.parameter import Parameter
        from modules.rc_common.session import SessionState

        logger = logging.getLogger("test")
        session_path = tmp_path / "test_session.json"

        # Create a session file
        s = SessionState()
        s.expedition = "NA168"
        s.dive = "H2080"
        s.mark_step_complete("Extract Images", {"Success": True})
        s.save(session_path)

        params = {
            'session_file': Parameter('s', 's', 'session_file', str, str(session_path)),
            'expedition_name': Parameter('exp', 'exp', 'expedition_name', str, 'NA173'),
            'dive_name': Parameter('dive', 'dive', 'dive_name', str, 'H2102'),
        }
        for p in params.values():
            p.set_value(p.get_default_value())

        session = _load_or_create_session(params, logger)
        assert session.expedition == "NA168"
        assert session.is_step_complete("Extract Images")


class TestInitializeParameters:
    """Test global parameter registration."""

    def test_global_params_present(self):
        from main import initialize_parameters
        # Use an empty module dict
        params = initialize_parameters({})
        assert 'expedition_name' in params
        assert 'dive_name' in params
        assert 'output_dir' in params
        assert 'rc_executable_path' in params
        assert 'rc_instance_name' in params
        assert 'camera_profiles_path' in params
        assert 'session_file' in params
        assert 'rc_checkpoint_dir' in params

    def test_rc_instance_default(self):
        from main import initialize_parameters
        params = initialize_parameters({})
        assert params['rc_instance_name'].default_value == '*'

    def test_camera_profiles_default(self):
        from main import initialize_parameters
        params = initialize_parameters({})
        assert params['camera_profiles_path'].default_value == 'config/camera_profiles.json'


class TestPipelineSkipCompleted:
    """Test that session resume skips completed steps."""

    @patch.dict(os.environ, {'RC_NO_INTERACTIVE': '1', 'RC_MODULES': 'Extract Images'})
    def test_session_resume_skips_completed(self, tmp_path):
        """Verify the session saves after each step by testing _save_session."""
        from main import _save_session, _load_or_create_session
        from module_base.parameter import Parameter
        from modules.rc_common.session import SessionState

        logger = logging.getLogger("test")
        session_path = tmp_path / "session.json"

        session = SessionState()
        session.expedition = "NA173"
        session.dive = "H2102"
        session.mark_step_complete("Extract Images", {"Success": True, "Count": 100})

        params = {
            'session_file': Parameter('s', 's', 'session_file', str, str(session_path)),
            'output_dir': Parameter('o', 'o', 'output_dir', str, str(tmp_path)),
        }
        for p in params.values():
            p.set_value(p.get_default_value())

        _save_session(session, params, logger)

        # Verify file was written
        assert session_path.exists()
        data = json.loads(session_path.read_text())
        assert "Extract Images" in data["completed_steps"]
        assert data["step_outputs"]["Extract Images"]["Count"] == 100


class TestLogOutputData:
    """Test recursive output logging."""

    def test_log_simple(self):
        from main import log_output_data
        logger = MagicMock()
        log_output_data(logger, {"Key": "Value"})
        logger.info.assert_called_once()

    def test_log_nested(self):
        from main import log_output_data
        logger = MagicMock()
        log_output_data(logger, {"Module": {"Success": True, "Count": 5}})
        assert logger.info.call_count == 3  # Module: + Success: True + Count: 5
