"""
Shared coordinate math utilities for georeferencing.

Provides UTM conversion, angle wrapping, camera position offset application,
and vehicle-to-RealityCapture orientation conversion.
"""
from __future__ import annotations
import math
import utm as utm_lib


def wrap180(angle_deg: float) -> float:
    """Wrap angle to [-180, 180] range."""
    return ((angle_deg + 180.0) % 360.0) - 180.0


def wrap360(angle_deg: float) -> float:
    """Wrap angle to [0, 360) range."""
    return angle_deg % 360.0


def convert_to_utm(lat: float | None, lon: float | None) -> tuple[float | None, float | None, int | None, str | None]:
    """Convert latitude/longitude to UTM coordinates.

    Returns:
        (easting, northing, zone_number, zone_letter) or (None, None, None, None) on failure.
    """
    if lat is None or lon is None:
        return None, None, None, None
    try:
        easting, northing, zone_number, zone_letter = utm_lib.from_latlon(lat, lon)
        return easting, northing, zone_number, zone_letter
    except Exception:
        return None, None, None, None


def apply_camera_position_offset(
    utm_x: float | None, utm_y: float | None,
    altitude: float | None, heading_deg: float | None,
    forward_m: float, lateral_m: float, down_m: float,
) -> tuple[float | None, float | None, float | None]:
    """Apply camera position offset from vehicle center to world coordinates.

    Args:
        utm_x, utm_y: Vehicle position in UTM.
        altitude: Vehicle altitude (negative depth).
        heading_deg: Vehicle heading in degrees (0=North, 90=East, clockwise).
        forward_m: Camera offset forward from vehicle center.
        lateral_m: Camera offset to right from vehicle center.
        down_m: Camera offset down from vehicle center.

    Returns:
        (adjusted_utm_x, adjusted_utm_y, adjusted_altitude)
    """
    if utm_x is None or utm_y is None or heading_deg is None:
        return utm_x, utm_y, altitude

    heading_rad = math.radians(heading_deg)

    # Forward offset: east = forward * sin(heading), north = forward * cos(heading)
    east_offset = forward_m * math.sin(heading_rad)
    north_offset = forward_m * math.cos(heading_rad)

    # Lateral offset (right of vehicle)
    east_offset += lateral_m * math.cos(heading_rad)
    north_offset += lateral_m * (-math.sin(heading_rad))

    adjusted_utm_x = utm_x + east_offset
    adjusted_utm_y = utm_y + north_offset
    adjusted_altitude = altitude - down_m if altitude is not None else None

    return adjusted_utm_x, adjusted_utm_y, adjusted_altitude


def convert_to_rc_orientation(
    heading_mag: float | None, pitch_vehicle: float | None,
    roll_vehicle: float | None, camera_offset: float,
    decl_deg: float,
) -> tuple[float | None, float | None, float | None]:
    """Convert vehicle orientation to RealityCapture conventions.

    Input conventions:
        heading_mag: magnetic heading, 0=North, 90=East (clockwise)
        pitch_vehicle: vehicle pitch from horizontal, negative=nose down
        roll_vehicle: vehicle roll, negative=left wing down
        camera_offset: camera down angle from vehicle (positive = down)

    RealityCapture conventions:
        Yaw: 0=North, 90=East, 180=South, 270=West
        Pitch: 0=nadir (straight down), 90=horizontal
        Roll: 0=level, positive=right wing down

    Returns:
        (rc_yaw, rc_pitch, rc_roll)
    """
    if heading_mag is not None:
        rc_yaw = wrap360(heading_mag + decl_deg)
    else:
        rc_yaw = None

    if pitch_vehicle is not None:
        camera_pitch_from_horiz = pitch_vehicle - camera_offset
        rc_pitch = 90.0 + camera_pitch_from_horiz
    else:
        rc_pitch = None

    rc_roll = roll_vehicle

    return rc_yaw, rc_pitch, rc_roll
