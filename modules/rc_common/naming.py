"""Filename generation with expedition_dive_utm convention."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def generate_filename(
    expedition: str,
    dive: str,
    utm_zone: str,
    *,
    suffix: str = "",
    extension: str = "",
    zone_number: int | None = None,
    timestamp: datetime | None = None,
    component: str | None = None,
) -> str:
    """Generate a filename following the project naming convention.

    Format: {expedition}_{dive}_{utm_zone}[_zone_{N}][_{component}][_{suffix}][_{timestamp}].{ext}

    The *utm_zone* parameter accepts both legacy band-letter format (``"57L"``)
    and the preferred UTM-hemisphere format (``"UTM57N"``).

    Examples:
        generate_filename("NA173", "H2102", "UTM57N", extension=".rsalign")
        → "NA173_H2102_UTM57N.rsalign"

        generate_filename("NA173", "H2102", "UTM57N", zone_number=1,
                         timestamp=datetime(2025,7,5,3,47), extension=".rsalign")
        → "NA173_H2102_UTM57N_zone_001_20250705_0347.rsalign"

        generate_filename("NA173", "H2102", "UTM57N", component="comp01",
                         suffix="HighPoly", extension=".fbx")
        → "NA173_H2102_UTM57N_comp01_HighPoly.fbx"
    """
    parts = [expedition, dive, utm_zone]

    if zone_number is not None:
        parts.append(f"zone_{zone_number:03d}")

    if component:
        parts.append(component)

    if suffix:
        parts.append(suffix)

    if timestamp:
        parts.append(timestamp.strftime("%Y%m%d_%H%M"))

    name = "_".join(parts)

    if extension:
        if not extension.startswith("."):
            extension = f".{extension}"
        name += extension

    return name


def validate_filename_convention(filename: str) -> tuple[bool, str | None]:
    """Check if a filename follows the {expedition}_{dive}_{utm} convention.

    Accepts both legacy ``"57L"`` and new ``"UTM57N"`` UTM formats.

    Returns (True, None) if valid, (False, reason) if not.
    """
    stem = Path(filename).stem
    parts = stem.split("_")

    if len(parts) < 3:
        return False, f"Filename '{filename}' must have at least 3 underscore-separated parts (expedition_dive_utm)"

    # Accept both legacy "57L" and new "UTM57N" formats
    utm = parts[2]
    utm_body = utm[3:] if utm.upper().startswith("UTM") else utm
    if not (len(utm_body) >= 2 and utm_body[:-1].isdigit() and utm_body[-1].isalpha()):
        return False, f"Third part '{utm}' doesn't look like a UTM zone (expected e.g. 'UTM57N' or '57L')"

    return True, None
