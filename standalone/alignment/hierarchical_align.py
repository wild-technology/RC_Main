#!/usr/bin/env python3
"""
Hierarchical Iterative Alignment CLI for RealityScan/RealityCapture.

Automates the two-phase workflow for large-scale photogrammetry projects:
  Phase 1: Align each zone independently, exhaustively consolidate fragmented
           components within each zone into a single master component.
  Phase 2: Iteratively merge zone master components pairwise in geographic
           adjacency order, with escalating retry strategies.

Assumes the user has already prepared data into N overlapping zones via the
batch module (batched_images_by_zone/zone_N/ structure).

Communication with RealityScan uses:
  -delegateTo *     : delegate to first available instance
  -waitCompleted *  : block until operation finishes
  -getStatus *      : query instance status (rev counter, progress, errors)
  -exportReport     : component discovery via $IterateComponents template
"""

from __future__ import annotations

import argparse
import glob
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Report template for RealityScan's reporting system.
# Produces one line per component: GUID|name|camera_count
# ---------------------------------------------------------------------------
COMPONENT_LIST_TEMPLATE = (
    "$IterateComponents($(componentGUID)|$(componentName)|$(componentCamerasCount)\\n)"
)

logger = logging.getLogger("hierarchical_align")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ZoneInfo:
    """Metadata for a single zone discovered from the zones directory."""
    zone_number: int
    zone_dir: str
    image_subfolders: list[str]
    flight_log_path: Optional[str]
    image_count: int
    xmp_count: int
    centroid_x: float = 0.0
    centroid_y: float = 0.0


@dataclass
class ComponentInfo:
    """A single component as reported by RealityScan."""
    guid: str
    name: str
    camera_count: int


@dataclass
class ZoneResult:
    """Result of Phase 1 alignment for a single zone."""
    zone_number: int
    success: bool
    master_component_path: Optional[str] = None
    project_path: Optional[str] = None
    total_images: int = 0
    aligned_cameras: int = 0
    components_after_alignment: int = 0
    components_after_consolidation: int = 0
    discarded_fragments: list[ComponentInfo] = field(default_factory=list)
    merge_attempts_used: int = 0
    warning: Optional[str] = None


@dataclass
class MergeStepResult:
    """Result of a single Phase 2 merge step."""
    step_number: int
    zone_a_label: str
    zone_b_label: str
    success: bool
    output_path: Optional[str] = None
    project_path: Optional[str] = None
    attempt_used: int = 0
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# RCController — manages communication with headless RealityScan
# ---------------------------------------------------------------------------

