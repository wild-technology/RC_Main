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

    Examples:
        generate_filename("NA173", "H2102", "57L", extension=".rsalign")
        → "NA173_H2102_57L.rsalign"

        generate_filename("NA173", "H2102", "57L", zone_number=1,
                         timestamp=datetime(2025,7,5,3,47), extension=".rsalign")
        → "NA173_H2102_57L_zone_001_20250705_0347.rsalign"

        generate_filename("NA173", "H2102", "57L", component="comp01",
                         suffix="HighPoly", extension=".fbx")
        → "NA173_H2102_57L_comp01_HighPoly.fbx"
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

    Returns (True, None) if valid, (False, reason) if not.
    """
    stem = Path(filename).stem
    parts = stem.split("_")

    if len(parts) < 3:
        return False, f"Filename '{filename}' must have at least 3 underscore-separated parts (expedition_dive_utm)"

    # Basic check: UTM zone should be digits followed by a letter
    utm = parts[2]
    if not (len(utm) >= 2 and utm[:-1].isdigit() and utm[-1].isalpha()):
        return False, f"Third part '{utm}' doesn't look like a UTM zone (expected e.g. '57L')"

    return True, None
