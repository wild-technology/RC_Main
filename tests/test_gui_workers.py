"""Tests for GUI worker and state modules (syntax and structure)."""

import ast
import sqlite3
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).parent.parent / "gui"


class TestWorkerSyntax:
    """Verify worker files parse without errors."""

    @pytest.fixture(params=[
        "workers/pipeline_worker.py",
        "workers/rc_process.py",
    ])
    def worker_file(self, request):
        return GUI_DIR / request.param

    def test_file_exists(self, worker_file):
        assert worker_file.exists()

    def test_parses_cleanly(self, worker_file):
        source = worker_file.read_text()
        ast.parse(source, filename=str(worker_file))


class TestWorkerClasses:
    """Verify expected classes exist in worker modules."""

    def _get_classes(self, filepath: Path) -> list[str]:
        source = filepath.read_text()
        tree = ast.parse(source)
        return [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    def test_pipeline_worker_classes(self):
        classes = self._get_classes(GUI_DIR / "workers" / "pipeline_worker.py")
        assert "PipelineWorker" in classes
        assert "WorkerSignals" in classes
        assert "GUIProgressBackend" in classes

    def test_rc_process_classes(self):
        classes = self._get_classes(GUI_DIR / "workers" / "rc_process.py")
        assert "RCProcess" in classes
        assert "RCProcessSignals" in classes


class TestStateSyntax:
    """Verify state management files parse correctly."""

    @pytest.fixture(params=[
        "state/metadata_db.py",
        "state/session_manager.py",
    ])
    def state_file(self, request):
        return GUI_DIR / request.param

    def test_file_exists(self, state_file):
        assert state_file.exists()

    def test_parses_cleanly(self, state_file):
        source = state_file.read_text()
        ast.parse(source, filename=str(state_file))


class TestStateClasses:
    """Verify expected classes in state modules."""

    def _get_classes(self, filepath: Path) -> list[str]:
        source = filepath.read_text()
        tree = ast.parse(source)
        return [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    def test_metadata_db_class(self):
        classes = self._get_classes(GUI_DIR / "state" / "metadata_db.py")
        assert "MetadataDB" in classes

    def test_session_manager_class(self):
        classes = self._get_classes(GUI_DIR / "state" / "session_manager.py")
        assert "SessionManager" in classes


class TestMetadataDB:
    """Test MetadataDB operations (SQLite, no PySide6 needed)."""

    def test_create_db(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        assert (tmp_path / "test.db").exists()
        db.close()

    def test_add_expedition(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        exp_id = db.add_expedition("NA173")
        assert isinstance(exp_id, int)
        assert exp_id > 0
        db.close()

    def test_add_dive(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        dive_id = db.add_dive("NA173", "H2102")
        assert isinstance(dive_id, int)
        assert dive_id > 0
        db.close()

    def test_add_session(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        dive_id = db.add_dive("NA173", "H2102")
        session_id = db.add_session(dive_id, "/tmp/session.json")
        assert isinstance(session_id, int)
        db.close()

    def test_list_expeditions(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        db.add_expedition("NA173")
        db.add_expedition("NA168")
        exps = db.list_expeditions()
        assert len(exps) == 2
        names = {e["name"] for e in exps}
        assert names == {"NA173", "NA168"}
        db.close()

    def test_list_dives(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        db.add_dive("NA173", "H2102")
        db.add_dive("NA173", "H2103")
        dives = db.list_dives("NA173")
        assert len(dives) == 2
        db.close()

    def test_list_sessions(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        dive_id = db.add_dive("NA173", "H2102")
        db.add_session(dive_id, "/tmp/s1.json")
        db.add_session(dive_id, "/tmp/s2.json")
        sessions = db.list_sessions("NA173")
        assert len(sessions) == 2
        db.close()

    def test_idempotent_expedition(self, tmp_path):
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(tmp_path / "test.db")
        id1 = db.add_expedition("NA173")
        id2 = db.add_expedition("NA173")
        assert id1 == id2
        db.close()


class TestSessionManager:
    """Test SessionManager (no PySide6 needed)."""

    def test_new_session(self, tmp_path):
        from gui.state.session_manager import SessionManager
        mgr = SessionManager()
        mgr.new_session("NA173", "H2102")
        assert mgr.session.expedition == "NA173"
        assert mgr.session.dive == "H2102"

    def test_save_and_load_session(self, tmp_path):
        from gui.state.session_manager import SessionManager
        mgr = SessionManager()
        mgr.new_session("NA173", "H2102")
        mgr.session.mark_step_complete("Extract Images", {"Count": 500})

        path = str(tmp_path / "session.json")
        mgr.save_session(path)

        mgr2 = SessionManager()
        mgr2.load_session(path)
        assert mgr2.session.expedition == "NA173"
        assert mgr2.session.is_step_complete("Extract Images")

    def test_mark_step_complete_autosaves(self, tmp_path):
        from gui.state.session_manager import SessionManager
        path = str(tmp_path / "session.json")
        mgr = SessionManager()
        mgr.new_session("NA173", "H2102")
        mgr.save_session(path)  # Set the path

        mgr.mark_step_complete("Enhance Images", {"Processed": 100})

        # Verify autosave happened
        import json
        data = json.loads(Path(path).read_text())
        assert "Enhance Images" in data["completed_steps"]

    def test_with_metadata_db(self, tmp_path):
        from gui.state.session_manager import SessionManager
        db_path = tmp_path / "meta.db"
        mgr = SessionManager(db_path=db_path)
        mgr.new_session("NA173", "H2102")
        mgr.save_session(str(tmp_path / "session.json"))

        # Check DB was populated
        from gui.state.metadata_db import MetadataDB
        db = MetadataDB(db_path)
        exps = db.list_expeditions()
        assert any(e["name"] == "NA173" for e in exps)
        db.close()
        mgr.close()