class RCController:
    """Manages a headless RealityScan instance via -delegateTo * delegation."""

    def __init__(self, rc_exe: str, error_dir: str):
        self.rc_exe = rc_exe
        self.error_dir = error_dir
        os.makedirs(error_dir, exist_ok=True)

    # -- Low-level helpers --------------------------------------------------

    def _run(self, *args: str, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """Run RealityScan.exe with arguments."""
        cmd = [self.rc_exe] + list(args)
        logger.debug("CMD: %s", " ".join(cmd))
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout,
        )

    def delegate(self, *args: str) -> subprocess.CompletedProcess:
        """Send a delegation command: RealityScan.exe -delegateTo * <args>"""
        return self._run("-delegateTo", "*", *args)

    def wait(self) -> subprocess.CompletedProcess:
        """Block until current operation completes: -waitCompleted *"""
        return self._run("-waitCompleted", "*")

    def execute(self, *args: str) -> subprocess.CompletedProcess:
        """Delegate a command and wait for completion.
        This is the primary interface — use for all RC operations.
        """
        result = self.delegate(*args)
        self.wait()
        time.sleep(0.3)  # small settling delay
        return result

    # -- Status and revision tracking ---------------------------------------

    def get_status_raw(self) -> Optional[str]:
        """Get raw status string from RC instance."""
        result = self._run("-getStatus", "*")
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None

    def get_status(self) -> dict:
        """Parse status into dict with keys: rev, progress, lastError, etc."""
        raw = self.get_status_raw()
        parsed = {}
        if not raw:
            return parsed
        for part in raw.split():
            if ":" in part:
                key, value = part.split(":", 1)
                parsed[key] = value
        return parsed

    def get_rev(self) -> Optional[int]:
        """Get current revision counter from status."""
        status = self.get_status()
        rev_str = status.get("rev")
        if rev_str:
            try:
                return int(rev_str)
            except ValueError:
                pass
        return None

    def is_running(self) -> bool:
        """Check if an RC instance is reachable."""
        result = self._run("-getStatus", "*")
        return result.returncode == 0

    # -- Instance lifecycle -------------------------------------------------

    def start(self):
        """Launch headless RC instance if not already running."""
        if self.is_running():
            logger.info("RealityScan instance already running, creating new scene")
            self.execute("-newScene", "-deleteAutosave")
            return

        logger.info("Starting new RealityScan instance (headless)...")
        cmd = [
            self.rc_exe,
            "-headless", "-stdConsole",
            "-silent", self.error_dir,
            "-setInstanceName", "RC1",
            "-set", "appAutoSaveMode=false",
            "-set", "RealityCaptureAutoSaveCliHandling=delete",
            "-set", "RealityCaptureQuitOnError=false",
            "-set", "RealityCaptureProcessActionTime=0",
            "-set", "RealityCaptureProcessAction=ExecuteProgram",
        ]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        # Wait for instance to become reachable
        for _ in range(120):  # up to 2 minutes
            if self.is_running():
                logger.info("RealityScan instance is ready")
                return
            time.sleep(1)

        raise RuntimeError("RealityScan instance did not start within 2 minutes")

    def quit(self):
        """Send quit command to RC instance."""
        try:
            self.delegate("-quit")
            logger.info("RealityScan quit command sent")
        except Exception as e:
            logger.warning("Failed to quit RealityScan: %s", e)

    # -- Component discovery via reporting ----------------------------------

    def list_components(self, report_dir: str) -> list[ComponentInfo]:
        """Use exportReport with $IterateComponents to list all components.

        Falls back to empty list if exportReport fails (e.g., delegation
        limitation).
        """
        os.makedirs(report_dir, exist_ok=True)
        template_file = os.path.join(report_dir, "_comp_template.txt")
        report_file = os.path.join(report_dir, "_comp_report.txt")

        with open(template_file, "w", encoding="utf-8") as f:
            f.write(COMPONENT_LIST_TEMPLATE)

        # Remove stale report
        if os.path.exists(report_file):
            os.remove(report_file)

        self.execute("-exportReport", report_file, template_file)
        time.sleep(0.5)

        if not os.path.exists(report_file):
            logger.warning("exportReport did not produce output file")
            return []

        components = []
        with open(report_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) >= 3:
                    try:
                        cam_count = int(parts[2].strip())
                    except ValueError:
                        cam_count = 0
                    components.append(ComponentInfo(
                        guid=parts[0].strip(),
                        name=parts[1].strip(),
                        camera_count=cam_count,
                    ))
        return components

    def count_components(self, report_dir: str) -> int:
        """Count components in current scene."""
        return len(self.list_components(report_dir))


# ---------------------------------------------------------------------------
# Zone Discovery
# ---------------------------------------------------------------------------

def discover_zones(zones_dir: str) -> list[ZoneInfo]:
    """Scan a batched_images_by_zone/ directory for zone_N subdirectories.

    For each zone, finds:
    - Image subfolders (camupper, cammid, camlower, zeuss, etc.)
    - Flight log file (flight_log*.txt)
    - Image count and XMP sidecar count
    """
    zones = []
    zone_pattern = re.compile(r"zone_(\d+)")

    if not os.path.isdir(zones_dir):
        raise FileNotFoundError(f"Zones directory not found: {zones_dir}")

    for entry in sorted(os.listdir(zones_dir)):
        match = zone_pattern.match(entry)
        if not match:
            continue

        zone_number = int(match.group(1))
        zone_dir = os.path.join(zones_dir, entry)

        if not os.path.isdir(zone_dir):
            continue

        # Find image subfolders (directories containing images)
        image_subfolders = []
        image_count = 0
        xmp_count = 0

        for sub in sorted(os.listdir(zone_dir)):
            sub_path = os.path.join(zone_dir, sub)
            if not os.path.isdir(sub_path):
                continue
            images = [f for f in os.listdir(sub_path)
                      if f.lower().endswith((".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"))]
            xmps = [f for f in os.listdir(sub_path) if f.lower().endswith(".xmp")]
            if images:
                image_subfolders.append(sub_path)
                image_count += len(images)
                xmp_count += len(xmps)

        # Find flight log
        flight_log_path = None
        for f in os.listdir(zone_dir):
            if f.startswith("flight_log") and f.endswith(".txt"):
                flight_log_path = os.path.join(zone_dir, f)
                break

        if image_count == 0:
            logger.warning("Zone %d has no images, skipping", zone_number)
            continue

        zones.append(ZoneInfo(
            zone_number=zone_number,
            zone_dir=zone_dir,
            image_subfolders=image_subfolders,
            flight_log_path=flight_log_path,
            image_count=image_count,
            xmp_count=xmp_count,
        ))

    if not zones:
        raise ValueError(f"No valid zones found in {zones_dir}")

    logger.info("Discovered %d zones with %d total images",
                len(zones), sum(z.image_count for z in zones))
    return zones


