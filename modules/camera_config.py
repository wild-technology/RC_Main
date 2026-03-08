"""
Shared camera configuration constants and detection functions.

Single source of truth for camera types, pitch offsets, position offsets,
accuracy values, and subfolder naming used across georeferencing, batching,
and standalone scripts.
"""
from __future__ import annotations


# Camera configuration keyed by canonical type name.
# Each entry: pitch_offset (degrees down from vehicle forward),
#             pitch_accuracy (degrees), position_offsets (forward, lateral, down in meters),
#             subfolder (directory name used in batch output and flight logs)
CAMERA_CONFIGS = {
    'camupper': {
        'pitch_offset': 70.0,
        'pitch_accuracy': 10.0,
        'position_offsets': (1.0, 0.0, 0.0),
        'subfolder': 'camupper',
    },
    'cammid': {
        'pitch_offset': 20.0,
        'pitch_accuracy': 10.0,
        'position_offsets': (1.0, 0.0, 1.0),
        'subfolder': 'cammid',
    },
    'camlower': {
        'pitch_offset': 10.0,
        'pitch_accuracy': 5.0,
        'position_offsets': (1.0, 0.0, 1.0),
        'subfolder': 'camlower',
    },
    'zeuss_herc': {
        'pitch_offset': 30.0,
        'pitch_accuracy': 30.0,
        'position_offsets': (0.5, 0.0, 0.5),
        'subfolder': 'zeuss',
    },
}

DEFAULT_CAMERA_CONFIG = {
    'pitch_offset': 0.0,
    'pitch_accuracy': 10.0,
    'position_offsets': (0.0, 0.0, 0.0),
    'subfolder': 'other',
}

DEFAULT_YAW_ACCURACY = 3.0
DEFAULT_ROLL_ACCURACY = 3.0
DEFAULT_POS_ACCURACY = (10.0, 10.0, 1.0)  # X, Y, Alt


def detect_camera_type(filename: str) -> str:
    """Identify canonical camera type key from an image filename.

    Returns one of the keys in CAMERA_CONFIGS or 'unknown'.
    """
    fn = filename.lower()
    if fn.startswith('camupper'):
        return 'camupper'
    elif fn.startswith('cammid'):
        return 'cammid'
    elif fn.startswith('camlower'):
        return 'camlower'
    elif '_herc_' in fn or 'herc' in fn or 'zeuss' in fn:
        return 'zeuss_herc'
    return 'unknown'


def _get_config(filename: str) -> dict:
    """Return the camera config dict for a filename."""
    cam = detect_camera_type(filename)
    return CAMERA_CONFIGS.get(cam, DEFAULT_CAMERA_CONFIG)


def get_camera_pitch_offset(filename: str) -> float:
    """Camera pitch offset in degrees (positive = pointing down)."""
    return _get_config(filename)['pitch_offset']


def get_camera_pitch_accuracy(filename: str) -> float:
    """Pitch accuracy in degrees for a given camera."""
    return _get_config(filename)['pitch_accuracy']


def get_camera_position_offsets(filename: str) -> tuple[float, float, float]:
    """(forward, lateral, down) position offsets in meters from vehicle center."""
    return _get_config(filename)['position_offsets']


def get_camera_accuracy(filename: str) -> tuple[float, float, float]:
    """Return (yaw, pitch, roll) accuracy in degrees."""
    pitch_acc = get_camera_pitch_accuracy(filename)
    return (DEFAULT_YAW_ACCURACY, pitch_acc, DEFAULT_ROLL_ACCURACY)


def camera_subfolder_name(filename: str) -> str:
    """Standardized subfolder name for organizing images by camera type."""
    return _get_config(filename)['subfolder']
