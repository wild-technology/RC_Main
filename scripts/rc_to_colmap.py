#!/usr/bin/env python3
"""
rc_to_colmap.py — Drive an open RealityCapture project to export a COLMAP
registration + undistorted images, then convert to COLMAP binary format.

Automates the three manual steps documented in the export guide:

  Step 1  -exportRegistration  → cameras.txt / images.txt / points3D.txt
  Step 2  -exportUndistoredImages  → <output_dir>/images/*.jpg
  Step 3  colmap model_converter (text → binary), validate, clean up text

All steps communicate with a running RealityScan instance via the
existing RCDelegationClient (two-phase idle detection, no hardcoded
operation timeouts).

Usage (Windows):
  python scripts/rc_to_colmap.py ^
      --rc_exe      "C:\\Program Files\\Capturing Reality\\RealityScan\\RealityScan.exe" ^
      --output_dir  "C:\\Users\\WildTech\\Desktop\\H2103d_Northampton" ^
      [--instance   "*"] ^
      [--keep_text]

Output layout produced:
  <output_dir>/
    images/               ← undistorted JPEGs from RC
    colmap/sparse/0/      ← cameras.bin, images.bin, points3D.bin
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.rc_common.rc_delegation import RCDelegationClient  # noqa: E402
from modules.rc_common.rc_xml import write_rc_xml               # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RC note: "exportUndistoredImages" is RC's own spelling — do not fix it.
# ---------------------------------------------------------------------------
COLMAP_TEXT_FILES = ("cameras.txt", "images.txt", "points3D.txt")
COLMAP_BIN_FILES  = ("cameras.bin", "images.bin", "points3D.bin")


# ---------------------------------------------------------------------------
# XML parameter builders
# ---------------------------------------------------------------------------

def _write_registration_xml(path: Path) -> None:
    """Write export-registration params: COLMAP Text format."""
    write_rc_xml(
        path=str(path),
        params={
            "exportFormat":        "colmap",   # COLMAP Text Format
            "exportType":          "txt",
        },
    )


def _write_undistorted_xml(path: Path) -> None:
    """Write undistorted-image export params matching the guide settings."""
    write_rc_xml(
        path=str(path),
        params={
            "fitType":             "innerRegion",   # Fit: Inner Region
            "resolutionType":      "fit",           # Resolution: Fit (original)
            "imageFormat":         "jpg",           # Format: JPEG
            "namingConvention":    "original",      # Naming: Original filename
        },
    )


# ---------------------------------------------------------------------------
# COLMAP helpers
# ---------------------------------------------------------------------------

def _find_colmap() -> str:
    colmap = shutil.which("colmap")
    if colmap:
        return colmap
    win_default = Path(r"C:\Program Files\COLMAP\COLMAP.bat")
    if win_default.exists():
        return str(win_default)
    raise FileNotFoundError(
        "colmap executable not found on PATH. "
        "Install COLMAP and make sure it is on your PATH."
    )


def _run_model_converter(colmap_exe: str, text_dir: Path, bin_dir: Path) -> None:
    cmd = [
        colmap_exe,
        "model_converter",
        "--input_path",  str(text_dir),
        "--output_path", str(bin_dir),
        "--output_type", "BIN",
    ]
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        log.debug("colmap stdout:\n%s", result.stdout.strip())
    if result.returncode != 0:
        log.error("colmap stderr:\n%s", result.stderr.strip())
        raise RuntimeError(
            f"colmap model_converter failed (exit {result.returncode})"
        )


def _validate_text_export(text_dir: Path) -> None:
    missing = []
    for name in COLMAP_TEXT_FILES:
        p = text_dir / name
        if not p.exists() or p.stat().st_size == 0:
            missing.append(name)
    if missing:
        raise RuntimeError(
            f"RC registration export incomplete in '{text_dir}': "
            + ", ".join(missing)
        )


def _validate_binary_output(bin_dir: Path) -> None:
    bad = []
    for name in COLMAP_BIN_FILES:
        p = bin_dir / name
        if not p.exists() or p.stat().st_size == 0:
            bad.append(name)
    if bad:
        raise RuntimeError(
            "colmap model_converter did not produce valid binary files: "
            + ", ".join(bad)
        )
    for name in COLMAP_BIN_FILES:
        log.info("  %-18s  %d bytes", name, (bin_dir / name).stat().st_size)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export COLMAP registration + undistorted images from an open "
            "RealityScan project, then convert to COLMAP binary format."
        )
    )
    parser.add_argument(
        "--rc_exe",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to RealityScan.exe",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        type=Path,
        metavar="DIR",
        help=(
            "Root project output directory. "
            "Undistorted images → <output_dir>/images/, "
            "COLMAP binary     → <output_dir>/colmap/sparse/0/"
        ),
    )
    parser.add_argument(
        "--instance",
        default="*",
        metavar="NAME",
        help="RC instance name to delegate to (default: '*' = first available).",
    )
    parser.add_argument(
        "--keep_text",
        action="store_true",
        help="Keep COLMAP text files alongside the binary files.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    rc_exe     = args.rc_exe.resolve()
    output_dir = args.output_dir.resolve()
    images_dir = output_dir / "images"
    sparse_dir = output_dir / "colmap" / "sparse" / "0"

    if not rc_exe.exists():
        log.error("RealityScan executable not found: %s", rc_exe)
        return 1

    images_dir.mkdir(parents=True, exist_ok=True)
    sparse_dir.mkdir(parents=True, exist_ok=True)

    log.info("RC executable : %s", rc_exe)
    log.info("Output root   : %s", output_dir)
    log.info("RC instance   : %s", args.instance)

    client = RCDelegationClient(str(rc_exe), args.instance)
    client.on_progress = lambda op, pct, elapsed, eta: log.info(
        "  [%s]  %.1f%%  elapsed=%.0fs  eta=%.0fs", op, pct, elapsed, eta
    )

    # ── Startup: clear any queued commands ──────────────────────────────────
    log.info("Clearing RC command queue …")
    client.clear_queue()

    # ── Build XML param files in a temp directory ───────────────────────────
    with tempfile.TemporaryDirectory(prefix="rc_colmap_") as tmp:
        tmp_path         = Path(tmp)
        reg_xml_path     = tmp_path / "export_registration.xml"
        undist_xml_path  = tmp_path / "export_undistorted.xml"
        colmap_text_dir  = tmp_path / "colmap_text"
        colmap_text_dir.mkdir()

        _write_registration_xml(reg_xml_path)
        _write_undistorted_xml(undist_xml_path)

        # ── Step 1: Export COLMAP registration (text) ───────────────────────
        # RC writes cameras.txt / images.txt / points3D.txt to the given path.
        # We give it a path ending in the directory; RC uses it as a prefix.
        reg_output_prefix = str(colmap_text_dir / "export")
        log.info("Step 1/4 — Exporting COLMAP registration …")
        client.delegate("-exportRegistration", reg_output_prefix, str(reg_xml_path))
        client.wait_idle_two_phase("Export COLMAP registration")

        # RC may write directly to colmap_text_dir or use "export_cameras.txt"
        # etc. — normalise whatever it produced to the canonical names.
        _normalise_registration_files(colmap_text_dir)

        log.info("Validating registration export …")
        try:
            _validate_text_export(colmap_text_dir)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1
        log.info("  Registration export OK.")

        # ── Step 2: Export undistorted images ────────────────────────────────
        log.info("Step 2/4 — Exporting undistorted images → %s …", images_dir)
        # RC spelling: exportUndistoredImages (one 't') — this is correct.
        client.delegate(
            "-exportUndistoredImages", str(images_dir), str(undist_xml_path)
        )
        client.wait_idle_two_phase("Export undistorted images")

        jpg_count = len(list(images_dir.glob("*.jpg")))
        if jpg_count == 0:
            log.error("No JPEG files found in %s after export.", images_dir)
            return 1
        log.info("  Exported %d images.", jpg_count)

        # ── Step 3: Convert text → binary ───────────────────────────────────
        log.info("Step 3/4 — Converting COLMAP text → binary …")
        try:
            colmap_exe = _find_colmap()
        except FileNotFoundError as exc:
            log.error("%s", exc)
            return 1

        try:
            _run_model_converter(colmap_exe, colmap_text_dir, sparse_dir)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1

        # ── Step 4: Validate + optional cleanup ──────────────────────────────
        log.info("Step 4/4 — Validating binary output …")
        try:
            _validate_binary_output(sparse_dir)
        except RuntimeError as exc:
            log.error("%s", exc)
            return 1

        if not args.keep_text:
            log.info("Removing COLMAP text files …")
            for name in COLMAP_TEXT_FILES:
                p = sparse_dir / name
                if p.exists():
                    p.unlink()
        # tmp directory (including colmap_text_dir and XML files) auto-deleted
        # when the `with` block exits.

    log.info("Done.")
    log.info("  Undistorted images : %s", images_dir)
    log.info("  COLMAP binary      : %s", sparse_dir)
    return 0


def _normalise_registration_files(directory: Path) -> None:
    """Map whatever RC produced to the canonical COLMAP filenames.

    RC may write the registration into a single file (e.g. ``export.txt``)
    or with prefixed names (``export_cameras.txt`` etc.).  We rename
    them to the names colmap model_converter expects.
    """
    # If the canonical names already exist, nothing to do.
    if all((directory / name).exists() for name in COLMAP_TEXT_FILES):
        return

    # Prefixed pattern: export_cameras.txt, export_images.txt, export_points3D.txt
    mapping = {
        "cameras.txt":  ["export_cameras.txt",  "cameras.txt"],
        "images.txt":   ["export_images.txt",   "images.txt"],
        "points3D.txt": ["export_points3D.txt", "points3D.txt"],
    }
    for canonical, candidates in mapping.items():
        dst = directory / canonical
        if dst.exists():
            continue
        for candidate in candidates:
            src = directory / candidate
            if src.exists():
                src.rename(dst)
                log.debug("Renamed %s → %s", src.name, canonical)
                break


if __name__ == "__main__":
    sys.exit(main())