def compute_zone_centroids(zones: list[ZoneInfo]) -> list[ZoneInfo]:
    """Parse each zone's flight log and compute the mean (X, Y) centroid.

    Flight log format: semicolon-delimited CSV with columns containing
    'x' or 'east' for easting and 'y' or 'north' for northing.
    """
    for zone in zones:
        if not zone.flight_log_path or not os.path.isfile(zone.flight_log_path):
            logger.warning("Zone %d has no flight log, using (0, 0) centroid",
                           zone.zone_number)
            continue

        with open(zone.flight_log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if len(lines) < 2:
            logger.warning("Zone %d flight log is empty", zone.zone_number)
            continue

        # Parse header to find X and Y columns
        header = lines[0].strip().split(";")
        header_lower = [h.strip().lower() for h in header]

        x_col = None
        y_col = None
        for i, h in enumerate(header_lower):
            if x_col is None and ("x" in h or "east" in h):
                x_col = i
            if y_col is None and ("y" in h or "north" in h):
                y_col = i

        if x_col is None or y_col is None:
            logger.warning("Zone %d: could not find X/Y columns in flight log header: %s",
                           zone.zone_number, header)
            continue

        xs, ys = [], []
        for line in lines[1:]:
            parts = line.strip().split(";")
            if len(parts) <= max(x_col, y_col):
                continue
            try:
                xs.append(float(parts[x_col].strip()))
                ys.append(float(parts[y_col].strip()))
            except ValueError:
                continue

        if xs and ys:
            zone.centroid_x = sum(xs) / len(xs)
            zone.centroid_y = sum(ys) / len(ys)
            logger.debug("Zone %d centroid: (%.1f, %.1f)",
                         zone.zone_number, zone.centroid_x, zone.centroid_y)
        else:
            logger.warning("Zone %d: no valid coordinates in flight log",
                           zone.zone_number)

    return zones


def compute_merge_order(zones: list[ZoneInfo]) -> list[int]:
    """Determine optimal merge order via nearest-neighbor traversal.

    Starts from the zone closest to the global centroid and greedily visits
    the nearest unvisited zone. Returns ordered list of zone indices into
    the zones list.
    """
    if len(zones) <= 1:
        return list(range(len(zones)))

    # Global centroid
    cx = sum(z.centroid_x for z in zones) / len(zones)
    cy = sum(z.centroid_y for z in zones) / len(zones)

    def dist(i: int, x: float, y: float) -> float:
        return math.hypot(zones[i].centroid_x - x, zones[i].centroid_y - y)

    remaining = list(range(len(zones)))
    start = min(remaining, key=lambda i: dist(i, cx, cy))
    order = [start]
    remaining.remove(start)

    while remaining:
        last = order[-1]
        nearest = min(remaining, key=lambda i: dist(
            i, zones[last].centroid_x, zones[last].centroid_y))
        order.append(nearest)
        remaining.remove(nearest)

    return order


# ---------------------------------------------------------------------------
# Phase 1: Zone Alignment & Consolidation
# ---------------------------------------------------------------------------

def consolidate_zone(
    rc: RCController,
    report_dir: str,
    min_cameras: int = 5,
    warn_threshold: float = 0.9,
) -> tuple[list[ComponentInfo], list[ComponentInfo], int]:
    """Exhaustively attempt to merge fragmented components within a zone.

    Returns:
        (final_components, discarded_fragments, attempts_used)
    """
    components = rc.list_components(report_dir)
    if len(components) <= 1:
        return components, [], 0

    total_cameras = sum(c.camera_count for c in components)
    logger.info("  %d components after initial alignment (%d total cameras)",
                len(components), total_cameras)

    # Attempt 1: Standard merge
    logger.info("  Consolidation attempt 1: mergeComponents")
    rc.execute("-mergeComponents")
    components = rc.list_components(report_dir)
    if len(components) <= 1:
        return components, [], 1

    # Attempt 2: Re-align (RC uses merge-oriented algorithms on re-align)
    logger.info("  Consolidation attempt 2: re-align + mergeComponents")
    rc.execute("-align")
    rc.execute("-mergeComponents")
    components = rc.list_components(report_dir)
    if len(components) <= 1:
        return components, [], 2

    # Attempt 3: Aggressive settings
    logger.info("  Consolidation attempt 3: aggressive re-align")
    rc.execute("-set", "sfmForceComponentRematch=true")
    rc.execute("-set", "sfmImagesOverlap=Low")
    rc.execute("-align")
    rc.execute("-mergeComponents")
    rc.execute("-set", "sfmForceComponentRematch=false")  # reset
    components = rc.list_components(report_dir)
    if len(components) <= 1:
        return components, [], 3

    # Attempt 4: Keep maximal, discard small fragments
    logger.info("  Consolidation: %d components remain after all merge attempts",
                len(components))

    # Sort by camera count descending
    components.sort(key=lambda c: c.camera_count, reverse=True)
    maximal = components[0]
    fragments = [c for c in components[1:] if c.camera_count >= min_cameras]
    discarded = [c for c in components[1:] if c.camera_count < min_cameras]

    # Also discard fragments that are small relative to maximal
    kept_fragments = []
    for frag in fragments:
        discarded.append(frag)

    # Warn if maximal doesn't have enough of the total cameras
    if total_cameras > 0:
        ratio = maximal.camera_count / total_cameras
        if ratio < warn_threshold:
            logger.warning(
                "  WARNING: Maximal component has only %.0f%% of aligned cameras "
                "(%d/%d). Discarded fragments may contain important data.",
                ratio * 100, maximal.camera_count, total_cameras
            )

    for d in discarded:
        logger.info("    Discarding: %s (%d cameras)", d.name, d.camera_count)

    return [maximal], discarded, 4


def align_zone(
    rc: RCController,
    zone: ZoneInfo,
    output_dir: str,
    report_dir: str,
    alignment_params: Optional[str],
    flight_log_params: Optional[str],
    min_cameras: int = 5,
    warn_threshold: float = 0.9,
) -> ZoneResult:
    """Align a single zone's images and consolidate into a master component.

    Steps:
    1. New scene
    2. Add image folders (XMP sidecars auto-loaded)
    3. Import flight log
    4. Run alignment
    5. Consolidate (exhaustive merge attempts)
    6. Export master component + raw components
    7. Save project
    """
    zone_output = os.path.join(output_dir, f"zone_{zone.zone_number}")
    raw_components_dir = os.path.join(zone_output, "raw_components")
    master_path = os.path.join(zone_output, f"zone_{zone.zone_number}_master.rsalign")
    project_path = os.path.join(zone_output, f"zone_{zone.zone_number}.rcproj")
    os.makedirs(zone_output, exist_ok=True)
    os.makedirs(raw_components_dir, exist_ok=True)

    result = ZoneResult(
        zone_number=zone.zone_number,
        success=False,
        total_images=zone.image_count,
    )

    try:
        # 1. New scene
        rc.execute("-newScene")

        # 2. Add image folders
        for subfolder in zone.image_subfolders:
            logger.info("  Adding images from: %s", os.path.basename(subfolder))
            rc.execute("-addFolder", subfolder)

        # 3. Import flight log
        if zone.flight_log_path and flight_log_params:
            logger.info("  Importing flight log: %s", os.path.basename(zone.flight_log_path))
            rc.execute("-importFlightLog", zone.flight_log_path, flight_log_params)
        elif zone.flight_log_path:
            logger.warning("  Flight log found but no params file specified, skipping import")

        # 4. Align
        logger.info("  Running alignment...")
        if alignment_params:
            rc.execute("-align", alignment_params)
        else:
            rc.execute("-align")

        # Check initial component count
        components = rc.list_components(report_dir)
        result.components_after_alignment = len(components)
        result.aligned_cameras = sum(c.camera_count for c in components)
        logger.info("  Alignment produced %d component(s), %d cameras aligned",
                     len(components), result.aligned_cameras)

        if not components:
            logger.error("  No components produced — alignment failed entirely")
            result.warning = "Alignment produced zero components"
            return result

        # 5. Consolidate
        final_components, discarded, attempts = consolidate_zone(
            rc, report_dir, min_cameras, warn_threshold
        )
        result.components_after_consolidation = len(final_components)
        result.discarded_fragments = discarded
        result.merge_attempts_used = attempts

        if final_components:
            result.aligned_cameras = final_components[0].camera_count

        # 6. Export raw components (all, for archival)
        all_components = rc.list_components(report_dir)
        for comp in all_components:
            rc.execute("-selectComponent", comp.name)
            rc.execute("-exportSelectedComponentDir", raw_components_dir)

        # 7. Export master component
        rc.execute("-selectMaximalComponent")
        rc.execute("-exportSelectedComponentFile", master_path)

        if os.path.exists(master_path) and os.path.getsize(master_path) > 0:
            result.success = True
            result.master_component_path = master_path
        else:
            logger.error("  Master component file not created")
            result.warning = "Export failed — master component file not created"

        # 8. Save project
        rc.execute("-save", project_path)
        result.project_path = project_path

        # Warn about discarded fragments
        if discarded:
            disc_cameras = sum(d.camera_count for d in discarded)
            total = result.aligned_cameras + disc_cameras
            if total > 0:
                pct = result.aligned_cameras / total * 100
                if pct < warn_threshold * 100:
                    result.warning = (
                        f"Maximal component has {pct:.0f}% of aligned cameras. "
                        f"{len(discarded)} fragment(s) discarded ({disc_cameras} cameras). "
                        f"Review raw_components/ — manual control points may be needed."
                    )

    except Exception as e:
        logger.error("  Zone %d alignment failed: %s", zone.zone_number, e)
        result.warning = str(e)

    return result


def run_phase1(
    rc: RCController,
    zones: list[ZoneInfo],
    output_dir: str,
    report_dir: str,
    alignment_params: Optional[str],
    flight_log_params: Optional[str],
    min_cameras: int = 5,
    warn_threshold: float = 0.9,
) -> list[ZoneResult]:
    """Align ALL zones sequentially. Phase 2 does not begin until every zone
    has a master component exported.

    Supports resume: skips zones whose master component already exists.
    """
    results = []

    for i, zone in enumerate(zones):
        zone_label = f"zone_{zone.zone_number}"
        master_path = os.path.join(output_dir, zone_label, f"{zone_label}_master.rsalign")

        logger.info("=" * 60)
        logger.info("[Phase 1] Zone %d/%d (zone_%d, %d images)",
                     i + 1, len(zones), zone.zone_number, zone.image_count)

        # Resume: skip if master already exists
        if os.path.exists(master_path) and os.path.getsize(master_path) > 0:
            logger.info("  SKIP — master component already exists: %s", master_path)
            results.append(ZoneResult(
                zone_number=zone.zone_number,
                success=True,
                master_component_path=master_path,
                total_images=zone.image_count,
            ))
            continue

        result = align_zone(
            rc, zone, output_dir, report_dir,
            alignment_params, flight_log_params,
            min_cameras, warn_threshold,
        )
        results.append(result)

        if result.success:
            logger.info("  Zone %d: SUCCESS (%d cameras, %d merge attempts, %d discarded)",
                         zone.zone_number, result.aligned_cameras,
                         result.merge_attempts_used, len(result.discarded_fragments))
        else:
            logger.error("  Zone %d: FAILED — %s",
                          zone.zone_number, result.warning or "unknown error")

        if result.warning:
            logger.warning("  Zone %d WARNING: %s", zone.zone_number, result.warning)

    # Summary
    succeeded = sum(1 for r in results if r.success)
    logger.info("=" * 60)
    logger.info("[Phase 1] Complete: %d/%d zones aligned successfully", succeeded, len(zones))

    return results


# ---------------------------------------------------------------------------
# Phase 2: Iterative Cross-Zone Merge
# ---------------------------------------------------------------------------

def merge_two_components(
    rc: RCController,
    comp_a_path: str,
    comp_b_path: str,
    output_path: str,
    project_path: str,
    report_dir: str,
    default_feature_source: int = 1,
) -> MergeStepResult:
    """Merge two .rsalign component files with 3-attempt escalation.

    Attempt 1: Component features (fastest, lowest RAM)
    Attempt 2: Merge using overlaps (moderate RAM)
    Attempt 3: Aggressive re-align with force rematch (highest RAM)
    """
    result = MergeStepResult(
        step_number=0,
        zone_a_label=os.path.basename(comp_a_path),
        zone_b_label=os.path.basename(comp_b_path),
        success=False,
    )

    attempts = [
        {
            "name": "component features",
            "feature_source": "1",
            "method": "merge",
            "aggressive": False,
        },
        {
            "name": "merge using overlaps",
            "feature_source": "0",
            "method": "merge",
            "aggressive": False,
        },
        {
            "name": "aggressive re-align + merge",
            "feature_source": "0",
            "method": "align",
            "aggressive": True,
        },
    ]

    for attempt_num, attempt in enumerate(attempts, 1):
        logger.info("    Attempt %d/3: %s", attempt_num, attempt["name"])

        try:
            # Fresh scene
            rc.execute("-newScene")

            # Import both components
            rc.execute("-importComponent", comp_a_path)
            rc.execute("-importComponent", comp_b_path)

            # Set feature source
            rc.execute("-selectAllImages")
            rc.execute("-setFeatureSource", attempt["feature_source"])

            # Aggressive settings
            if attempt["aggressive"]:
                rc.execute("-set", "sfmForceComponentRematch=true")
                rc.execute("-set", "sfmImagesOverlap=Low")

            # Merge method
            if attempt["method"] == "align":
                rc.execute("-align")
                rc.execute("-mergeComponents")
            else:
                rc.execute("-mergeComponents")

            # Reset aggressive settings
            if attempt["aggressive"]:
                rc.execute("-set", "sfmForceComponentRematch=false")

            # Check result
            components = rc.list_components(report_dir)
            if len(components) == 1:
                logger.info("    Attempt %d: SUCCESS — single component", attempt_num)
            elif len(components) > 1:
                # Check if largest has substantially all cameras
                components.sort(key=lambda c: c.camera_count, reverse=True)
                total = sum(c.camera_count for c in components)
                if total > 0 and components[0].camera_count / total > 0.95:
                    logger.info("    Attempt %d: SUCCESS — maximal has %.0f%% of cameras",
                                 attempt_num, components[0].camera_count / total * 100)
                else:
                    logger.info("    Attempt %d: FAILED — %d components remain",
                                 attempt_num, len(components))
                    continue
            else:
                logger.info("    Attempt %d: FAILED — no components", attempt_num)
                continue

            # Export merged result
            rc.execute("-selectMaximalComponent")
            rc.execute("-exportSelectedComponentFile", output_path)

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                rc.execute("-save", project_path)
                result.success = True
                result.output_path = output_path
                result.project_path = project_path
                result.attempt_used = attempt_num
                return result
            else:
                logger.warning("    Export failed — file not created")
                continue

        except Exception as e:
            logger.error("    Attempt %d error: %s", attempt_num, e)
            continue

    result.error_message = "All 3 merge attempts failed"
    return result


def run_phase2(
    rc: RCController,
    zone_results: list[ZoneResult],
    merge_order: list[int],
    zones: list[ZoneInfo],
    output_dir: str,
    report_dir: str,
    default_feature_source: int = 1,
) -> list[MergeStepResult]:
    """Iteratively merge zone master components pairwise in geographic order.

    Supports resume: skips steps whose output .rsalign already exists.
    """
    merge_stages_dir = os.path.join(output_dir, "merge_stages")
    final_dir = os.path.join(output_dir, "final")
    os.makedirs(merge_stages_dir, exist_ok=True)
    os.makedirs(final_dir, exist_ok=True)

    # Build ordered list of zone master component paths
    ordered_results = [zone_results[i] for i in merge_order]
    ordered_zones = [zones[i] for i in merge_order]

    # Filter to only successful zones
    valid = [(r, z) for r, z in zip(ordered_results, ordered_zones) if r.success and r.master_component_path]
    if len(valid) < 2:
        logger.error("Need at least 2 successful zones to merge, have %d", len(valid))
        return []

    merge_results = []
    current_component = valid[0][0].master_component_path
    current_label = f"zone_{valid[0][1].zone_number}"

    logger.info("=" * 60)
    logger.info("[Phase 2] Merging %d zones in order: %s",
                 len(valid),
                 " -> ".join(f"zone_{z.zone_number}" for _, z in valid))

    for step_num, (zone_result, zone_info) in enumerate(valid[1:], 1):
        next_component = zone_result.master_component_path
        next_label = f"zone_{zone_info.zone_number}"
        merged_label = f"{current_label}_{zone_info.zone_number}"

        output_path = os.path.join(
            merge_stages_dir,
            f"step_{step_num:02d}_{merged_label}.rsalign"
        )
        project_path = os.path.join(
            merge_stages_dir,
            f"step_{step_num:02d}_{merged_label}.rcproj"
        )

        logger.info("-" * 60)
        logger.info("[Phase 2] Step %d/%d: Merging %s + %s",
                     step_num, len(valid) - 1, current_label, next_label)

        # Resume: skip if output already exists
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info("  SKIP — output already exists: %s", output_path)
            merge_results.append(MergeStepResult(
                step_number=step_num,
                zone_a_label=current_label,
                zone_b_label=next_label,
                success=True,
                output_path=output_path,
                project_path=project_path,
            ))
            current_component = output_path
            current_label = merged_label
            continue

        step_result = merge_two_components(
            rc, current_component, next_component,
            output_path, project_path, report_dir,
            default_feature_source,
        )
        step_result.step_number = step_num
        step_result.zone_a_label = current_label
        step_result.zone_b_label = next_label
        merge_results.append(step_result)

        if step_result.success:
            logger.info("  Step %d: SUCCESS (attempt %d)",
                         step_num, step_result.attempt_used)
            current_component = step_result.output_path
            current_label = merged_label
        else:
            logger.error("  Step %d: FAILED — %s",
                          step_num, step_result.error_message)
            logger.info("  Continuing with current component for remaining zones")
            # Don't update current_component — skip this zone and try next

    # Copy final result
    final_path = os.path.join(final_dir, "unified_master.rsalign")
    if current_component and os.path.exists(current_component):
        shutil.copy2(current_component, final_path)
        logger.info("Final unified component: %s", final_path)
    else:
        logger.error("No final component to export")

    return merge_results


# ---------------------------------------------------------------------------
# Summary Report
# ---------------------------------------------------------------------------

def print_summary(
    zones: list[ZoneInfo],
    zone_results: list[ZoneResult],
    merge_order: list[int],
    merge_results: list[MergeStepResult],
):
    """Print a structured summary of the entire workflow."""
    lines = [
        "",
        "=" * 70,
        "HIERARCHICAL ALIGNMENT SUMMARY",
        "=" * 70,
        "",
        "--- Phase 1: Zone Alignment ---",
        f"{'Zone':<10} {'Images':<10} {'Aligned':<10} {'Components':<12} {'Attempts':<10} {'Status':<10}",
        "-" * 70,
    ]

    for r in zone_results:
        status = "OK" if r.success else "FAILED"
        lines.append(
            f"zone_{r.zone_number:<5} {r.total_images:<10} {r.aligned_cameras:<10} "
            f"{r.components_after_alignment:<12} {r.merge_attempts_used:<10} {status}"
        )
        if r.discarded_fragments:
            for d in r.discarded_fragments:
                lines.append(f"  -> discarded: {d.name} ({d.camera_count} cameras)")
        if r.warning:
            lines.append(f"  ** {r.warning}")

    succeeded_zones = sum(1 for r in zone_results if r.success)
    lines.append(f"\nPhase 1 result: {succeeded_zones}/{len(zone_results)} zones succeeded")

    if merge_results:
        lines.extend([
            "",
            "--- Phase 2: Cross-Zone Merge ---",
            f"Merge order: {' -> '.join(f'zone_{zones[i].zone_number}' for i in merge_order)}",
            "",
            f"{'Step':<8} {'Zones':<30} {'Attempt':<10} {'Status':<10}",
            "-" * 70,
        ])

        for r in merge_results:
            status = "OK" if r.success else "FAILED"
            zones_label = f"{r.zone_a_label} + {r.zone_b_label}"
            lines.append(f"{r.step_number:<8} {zones_label:<30} {r.attempt_used:<10} {status}")
            if r.error_message:
                lines.append(f"  ** {r.error_message}")

        succeeded_merges = sum(1 for r in merge_results if r.success)
        retried = sum(1 for r in merge_results if r.success and r.attempt_used > 1)
        lines.append(f"\nPhase 2 result: {succeeded_merges}/{len(merge_results)} merges succeeded")
        if retried:
            lines.append(f"  ({retried} required retry with escalated settings)")

        failed = [r for r in merge_results if not r.success]
        if failed:
            lines.append("\nFailed merges (manual control points needed):")
            for r in failed:
                lines.append(f"  {r.zone_a_label} + {r.zone_b_label}")

    lines.extend(["", "=" * 70])

    summary = "\n".join(lines)
    print(summary)
    logger.info(summary)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_default_metadata_dir() -> Optional[str]:
    """Try to locate the RC_CLI/Metadata directory relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Walk up to RC_Main root
    for parent in [script_dir, os.path.dirname(script_dir), os.path.dirname(os.path.dirname(script_dir))]:
        candidate = os.path.join(parent, "modules", "realitycapture_interface", "RC_CLI", "Metadata")
        if os.path.isdir(candidate):
            return candidate
    return None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    default_rc_exe = os.environ.get(
        "RC_EXE",
        r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"
    )

    metadata_dir = find_default_metadata_dir()
    default_alignment = os.path.join(metadata_dir, "AlignmentParams.xml") if metadata_dir else None
    default_flightlog = os.path.join(metadata_dir, "FlightLogParams.xml") if metadata_dir else None

    parser = argparse.ArgumentParser(
        description="Hierarchical Iterative Alignment for RealityScan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full workflow: align all zones then merge
  python hierarchical_align.py --zones-dir D:\\project\\batched_images_by_zone --output-dir D:\\project\\alignment

  # Resume from Phase 2 (zones already aligned)
  python hierarchical_align.py --zones-dir D:\\project\\batched_images_by_zone --output-dir D:\\project\\alignment --skip-zone-align

  # Only align zones, skip cross-zone merge
  python hierarchical_align.py --zones-dir D:\\project\\batched_images_by_zone --output-dir D:\\project\\alignment --skip-merge
        """,
    )

    parser.add_argument("--zones-dir", required=True,
                        help="Path to batched_images_by_zone/ directory")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for components, projects, and logs")
    parser.add_argument("--rc-exe", default=default_rc_exe,
                        help=f"Path to RealityScan.exe (default: {default_rc_exe})")
    parser.add_argument("--alignment-params", default=default_alignment,
                        help="Path to AlignmentParams.xml")
    parser.add_argument("--flight-log-params", default=default_flightlog,
                        help="Path to FlightLogParams.xml")
    parser.add_argument("--skip-zone-align", action="store_true",
                        help="Skip Phase 1 (resume from Phase 2)")
    parser.add_argument("--skip-merge", action="store_true",
                        help="Skip Phase 2 (only do Phase 1 zone alignment)")
    parser.add_argument("--feature-source", type=int, default=1, choices=[0, 1, 2],
                        help="Default feature source for cross-zone merge (0=overlaps, 1=component, 2=all)")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="Max retry attempts per merge step (default: 3)")
    parser.add_argument("--min-component-cameras", type=int, default=5,
                        help="Discard zone components with fewer cameras (default: 5)")
    parser.add_argument("--warn-discard-threshold", type=float, default=0.9,
                        help="Warn if maximal component has less than this fraction of cameras (default: 0.9)")

    return parser.parse_args()


