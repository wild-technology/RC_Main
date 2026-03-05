"""Session state persistence and checkpoint management."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)


class SessionState:
    """Manages pipeline session state with JSON persistence.

    Saves/loads: expedition, dive, parameters, completed steps,
    step outputs, and current step.
    """

    def __init__(self) -> None:
        self.expedition: str = ""
        self.dive: str = ""
        self.utm_zone: str = ""
        self.parameters: dict[str, Any] = {}
        self.completed_steps: list[str] = []
        self.current_step: str = ""
        self.step_outputs: dict[str, dict[str, Any]] = {}
        self.timestamp: str = ""

    def mark_step_complete(self, step_name: str, outputs: Optional[dict] = None) -> None:
        """Record a step as completed with optional output data."""
        if step_name not in self.completed_steps:
            self.completed_steps.append(step_name)
        if outputs:
            self.step_outputs[step_name] = outputs
        self.current_step = ""
        self.timestamp = datetime.now().isoformat()
        _log.info("Step '%s' marked complete", step_name)

    def set_current_step(self, step_name: str) -> None:
        """Set the currently executing step."""
        self.current_step = step_name
        self.timestamp = datetime.now().isoformat()

    def is_step_complete(self, step_name: str) -> bool:
        """Check if a step has been completed."""
        return step_name in self.completed_steps

    def get_step_output(self, step_name: str) -> Optional[dict]:
        """Get the output data from a completed step."""
        return self.step_outputs.get(step_name)

    def save(self, path: str | Path) -> None:
        """Save session state to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "expedition": self.expedition,
            "dive": self.dive,
            "utm_zone": self.utm_zone,
            "parameters": self.parameters,
            "completed_steps": self.completed_steps,
            "current_step": self.current_step,
            "step_outputs": self.step_outputs,
            "timestamp": datetime.now().isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        _log.info("Session saved to %s", path)

    def load(self, path: str | Path) -> None:
        """Load session state from a JSON file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Session file not found: {path}")
        with open(path, "r") as f:
            data = json.load(f)
        self.expedition = data.get("expedition", "")
        self.dive = data.get("dive", "")
        self.utm_zone = data.get("utm_zone", "")
        self.parameters = data.get("parameters", {})
        self.completed_steps = data.get("completed_steps", [])
        self.current_step = data.get("current_step", "")
        self.step_outputs = data.get("step_outputs", {})
        self.timestamp = data.get("timestamp", "")
        _log.info("Session loaded from %s (completed: %s)", path, self.completed_steps)


class CheckpointManager:
    """Manages per-operation checkpoints for long-running tasks.

    Checkpoints track completed sub-units (zones, components) so that
    operations can resume after interruption.
    """

    def __init__(self, checkpoint_dir: str | Path) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _checkpoint_path(self, operation: str) -> Path:
        """Get the checkpoint file path for an operation."""
        safe_name = operation.replace(" ", "_").replace("/", "_")
        return self.checkpoint_dir / f"checkpoint_{safe_name}.json"

    def save_checkpoint(
        self,
        operation: str,
        completed_items: list[str],
        metadata: Optional[dict] = None,
    ) -> None:
        """Save a checkpoint for a long-running operation."""
        path = self._checkpoint_path(operation)
        data = {
            "operation": operation,
            "completed_items": completed_items,
            "metadata": metadata or {},
            "timestamp": datetime.now().isoformat(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        _log.debug("Checkpoint saved for '%s': %d items", operation, len(completed_items))

    def load_checkpoint(self, operation: str) -> Optional[dict]:
        """Load checkpoint data for an operation, or None if not found."""
        path = self._checkpoint_path(operation)
        if not path.exists():
            return None
        with open(path, "r") as f:
            data = json.load(f)
        _log.info(
            "Checkpoint loaded for '%s': %d completed items",
            operation,
            len(data.get("completed_items", [])),
        )
        return data

    def get_completed_items(self, operation: str) -> list[str]:
        """Get list of completed items for an operation."""
        data = self.load_checkpoint(operation)
        if data is None:
            return []
        return data.get("completed_items", [])

    def clear_checkpoint(self, operation: str) -> None:
        """Remove checkpoint file for an operation."""
        path = self._checkpoint_path(operation)
        if path.exists():
            path.unlink()
            _log.info("Checkpoint cleared for '%s'", operation)
