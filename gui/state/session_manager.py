"""Session management for the GUI, bridging UI actions and SessionState."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from modules.rc_common.session import SessionState, CheckpointManager
from gui.state.metadata_db import MetadataDB

_log = logging.getLogger(__name__)


class SessionManager:
    """Manages session lifecycle for the GUI.

    Coordinates SessionState (pipeline state), CheckpointManager
    (per-operation checkpoints), and MetadataDB (cross-session history).
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.session = SessionState()
        self._checkpoint: Optional[CheckpointManager] = None
        self._db: Optional[MetadataDB] = None
        self._session_path: Optional[str] = None

        if db_path:
            self._db = MetadataDB(db_path)

    @property
    def checkpoint(self) -> Optional[CheckpointManager]:
        return self._checkpoint

    def new_session(self, expedition: str = "", dive: str = "") -> None:
        """Create a fresh session."""
        self.session = SessionState()
        self.session.expedition = expedition
        self.session.dive = dive
        self._session_path = None
        self._checkpoint = None
        _log.info("New session created: %s / %s", expedition, dive)

    def load_session(self, path: str) -> None:
        """Load session from file."""
        self.session.load(path)
        self._session_path = path
        _log.info("Session loaded from %s", path)

        # Set up checkpoint dir based on session
        if self.session.parameters.get("rc_checkpoint_dir"):
            self._checkpoint = CheckpointManager(self.session.parameters["rc_checkpoint_dir"])

    def save_session(self, path: str | None = None) -> None:
        """Save session to file."""
        save_path = path or self._session_path
        if not save_path:
            raise ValueError("No session file path specified")
        self.session.save(save_path)
        self._session_path = save_path

        # Record in metadata DB
        if self._db and self.session.expedition and self.session.dive:
            dive_id = self._db.add_dive(self.session.expedition, self.session.dive)
            self._db.add_session(dive_id, save_path)

    def mark_step_complete(self, step_name: str, outputs: dict | None = None) -> None:
        """Mark a step as complete and auto-save."""
        self.session.mark_step_complete(step_name, outputs)
        if self._session_path:
            self.save_session()

    def setup_checkpoints(self, checkpoint_dir: str) -> None:
        """Initialize checkpoint manager."""
        self._checkpoint = CheckpointManager(checkpoint_dir)

    def close(self) -> None:
        """Clean up resources."""
        if self._db:
            self._db.close()
