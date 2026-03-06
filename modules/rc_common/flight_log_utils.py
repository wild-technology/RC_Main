"""Shared utilities for flight log parameter XML management.

Provides functions to locate and update the FlightLogParams.xml file
with the correct UTM zone coordinate system (EPSG/PROJ4).

Extracted from RealityCaptureAlignment for reuse by model_generation
and other modules that need to re-apply flight log settings after
importing components into a new scene.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional

_log = logging.getLogger(__name__)


def get_flight_log_params_xml_path() -> str | None:
    """Return path to FlightLogParams.xml in the RC_CLI/Metadata directory.

    Searches relative to the realitycapture_interface module location,
    which is where the RC_CLI/Metadata directory lives.
    """
    # The XML template lives under modules/realitycapture_interface/RC_CLI/Metadata/
    rc_common_dir = os.path.dirname(os.path.abspath(__file__))
    modules_dir = os.path.dirname(rc_common_dir)
    xml_path = os.path.join(
        modules_dir, "realitycapture_interface", "RC_CLI", "Metadata", "FlightLogParams.xml"
    )
    if os.path.isfile(xml_path):
        return xml_path
    return None


def update_flight_log_params_xml(
    flight_log_path: str,
    utm_zone_str: str,
    logger: Optional[logging.Logger] = None,
) -> str | None:
    """Update FlightLogParams.xml with correct EPSG/PROJ4 for the given UTM zone.

    Rewrites the ``CoordinateSystemFlightLog`` and
    ``CoordinateSystemFlightLogType`` entries in the template XML to
    match the specified UTM zone.

    Parameters
    ----------
    flight_log_path:
        Path to the flight log file (used only for logging context).
    utm_zone_str:
        Zone+hemisphere string, e.g. ``"57N"`` or ``"17S"``.
    logger:
        Optional logger instance.  Falls back to the module-level
        logger when ``None``.

    Returns
    -------
    str or None
        Path to the updated XML file, or ``None`` on failure.
    """
    log = logger or _log

    xml_path = get_flight_log_params_xml_path()
    if not xml_path:
        log.warning("FlightLogParams.xml not found, skipping coordinate system update")
        return None

    try:
        # Parse zone number and hemisphere from e.g. "57N"
        match = re.match(r'^(\d{1,2})([NS])$', utm_zone_str)
        if not match:
            log.warning("Cannot parse UTM zone string '%s', skipping XML update", utm_zone_str)
            return None

        zone_number = int(match.group(1))
        hemisphere = match.group(2)

        # Build PROJ4 string
        if hemisphere == 'S':
            proj_str = f"+proj=utm +zone={zone_number} +south +datum=WGS84 +units=m +no_defs"
        else:
            proj_str = f"+proj=utm +zone={zone_number} +datum=WGS84 +units=m +no_defs"

        # Build EPSG code and description
        epsg_code = (32600 + zone_number) if hemisphere == 'N' else (32700 + zone_number)
        epsg_str = f"epsg:{epsg_code} - WGS 84 / UTM zone {zone_number}{hemisphere}"

        # Update XML
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for entry in root.findall("entry"):
            key = entry.get("key")
            if key == "CoordinateSystemFlightLog":
                entry.set("value", proj_str)
            elif key == "CoordinateSystemFlightLogType":
                entry.set("value", epsg_str)

        tree.write(xml_path, encoding="unicode", xml_declaration=False)

        log.info("Updated FlightLogParams.xml: %s", epsg_str)
        return xml_path

    except Exception as e:
        log.error("Failed to update FlightLogParams.xml: %s", e)
        return None