def setup_logging(output_dir: str):
    """Configure logging to both file and console."""
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "hierarchical_align.log")

    # File handler — detailed
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))

    # Console handler — info level
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger("hierarchical_align")
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    logger.info("Logging to: %s", log_file)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    setup_logging(args.output_dir)

    # Directories
    zone_components_dir = os.path.join(args.output_dir, "zone_components")
    report_dir = os.path.join(args.output_dir, "reports")
    error_dir = os.path.join(args.output_dir, "rc_errors")
    os.makedirs(zone_components_dir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)

    # Discover zones
    logger.info("Discovering zones in: %s", args.zones_dir)
    zones = discover_zones(args.zones_dir)
    zones = compute_zone_centroids(zones)
    merge_order = compute_merge_order(zones)

    logger.info("Merge order (geographic adjacency): %s",
                 " -> ".join(f"zone_{zones[i].zone_number}" for i in merge_order))
    for i, idx in enumerate(merge_order):
        z = zones[idx]
        logger.info("  %d. zone_%d (%d images, centroid: %.1f, %.1f)",
                     i + 1, z.zone_number, z.image_count, z.centroid_x, z.centroid_y)

    # Initialize RC
    rc = RCController(args.rc_exe, error_dir)

    zone_results = []
    merge_results = []

    try:
        rc.start()

        # Phase 1: Align each zone
        if not args.skip_zone_align:
            zone_results = run_phase1(
                rc, zones, zone_components_dir, report_dir,
                args.alignment_params, args.flight_log_params,
                args.min_component_cameras, args.warn_discard_threshold,
            )
        else:
            # Load existing zone results for Phase 2
            logger.info("Skipping Phase 1 — loading existing zone master components")
            for zone in zones:
                master_path = os.path.join(
                    zone_components_dir, f"zone_{zone.zone_number}",
                    f"zone_{zone.zone_number}_master.rsalign"
                )
                zone_results.append(ZoneResult(
                    zone_number=zone.zone_number,
                    success=os.path.exists(master_path),
                    master_component_path=master_path if os.path.exists(master_path) else None,
                    total_images=zone.image_count,
                ))

        # Phase 2: Cross-zone merge
        if not args.skip_merge:
            # Check all zones succeeded before proceeding
            failed_zones = [r for r in zone_results if not r.success]
            if failed_zones:
                logger.warning(
                    "Phase 2: %d zone(s) failed alignment. "
                    "Proceeding with %d successful zone(s).",
                    len(failed_zones), len(zone_results) - len(failed_zones)
                )

            merge_results = run_phase2(
                rc, zone_results, merge_order, zones,
                args.output_dir, report_dir,
                args.feature_source,
            )
        else:
            logger.info("Skipping Phase 2 (--skip-merge)")

        # Summary
        print_summary(zones, zone_results, merge_order, merge_results)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
    finally:
        rc.quit()


if __name__ == "__main__":
    main()
