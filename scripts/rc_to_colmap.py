#!/usr/bin/env python3
"""
rc_to_colmap.py — Convert a RealityCapture COLMAP text export to COLMAP binary format.

Workflow:
  1. Verify the three RC text files exist (cameras.txt, images.txt, points3D.txt)
  2. Copy them into <output_dir>/sparse/0/
  3. Run `colmap model_converter` to produce binary files (.bin)
  4. Validate that all three binary files were created and are non-empty
  5. Remove the text originals from the output tree

Usage (Windows):
  python scripts/rc_to_colmap.py ^
      --rc_export_dir C:\\temp\\rc_colmap_export ^
      --output_dir    C:\\Users\\WildTech\\Desktop\\H2103d_Northampton\\colmap

Usage (Linux / testing):
  python scripts/rc_to_colmap.py \\
      --rc_export_dir /tmp/rc_colmap_export \\
      --output_dir    /tmp/h2103d_northampton/colmap
"""

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Files produced by RC's COLMAP text export
COLMAP_TEXT_FILES = ("cameras.txt", "images.txt", "points3D.txt")
# Corresponding binary files produced by colmap model_converter
COLMAP_BIN_FILES  = ("cameras.bin", "images.bin", "points3D.bin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_colmap() -> str:
    """Return the colmap executable path, or raise if not found."""
    colmap = shutil.which("colmap")
    if colmap:
        return colmap
    # Common Windows install location
    win_default = Path(r"C:\Program Files\COLMAP\COLMAP.bat")
    if win_default.exists():
        return str(win_default)
    raise FileNotFoundError(
        "colmap executable not found on PATH. "
        "Install COLMAP and ensure it is on your PATH."
    )


def verify_rc_export(rc_dir: Path) -> None:
    """Raise if any expected RC text file is missing or empty."""
    missing = []
    for name in COLMAP_TEXT_FILES:
        p = rc_dir / name
        if not p.exists():
            missing.append(name)
        elif p.stat().st_size == 0:
            missing.append(f"{name} (empty)")
    if missing:
        raise FileNotFoundError(
            f"RC export directory '{rc_dir}' is missing or has empty files: "
            + ", ".join(missing)
        )


def copy_text_files(rc_dir: Path, sparse_dir: Path) -> None:
    """Copy the three text files into sparse_dir."""
    sparse_dir.mkdir(parents=True, exist_ok=True)
    for name in COLMAP_TEXT_FILES:
        src = rc_dir / name
        dst = sparse_dir / name
        shutil.copy2(src, dst)
        log.info("Copied  %s  →  %s", src, dst)


def run_model_converter(colmap_exe: str, sparse_dir: Path) -> None:
    """Run colmap model_converter on sparse_dir (text → binary in-place)."""
    cmd = [
        colmap_exe,
        "model_converter",
        "--input_path",  str(sparse_dir),
        "--output_path", str(sparse_dir),
        "--output_type", "BIN",
    ]
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        log.debug("colmap stdout:\n%s", result.stdout.strip())
    if result.returncode != 0:
        log.error("colmap stderr:\n%s", result.stderr.strip())
        raise RuntimeError(
            f"colmap model_converter failed (exit {result.returncode}). "
            "See stderr above."
        )


def validate_binary_output(sparse_dir: Path) -> None:
    """Raise if any expected binary file is missing or empty."""
    bad = []
    for name in COLMAP_BIN_FILES:
        p = sparse_dir / name
        if not p.exists():
            bad.append(f"{name} (missing)")
        elif p.stat().st_size == 0:
            bad.append(f"{name} (empty)")
    if bad:
        raise RuntimeError(
            "model_converter did not produce valid binary files: "
            + ", ".join(bad)
        )
    for name in COLMAP_BIN_FILES:
        p = sparse_dir / name
        log.info("Validated  %-18s  (%d bytes)", name, p.stat().st_size)


def remove_text_files(sparse_dir: Path) -> None:
    """Delete the text originals that were copied into sparse_dir."""
    for name in COLMAP_TEXT_FILES:
        p = sparse_dir / name
        if p.exists():
            p.unlink()
            log.info("Removed text file  %s", p)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a RealityCapture COLMAP text export into COLMAP binary "
            "format ready for NeRF / Gaussian Splatting pipelines."
        )
    )
    parser.add_argument(
        "--rc_export_dir",
        required=True,
        type=Path,
        metavar="DIR",
        help=(
            "Folder containing cameras.txt, images.txt, points3D.txt "
            "exported from RealityCapture."
        ),
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        type=Path,
        metavar="DIR",
        help=(
            "Root output directory. Binary files are written to "
            "<output_dir>/sparse/0/."
        ),
    )
    parser.add_argument(
        "--keep_text",
        action="store_true",
        help="Keep the text files alongside the binary files (default: remove them).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show colmap output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    rc_dir    = args.rc_export_dir.resolve()
    sparse_dir = args.output_dir.resolve() / "sparse" / "0"

    log.info("RC export dir : %s", rc_dir)
    log.info("Output sparse : %s", sparse_dir)

    # 1. Verify RC export
    log.info("Step 1/4 — Verifying RC export …")
    try:
        verify_rc_export(rc_dir)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    log.info("  All three text files present.")

    # 2. Copy text files
    log.info("Step 2/4 — Copying text files …")
    copy_text_files(rc_dir, sparse_dir)

    # 3. Convert to binary
    log.info("Step 3/4 — Converting to COLMAP binary format …")
    try:
        colmap_exe = find_colmap()
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1

    try:
        run_model_converter(colmap_exe, sparse_dir)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    # 4. Validate binary output
    log.info("Step 4/4 — Validating binary output …")
    try:
        validate_binary_output(sparse_dir)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    # 5. Clean up text files (unless --keep_text)
    if not args.keep_text:
        log.info("Removing text originals …")
        remove_text_files(sparse_dir)

    log.info("Done. COLMAP sparse model at: %s", sparse_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
