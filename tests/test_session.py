"""Tests for session: SessionState persistence and CheckpointManager."""
import pytest

from modules.rc_common.session import CheckpointManager, SessionState


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------

def test_session_save_load(tmp_path):
    path = tmp_path / "session.json"

    s1 = SessionState()
    s1.expedition = "NA173"
    s1.dive = "H2102"
    s1.utm_zone = "57L"
    s1.parameters = {"quality": "high"}
    s1.save(path)

    s2 = SessionState()
    s2.load(path)

    assert s2.expedition == "NA173"
    assert s2.dive == "H2102"
    assert s2.utm_zone == "57L"
    assert s2.parameters == {"quality": "high"}


def test_mark_step_complete(tmp_path):
    s = SessionState()
    s.mark_step_complete("alignment")
    assert s.is_step_complete("alignment") is True


def test_step_not_complete():
    s = SessionState()
    assert s.is_step_complete("alignment") is False


def test_step_output():
    s = SessionState()
    outputs = {"output_file": "/tmp/result.obj", "count": 42}
    s.mark_step_complete("model_gen", outputs=outputs)
    assert s.get_step_output("model_gen") == outputs


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------

def test_checkpoint_save_load(tmp_path):
    mgr = CheckpointManager(tmp_path / "checkpoints")
    mgr.save_checkpoint("alignment", ["zone_001", "zone_002"])

    data = mgr.load_checkpoint("alignment")
    assert data is not None
    assert data["completed_items"] == ["zone_001", "zone_002"]


def test_checkpoint_not_found(tmp_path):
    mgr = CheckpointManager(tmp_path / "checkpoints")
    assert mgr.load_checkpoint("nonexistent") is None


def test_checkpoint_clear(tmp_path):
    mgr = CheckpointManager(tmp_path / "checkpoints")
    mgr.save_checkpoint("alignment", ["zone_001"])
    mgr.clear_checkpoint("alignment")
    assert mgr.load_checkpoint("alignment") is None


def test_get_completed_items(tmp_path):
    mgr = CheckpointManager(tmp_path / "checkpoints")
    items = ["zone_001", "zone_002", "zone_003"]
    mgr.save_checkpoint("alignment", items)
    assert mgr.get_completed_items("alignment") == items
