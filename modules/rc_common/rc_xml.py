"""Read, write, and generate RealityScan XML parameter files.

All RC parameter XMLs must follow this exact format:
    <Configuration id="{GUID}">
      <entry key="name" value="val"/>
    </Configuration>
"""
from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


def read_rc_xml(path: str | Path) -> dict[str, str]:
    """Read a RealityScan XML parameter file into a dict.

    Returns dict mapping key -> value for all <entry> elements.
    Raises ValueError if the XML doesn't follow the RC format.
    """
    path = Path(path)
    tree = ET.parse(path)
    root = tree.getroot()

    if root.tag != "Configuration":
        raise ValueError(
            f"Invalid RC XML: root element is '{root.tag}', expected 'Configuration'"
        )

    params = {}
    for child in root:
        if child.tag != "entry":
            _log.warning("Unexpected element '%s' in RC XML (expected 'entry')", child.tag)
            continue
        key = child.get("key")
        value = child.get("value")
        if key is None or value is None:
            _log.warning("Entry missing key or value attribute in %s", path)
            continue
        params[key] = value

    return params


def write_rc_xml(
    path: str | Path,
    params: dict[str, str],
    config_id: Optional[str] = None,
) -> None:
    """Write a RealityScan XML parameter file.

    Parameters
    ----------
    path: Output file path
    params: Dict of key-value pairs to write as <entry> elements
    config_id: Optional GUID for the Configuration id attribute.
               If None, a new UUID is generated.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if config_id is None:
        config_id = f"{{{uuid.uuid4()!s}}}"
    elif not config_id.startswith("{"):
        config_id = f"{{{config_id}}}"

    root = ET.Element("Configuration", id=config_id)

    for key, value in params.items():
        ET.SubElement(root, "entry", key=key, value=str(value))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="unicode", xml_declaration=False)

    # Add trailing newline
    with open(path, "a") as f:
        f.write("\n")

    _log.info("Wrote RC XML with %d entries to %s", len(params), path)


def generate_rc_xml_string(
    params: dict[str, str],
    config_id: Optional[str] = None,
) -> str:
    """Generate RC XML as a string (for embedding or debugging)."""
    if config_id is None:
        config_id = f"{{{uuid.uuid4()!s}}}"
    elif not config_id.startswith("{"):
        config_id = f"{{{config_id}}}"

    root = ET.Element("Configuration", id=config_id)
    for key, value in params.items():
        ET.SubElement(root, "entry", key=key, value=str(value))

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def merge_rc_xml(base_path: str | Path, override: dict[str, str]) -> dict[str, str]:
    """Load an RC XML file and merge/override specific parameters.

    Returns the merged dict (does not write to disk).
    """
    params = read_rc_xml(base_path)
    params.update(override)
    return params
