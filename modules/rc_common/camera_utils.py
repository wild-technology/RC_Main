"""Camera type detection and profile management."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# Default config path relative to project root
_DEFAULT_PROFILES_PATH = Path(__file__).resolve().parents[2] / "config" / "camera_profiles.json"


def load_camera_profiles(config_path: Optional[str | Path] = None) -> dict:
    """Load camera profiles from JSON config file.

    Returns dict with 'cameras' key containing list of camera profile dicts.
    """
    path = Path(config_path) if config_path else _DEFAULT_PROFILES_PATH
    if not path.exists():
        raise FileNotFoundError(f"Camera profiles not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _log.info("Loaded %d camera profiles from %s", len(data.get("cameras", [])), path)
    return data


def detect_camera_type(filename: str) -> str:
    """Detect camera type from filename.

    Returns one of: 'camlower', 'cammid', 'camupper', 'zeuss', 'unknown'.

    Detection rules:
    - Filenames starting with 'camupper_', 'cammid_', 'camlower_' → that camera
    - Filenames containing 'HERC' or 'hercules' (case-insensitive) → 'zeuss'
    - Everything else → 'unknown'
    """
    # Use PureWindowsPath to handle both Unix and Windows path separators
    from pathlib import PureWindowsPath
    basename = PureWindowsPath(filename).stem.lower()

    if basename.startswith("camupper"):
        return "camupper"
    elif basename.startswith("cammid"):
        return "cammid"
    elif basename.startswith("camlower"):
        return "camlower"
    elif "herc" in basename or "hercules" in basename:
        return "zeuss"

    return "unknown"


def get_camera_profile(filename: str, profiles: dict) -> Optional[dict]:
    """Get the camera profile matching a filename.

    Parameters
    ----------
    filename: Image filename (not full path needed, just the name)
    profiles: Dict loaded from camera_profiles.json

    Returns
    -------
    Matching camera profile dict, or None if no match.
    """
    camera_type = detect_camera_type(filename)
    if camera_type == "unknown":
        return None

    for camera in profiles.get("cameras", []):
        for keyword in camera.get("keywords", []):
            if keyword.lower() == camera_type.lower():
                return camera

    return None


def get_camera_groups(profiles: dict) -> dict[str, list[str]]:
    """Return a dict mapping camera name to its keyword patterns.

    Useful for batch operations that need to iterate over all camera types.
    """
    return {
        cam["name"]: cam["keywords"]
        for cam in profiles.get("cameras", [])
    }
