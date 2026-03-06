"""
RealityScan status parser for delegation infrastructure.

Parses the output of RealityScan's ``-getStatus`` command into structured
dictionaries suitable for polling loops, progress reporting, and idle
detection.

Status format examples::

    id:0xffffffff progress:0.0% runtime:0.00sec endEstimation:0.00sec rev:473 lastError:0
    id:0x10001 progress:57.5% runtime:120.50sec endEstimation:89.20sec rev:475 lastError:0
    idle rev:482 lastError:0

Based on the inline ``RCStatusParser`` in
``StandaloneUtilities/ModelGenerator.py``, extracted and extended for reuse
across the delegation layer.
"""

from __future__ import annotations

import re
from typing import Optional


class RCStatusParser:
    """Parse RealityScan ``-getStatus`` output strings into structured dicts.

    The parser is intentionally lenient: malformed or empty input yields a
    dict populated with safe defaults so that callers never need to guard
    against ``KeyError`` or ``None`` values.
    """

    # Compiled once at class level for performance in tight polling loops.
    _KV_RE = re.compile(r"(\w+):(\S+)")
    _NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")

    # Sentinel values that indicate the instance is idle.
    IDLE_INDICATORS: tuple[str, ...] = (
        "idle",
        "id:0xffffffff",
    )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @staticmethod
    def defaults() -> dict:
        """Return a status dict populated with safe default values."""
        return {
            "id": "",
            "progress": 0.0,
            "runtime": 0.0,
            "estimation": 0.0,
            "is_idle": True,
            "rev": 0,
            "last_error": 0,
            "raw": "",
        }

    @classmethod
    def parse(cls, status_text: Optional[str]) -> dict:
        """Parse a single status line into a structured dictionary.

        Parameters
        ----------
        status_text:
            Raw text returned by ``-getStatus``.  May be ``None`` or empty.

        Returns
        -------
        dict
            Keys: ``id``, ``progress``, ``runtime``, ``estimation``,
            ``is_idle``, ``rev``, ``last_error``, ``raw``.
        """
        result = cls.defaults()

        if not status_text or not status_text.strip():
            return result

        status_text = status_text.strip()
        result["raw"] = status_text

        status_lower = status_text.lower()

        # ---- idle detection ------------------------------------------------
        for indicator in cls.IDLE_INDICATORS:
            if indicator in status_lower:
                result["is_idle"] = True
                break
        else:
            # Tentatively mark as not-idle; may be overridden below if
            # progress is 100%.
            result["is_idle"] = False

        # ---- key:value extraction ------------------------------------------
        for match in cls._KV_RE.finditer(status_text):
            key = match.group(1).lower()
            value = match.group(2)

            if key == "id":
                result["id"] = value
                # Idle ID is a secondary idle indicator.
                if value.lower() == "0xffffffff":
                    result["is_idle"] = True

            elif key == "progress":
                result["progress"] = cls._extract_float(value)

            elif key == "runtime":
                result["runtime"] = cls._extract_float(value)

            elif key in ("endestimation", "estimation"):
                result["estimation"] = cls._extract_float(value)

            elif key == "rev":
                result["rev"] = cls._extract_int(value)

            elif key == "lasterror":
                result["last_error"] = cls._extract_int(value)

        # 100% progress implies the operation finished.
        if result["progress"] >= 100.0:
            result["is_idle"] = True

        return result

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def _extract_float(cls, value: str) -> float:
        """Pull the first decimal number out of *value*, or return 0.0."""
        m = cls._NUMBER_RE.search(value)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, OverflowError):
                pass
        return 0.0

    @staticmethod
    def _extract_int(value: str) -> int:
        """Pull an integer out of *value*, or return 0."""
        try:
            # Handle hex (0x...) and decimal transparently.
            return int(value, 0)
        except (ValueError, OverflowError):
            pass
        # Fallback: grab first run of digits.
        m = re.search(r"(\d+)", value)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, OverflowError):
                pass
        return 0
