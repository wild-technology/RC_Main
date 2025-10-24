#!/usr/bin/env python3
"""
Interactive RealityCapture/RealityScan launcher (stand‑alone)

This script guides a user through a structured workflow:
- Ask for base image folder (with subfolders), Expedition (e.g., NA173), Dive (e.g., H2104d), and Zone (e.g., 1,2,3)
- Build a project filename Expedition_Dive_Zone and save into the base directory
- Load images, load optional flight log
- Set image parameters (stub for future)
- Align images with user‑specified options; produce a summary
- Rename components sequentially and export to Alignments/InitialAlignments
- Merge components with user‑specified options
- Rename merged components sequentially and export to Alignments/MergedAlignments

Notes
- Requires RealityCapture.exe or RealityScan.exe to be installed.
- The CLI command semantics are documented in the project docs included with this repository.
- This script is intentionally self‑contained and does not depend on other project modules to run.
"""
from __future__ import annotations

import os
import sys
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, Sequence

# --------------- helpers ---------------

def find_rc_executable() -> Optional[Path]:
    """
    Try to find RealityCapture/RealityScan executable in common locations.
    Returns first existing path or None.
    """
    candidates = [
        Path(r"C:\\Program Files\\Epic Games\\RealityScan_2.0\\RealityScan.exe"),
        Path(r"C:\\Program Files\\Epic Games\\RealityScan\\RealityScan.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def find_flightlog_params_xml() -> Optional[Path]:
    """Locate FlightLogParams.xml in the repository (Metadata folder)."""
    try:
        repo_root = Path(__file__).resolve().parent.parent
        cand = repo_root / "modules" / "realitycapture_interface" / "RC_CLI" / "Metadata" / "FlightLogParams.xml"
        if cand.exists():
            return cand
    except Exception:
        pass
    return None


def get_flightlog_format_id() -> tuple[Optional[str], str]:
    """
    Read gpsLogFileFormat GUID from FlightLogParams.xml.
    Returns (guid, source): source is 'xml' or 'fallback' or 'none'.
    """
    from xml.etree import ElementTree as ET
    xml_path = find_flightlog_params_xml()
    if xml_path is not None:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for entry in root.findall("entry"):
                if entry.get("key") == "gpsLogFileFormat":
                    val = entry.get("value")
                    if val:
                        return val, f"xml:{xml_path}"
        except Exception:
            pass
    # Fallback to known GUID requested by user/story
    fallback = "{B438A617-2434-5A24-C1B7-58980F28345A}"
    return fallback, "fallback"


def run_rc_command(rc_exe: Path, args: Sequence[str], display_output: bool = False) -> None:
    """Run RealityCapture/RealityScan with provided CLI args."""
    cmd = [str(rc_exe)] + list(args)
    if display_output:
        print("\nLaunching:")
        print(" ", " ".join(cmd))
    # Use subprocess.run to wait for completion
    completed = subprocess.run(cmd, capture_output=not display_output, text=True)
    if completed.returncode != 0:
        # Always echo outputs for diagnostics on failure
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        raise subprocess.CalledProcessError(completed.returncode, cmd, completed.stdout, completed.stderr)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = input(f"{question} ({d}): ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"): return True
        if ans in ("n", "no"): return False
        print("Please enter y or n.")


def prompt_int(question: str, default: int) -> int:
    while True:
        ans = input(f"{question} [{default}]: ").strip()
        if not ans:
            return default
        try:
            return int(ans)
        except ValueError:
            print("Please enter an integer.")


def build_project_name(expedition: str, dive: str, zone: str) -> str:
    return f"{expedition}_{dive}_{zone}"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def prompt_overwrite_strategy(target_dir: Path, label: str, auto: bool = False) -> tuple[Optional[Path], str]:
    """
    Ask the user how to handle potential overwrites in a target directory.
    Returns (export_dir, action):
      - action is one of 'overwrite', 'subfolder', 'cancel'
      - export_dir is the directory to use (target_dir or a timestamped subfolder) or None if cancel
    If auto=True, it will silently choose to overwrite in the target directory, creating it if needed.
    """
    if auto:
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir, 'overwrite'

    existing = any(target_dir.iterdir()) if target_dir.exists() else False
    if not existing:
        return target_dir, 'overwrite'

    print(f"Warning: '{label}' directory is not empty: {target_dir}")
    print("Choose how to proceed:")
    print("  1) Overwrite existing files in this directory")
    print("  2) Create a new timestamped subfolder to avoid overwrites")
    print("  3) Cancel this step")
    while True:
        choice = input("Select [1/2/3]: ").strip()
        if choice == '1':
            return target_dir, 'overwrite'
        if choice == '2':
            sub = target_dir / _timestamp()
            sub.mkdir(parents=True, exist_ok=True)
            return sub, 'subfolder'
        if choice == '3':
            return None, 'cancel'
        print("Please enter 1, 2, or 3.")


# --------------- core workflow ---------------

class InteractiveLauncher:
    def __init__(self):
        self.base_images_dir: Optional[Path] = None
        self.expedition: Optional[str] = None
        self.dive: Optional[str] = None
        self.zone: Optional[str] = None
        self.project_name: Optional[str] = None
        self.project_path: Optional[Path] = None
        self.flight_log_path: Optional[Path] = None
        self.rc_exe: Optional[Path] = None
        self.display_output: bool = False

        # Non-interactive automation flag (used by batch mode)
        self.auto_mode: bool = False

        self.initial_export_dir: Optional[Path] = None
        self.merged_export_dir: Optional[Path] = None
        self.temp_components_dir: Optional[Path] = None

        # Path to generated imagelist (recursive images)
        self.imagelist_path: Optional[Path] = None

        # Persistent RC instance control
        self.instance_name: str = "RC1"
        self.instance_started: bool = False

        # Step context to guard exports
        self._current_step: str = ""

        # Step 5 fallback option
        self.per_image_fallback: bool = False

    # Utility: prompt wrappers to support auto (non-interactive) mode
    def _get_yes_no(self, question: str, default: bool = True) -> bool:
        if self.auto_mode:
            return default
        return prompt_yes_no(question, default)

    def _get_int(self, question: str, default: int) -> int:
        if self.auto_mode:
            return default
        return prompt_int(question, default)

    # ---- setup ----
    def prompt_initial(self) -> bool:
        print("=== RealityScan Interactive Launcher ===")
        # base folder of images (and subfolders)
        base = input("Enter base folder of images (contains subfolders): ").strip().strip('"')
        if not base:
            print("No base folder provided. Exiting.")
            return False
        base_path = Path(base)
        if not base_path.exists() or not base_path.is_dir():
            print("Base folder does not exist or is not a directory.")
            return False
        self.base_images_dir = base_path

        self.expedition = input("Expedition (e.g., NA173): ").strip()
        self.dive = input("Dive (e.g., H2104d): ").strip()
        self.zone = input("Zone (1,2,3,... or free text): ").strip()
        if not (self.expedition and self.dive and self.zone):
            print("All of Expedition, Dive, and Zone are required.")
            return False

        self.project_name = build_project_name(self.expedition, self.dive, self.zone)
        self.project_path = self.base_images_dir / f"{self.project_name}.rcproj"

        # export dirs
        align_root = self.base_images_dir / "Alignments"
        self.initial_export_dir = align_root / "InitialAlignments"
        self.merged_export_dir = align_root / "MergedAlignments"
        self.temp_components_dir = align_root / "_TempExport"
        for d in (self.initial_export_dir, self.merged_export_dir, self.temp_components_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Inform user where outputs will go
        print("\nOutput locations:")
        print(f"  Temporary components (after Align): {self.temp_components_dir}")
        print(f"  Initial alignments (after Export Initial): {self.initial_export_dir}")
        print(f"  Merged alignments (after Merge): {self.merged_export_dir}")

        # find RC exe
        rc = find_rc_executable()
        if rc is None:
            # last chance: let user specify
            custom = input("Path to RealityScan executable (blank to abort): ").strip().strip('"')
            if not custom:
                print("Executable not provided. Exiting.")
                return False
            rc = Path(custom)
            if not rc.exists():
                print("Provided executable does not exist.")
                return False
        self.rc_exe = rc

        self.display_output = prompt_yes_no("Display RC output in console?", default=False)

        # optional flight log
        fl = input("Flight log path (optional, press Enter to skip): ").strip().strip('"')
        if fl:
            p = Path(fl)
            if not p.exists():
                print("Warning: flight log path does not exist. It will be ignored.")
            else:
                self.flight_log_path = p

        return True

    # ---- instance helpers ----
    def _wait_instance_ready(self) -> None:
        assert self.rc_exe and self.instance_name
        while True:
            try:
                run_rc_command(self.rc_exe, ["-getStatus", self.instance_name], self.display_output)
                break
            except subprocess.CalledProcessError:
                time.sleep(0.5)

    def _wait_instance_stopped(self, timeout: float = 30.0) -> bool:
        """
        Wait until the named RC instance is no longer responding to -getStatus.
        Returns True if the instance stopped within timeout, False otherwise.
        """
        assert self.rc_exe and self.instance_name
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                run_rc_command(self.rc_exe, ["-getStatus", self.instance_name], self.display_output)
                # Still responding -> not stopped yet
                time.sleep(0.5)
            except subprocess.CalledProcessError:
                return True
        return False

    def start_instance(self) -> None:
        assert self.rc_exe and self.instance_name
        if self.instance_started:
            return
        print(f"Starting RealityScan instance '{self.instance_name}'...")
        # Start RC GUI instance and return immediately; keep it open
        subprocess.Popen([str(self.rc_exe), "-setInstanceName", self.instance_name])
        self._wait_instance_ready()
        self.instance_started = True
        print("Instance ready.")

    def reset_scene(self) -> None:
        """
        Robustly reset the current scene. If delegating -newScene fails (e.g., instance is in a bad state),
        gracefully restart the RealityScan instance and ensure a fresh scene is opened.
        """
        if not self.instance_started:
            self.start_instance()
            return
        try:
            # First try a normal scene reset
            self._delegate("-newScene")
            return
        except subprocess.CalledProcessError:
            print("[Warn] -newScene failed on the running instance. Attempting to restart RealityScan instance...")
            # Try to save and quit gracefully
            try:
                if self.project_path:
                    self._delegate("-save", str(self.project_path))
            except Exception:
                pass
            try:
                self._delegate("-quit")
            except Exception:
                pass
            # Mark instance as stopped and wait a moment
            self.instance_started = False
            time.sleep(1.0)
            # Start a fresh instance and open a new scene
            self.start_instance()
            self._delegate("-newScene")

    def _delegate(self, *args: str) -> None:
        assert self.rc_exe and self.instance_started
        cmd = ["-delegateTo", self.instance_name] + list(args) + ["-waitCompleted", self.instance_name]
        # Always show delegated command for transparency
        print("Delegating:", " ".join(cmd))
        run_rc_command(self.rc_exe, cmd, self.display_output)

    def _delegate_no_wait(self, *args: str) -> None:
        """Delegate without waiting for completion (no -waitCompleted)."""
        assert self.rc_exe and self.instance_started
        cmd = ["-delegateTo", self.instance_name] + list(args)
        print("Delegating (no-wait):", " ".join(cmd))
        run_rc_command(self.rc_exe, cmd, self.display_output)

    def _wait_for_components_stable(self, directory: Path, min_stable_seconds: float = 10.0, timeout_seconds: Optional[float] = 7200.0, poll_interval: float = 2.0) -> list[Path]:
        """
        Block until at least one Component*.rcalign/rsalign file exists in 'directory' and the set of
        files (with sizes) remains unchanged for at least 'min_stable_seconds'. Returns the stable list.
        If timeout_seconds is None, wait indefinitely. Otherwise, raise TimeoutError on timeout.
        """
        start = time.time()
        last_snapshot: dict[str, tuple[int, float]] = {}
        last_change_time = time.time()
        last_heartbeat = time.time()
        while True:
            comps = sorted(list(directory.glob("Component*.rcalign")) + list(directory.glob("Component*.rsalign")))
            snapshot = {str(p): (p.stat().st_size, p.stat().st_mtime) for p in comps if p.exists()}
            if snapshot != last_snapshot:
                last_snapshot = snapshot
                last_change_time = time.time()
                if comps:
                    print(f"[Align] Detected {len(comps)} component file(s); waiting for writes to finish...")
            # Heartbeat every ~60s so user sees we're still waiting
            now = time.time()
            if now - last_heartbeat >= 60.0:
                waited = int(now - start)
                print(f"[Align] Waiting for component exports to stabilize... waited {waited}s so far.")
                last_heartbeat = now
            # Condition: at least one file and stable for min_stable_seconds
            if comps and (time.time() - last_change_time) >= min_stable_seconds:
                return comps
            if timeout_seconds is not None and (time.time() - start) > timeout_seconds:
                raise TimeoutError(f"Timed out waiting for components to stabilize in {directory}")
            time.sleep(poll_interval)

    def _build_imagelist(self) -> tuple[Path, int]:
        """
        Build a recursive imagelist of images under base_images_dir and save it
        into the temporary components directory. Returns (path, count).
        """
        assert self.base_images_dir and self.temp_components_dir
        exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}
        files = [str(p) for p in self.base_images_dir.rglob("*") if p.suffix.lower() in exts]
        self.imagelist_path = self.temp_components_dir / "images.imagelist"
        with open(self.imagelist_path, "w", encoding="utf-8") as f:
            for path in files:
                f.write(path + "\n")
        return self.imagelist_path, len(files)

    # ---- steps ----
    def step_launch_new_project(self) -> bool:
        assert self.rc_exe and self.project_path
        self.start_instance()
        print(f"Creating new project at: {self.project_path}")
        # Use robust scene reset to handle reruns safely
        self.reset_scene()
        self._delegate("-save", str(self.project_path))
        return True

    def step_save_project(self) -> bool:
        assert self.rc_exe and self.project_path
        print(f"Saving project: {self.project_path}")
        if not self.instance_started:
            self.start_instance()
        self._delegate("-save", str(self.project_path))
        return True

    def step_load_images(self) -> dict:
        assert self.rc_exe and self.project_path and self.base_images_dir
        if not self.instance_started:
            self.start_instance()
        print("Scanning for images recursively...")
        imagelist_path, count = self._build_imagelist()
        if count == 0:
            print(f"No images found under {self.base_images_dir}.")
            self._delegate("-save", str(self.project_path))
            return {"Images Detected": 0, "Images Loaded": 0, "Imagelist": str(imagelist_path)}
        print(f"Loading images from: {self.base_images_dir} (including subfolders)")
        self._delegate("-add", str(imagelist_path))
        self._delegate("-save", str(self.project_path))
        print(f"Images detected: {count}")
        print(f"Images loaded:  {count}")
        return {"Images Detected": count, "Images Loaded": count, "Imagelist": str(imagelist_path)}

    def step_load_flight_log(self) -> dict:
        assert self.rc_exe and self.project_path
        if not self.flight_log_path or not self.flight_log_path.exists():
            raise RuntimeError("Flight log is required but was not provided or not found. Please supply a valid path.")
        if not self.instance_started:
            self.start_instance()
        guid, source = get_flightlog_format_id()
        if guid:
            print(f"Setting flight log format id: {guid} (source: {source})")
            self._delegate("-set", f"gpsLogFileFormat={guid}")
        print(f"Importing flight log: {self.flight_log_path}")
        self._delegate("-importFlightLog", str(self.flight_log_path))
        self._delegate("-save", str(self.project_path))
        return {"Flight Log": str(self.flight_log_path), "FormatId": guid or "", "FormatSource": source}

    def _count_keyword_matches(self, keyword: str) -> int:
        """
        Count images where the keyword matches a full directory segment in the path (case-insensitive).
        This avoids false positives from filenames and ensures grouping by folder name like CamUpper/CamMid/etc.
        """
        assert self.base_images_dir
        exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}
        kw = keyword.lower()
        count = 0
        for p in self.base_images_dir.rglob("*"):
            if p.suffix.lower() not in exts:
                continue
            parts_lower = [seg.lower() for seg in p.parts]
            if kw in parts_lower:
                count += 1
        return count

    def _apply_image_params_group(self, label: str, select_keywords: list[str], calib_group: int, lens_group: int, focal_mm: int, camera_model: str) -> int:
        """
        Select images using RealityScan's built-in path token matching (g/<token>/) and
        apply prior calibration/lens groups, camera model, and focal length.
        Returns total images detected in filesystem for reporting (best-effort).
        Only applies settings when at least one match is detected.
        """
        if not self.instance_started:
            self.start_instance()

        # Detect matches: any file path containing the token (case-insensitive)
        assert self.base_images_dir
        exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}
        def contains_token(p: Path, token: str) -> bool:
            return token.lower() in str(p).lower()

        detected = 0
        for p in self.base_images_dir.rglob("*"):
            if p.suffix.lower() not in exts:
                continue
            if any(contains_token(p, kw) for kw in select_keywords):
                detected += 1
        if detected <= 0:
            return 0

        # Use straightforward g/<token>/ selection patterns (no regex tricks), as per examples
        patterns: list[str] = []
        for v in select_keywords:
            # Title-case token as specified (e.g., CamUpper)
            patterns.append(f"g/{v}/")
            v_l = v.lower()
            v_u = v.upper()
            patterns.append(f"g/{v_l}/")
            if v_u != v_l:
                patterns.append(f"g/{v_u}/")

        # Use direct setters for groups and editInputSelection with quoted key=value for other priors
        # setPriorCalibrationGroup/setPriorLensGroup; editInputSelection "inpCalibration=1" / "inpFocal=.." / "inpDistortion=1" / "inpDistortionModel=.."
        distortion_model_code = 1 if camera_model.lower() == "division" else 2
        set_args = [
            "-setPriorCalibrationGroup", str(calib_group),
            "-setPriorLensGroup", str(lens_group),
            "-editInputSelection", f"\"inpCalibration=1\"",
            "-editInputSelection", f"\"inpFocal={focal_mm}\"",
            "-editInputSelection", f"\"inpDistortion=1\"",
            "-editInputSelection", f"\"inpDistortionModel={distortion_model_code}\"",
        ]

        # Apply settings for each selection pattern using built-in selection
        for pattern in patterns:
            print(f"  Applying priors to selection: {pattern}")
            self._delegate(
                "-deselectAllImages",
                "-selectImage", pattern,
                *set_args,
            )

        # Save after applying settings
        self._delegate("-save", str(self.project_path))

        return detected

    def step_set_image_parameters(self) -> dict:
        """
        Group images by directory name and set prior parameters via CLI.
        Directory-based groups:
          - CamUpper: calib=1, lens=1, focal=13, model=Division
          - CamMid  : calib=2, lens=2, focal=14, model=Division
          - CamLower: calib=3, lens=3, focal=18, model=Brown3
          - Zeuss   : calib=4, lens=3, focal=24, model=Brown3
        Note: 'Zeuss' replaces the previous 'herc' pattern.
        """
        assert self.rc_exe and self.project_path and self.base_images_dir
        if not self.instance_started:
            self.start_instance()

        # Mirror the batch example semantics: RootFolder/Images corresponds to the base folder you provided
        print("Setting image parameters based on filename tokens (g/<token>/) across the loaded project images...")
        print(f"  RootFolder: {self.base_images_dir}")
        print(f"  Images:     {self.base_images_dir}")
        print("  Tokens used: g/camupper/, g/cammid/, g/camlower/, g/herc/ (case variants)")
        summary = []

        # Define groups (directory tokens) for selection and parameter application
        groups = [
            {
                "name": "CamUpper",
                "keywords": ["CamUpper"],
                "calib": 1, "lens": 1, "focal": 13, "model": "Division",
            },
            {
                "name": "CamMid",
                "keywords": ["CamMid"],
                "calib": 2, "lens": 2, "focal": 14, "model": "Division",
            },
            {
                "name": "CamLower",
                "keywords": ["CamLower"],
                "calib": 3, "lens": 3, "focal": 18, "model": "Brown3",
            },
            {
                "name": "Zeuss",
                "keywords": ["Zeuss", "Herc"],
                "calib": 4, "lens": 3, "focal": 24, "model": "Brown3",
            },
        ]

        total_detected = 0
        per_group: list[dict] = []
        for g in groups:
            try:
                detected = self._apply_image_params_group(
                    g["name"], g["keywords"], g["calib"], g["lens"], g["focal"], g["model"]
                )
                if detected > 0:
                    total_detected += detected
                    per_group.append({
                        "Group": g["name"],
                        "Detected": detected,
                        "Calibration": g["calib"],
                        "Lens": g["lens"],
                        "Focal(mm)": g["focal"],
                        "Model": g["model"],
                    })
                    print(f"  Group '{g['name']}': detected ~{detected} file(s) -> calib {g['calib']}, lens {g['lens']}, focal {g['focal']}mm, model {g['model']}")
                else:
                    print(f"  Group '{g['name']}': no matching images found — skipped.")
            except subprocess.CalledProcessError as e:
                print(f"  Group '{g['name']}': failed to apply parameters (code {e.returncode}).")
                raise

        if not per_group:
            print("No matching images found for any group; no parameters were applied.")
        else:
            print("Finished setting image parameters.")
        return {"Groups Updated": len(per_group), "Images Matched (approx)": total_detected}

    def step_align_images(self) -> dict:
        assert self.rc_exe and self.project_path and self.temp_components_dir and self.base_images_dir
        if not self.instance_started:
            self.start_instance()
        print("Align Images — available variables:")
        min_comp = self._get_int("Minimum component size (images)", 100)
        export_xmp = self._get_yes_no("Export XMP sidecars?", True)

        args = [
            "-align",
        ]
        if export_xmp:
            args += [
                "-set", "xmpCamera=3",
                "-set", "xmpMerge=true",
                "-set", "xmpRig=true",
                "-set", "xmpCalibGroups=true",
                "-set", "xmpFlags=true",
                "-set", "xmpExGps=true",
                "-exportXMP",
            ]
        args += [
            "-setMinComponentSize", str(min_comp),
            "-exportLatestComponents", str(self.temp_components_dir) + "\\",
            "-save", str(self.project_path),
        ]

        print("Running alignment... this may take a while.")
        # Deliberately wait for alignment to complete before continuing
        # Use synchronous delegate with -waitCompleted to ensure completion
        self._delegate(*args)

        # After completion, continue normally
        # Wait for exported components to appear and stabilize (handles async write/export behavior)
        print("Waiting for component exports to finish and stabilize...")
        try:
            comp_files = self._wait_for_components_stable(self.temp_components_dir, min_stable_seconds=10.0, timeout_seconds=None, poll_interval=2.0)
            comp_rca = [p for p in comp_files if p.suffix.lower() == ".rcalign"]
            comp_rsa = [p for p in comp_files if p.suffix.lower() == ".rsalign"]
        except TimeoutError as te:
            # With timeout disabled, this block should not be reached, but keep fallback for safety
            print(f"[Warn] {te}")
            # Fall back to a best-effort scan (may be empty)
            comp_rca = sorted(self.temp_components_dir.glob("Component*.rcalign"))
            comp_rsa = sorted(self.temp_components_dir.glob("Component*.rsalign"))
            comp_files = comp_rca + comp_rsa
        # Summarize
        total_images = sum(1 for p in self.base_images_dir.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heif"))
        xmp_count = sum(1 for p in self.base_images_dir.rglob("*.xmp")) if export_xmp else 0
        print(f"Temporary components were exported to: {self.temp_components_dir}")
        # Validation: ensure components were actually saved to temp directory
        if comp_files:
            print(f"Validation: saved {len(comp_files)} component file(s) to temporary folder. (rcalign={len(comp_rca)}, rsalign={len(comp_rsa)})")
        else:
            print("Validation FAILED: no component files were found in the temporary folder after alignment.")
        summary = {
            "Components Found": len(comp_files),
            "Total Images": total_images,
            "XMP Files": xmp_count,
            "Images Not Registered (approx)": max(0, total_images - xmp_count) if export_xmp else None,
        }
        print("\nAlignment Summary:")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        return summary

    def step_export_initial(self) -> list[Path]:
        assert self.initial_export_dir and self.temp_components_dir and self.project_name
        exported: list[Path] = []
        # Gather both .rcalign and .rsalign from temp export
        comps = sorted(list(self.temp_components_dir.glob("Component*.rcalign")) +
                       list(self.temp_components_dir.glob("Component*.rsalign")))
        if not comps:
            # As a safety net, wait a short period in case alignment export is finishing
            try:
                print("No components detected yet; waiting briefly for alignment exports to finish...")
                comps = self._wait_for_components_stable(self.temp_components_dir, min_stable_seconds=10.0, timeout_seconds=120.0, poll_interval=2.0)
            except TimeoutError:
                pass
        if not comps:
            print("No components to export from temporary directory.")
            return exported
        # Decide overwrite strategy for InitialAlignments
        export_dir, action = prompt_overwrite_strategy(self.initial_export_dir, "InitialAlignments", auto=self.auto_mode)
        if action == 'cancel' or export_dir is None:
            print("Export cancelled by user.")
            return []
        print(f"Initial alignments directory: {export_dir}")
        print(f"Exporting {len(comps)} component(s) to {export_dir}")
        for idx, src in enumerate(comps, start=1):
            dst = export_dir / f"{self.project_name}_{idx}{src.suffix}"
            if dst.exists():
                # Confirm per-file overwrite if not bulk overwrite selection
                dst.unlink()
            src.replace(dst)
            exported.append(dst)
            print(f"  {src.name} -> {dst.name}")
        # Validation: ensure exported files exist in destination
        missing = [p for p in exported if not p.exists()]
        if missing:
            print(f"Validation FAILED: {len(missing)} exported file(s) not found in destination.")
        else:
            print(f"Validation: saved {len(exported)} initial component file(s) to {export_dir}.")
        # Deliberate save barrier after export
        try:
            if self.rc_exe and self.project_path and self.instance_started:
                self._delegate("-save", str(self.project_path))
        except Exception:
            pass
        return exported

    def step_merge_components(self) -> list[Path]:
        assert self.rc_exe and self.project_path and self.initial_export_dir and self.merged_export_dir and self.project_name
        # Gather components with both extensions
        initial_components = sorted(list(self.initial_export_dir.glob("*.rcalign")) + list(self.initial_export_dir.glob("*.rsalign")))
        if not initial_components:
            print("No initial components to merge; skipping.")
            return []

        print("Merge Components — available variables:")
        # Ask variables
        min_comp = self._get_int("Minimum component size (images) for merge", 50)
        # Feature source preference (based on AlignmentParams key lisPreferImagesAsFeatureSource)
        prefer_images = self._get_yes_no("Set Features Source for all images to Prefer original images (instead of components)?", True)
        _confirm = self._get_yes_no("Proceed to merge all initial components into merged components?", True)
        if not _confirm:
            return []

        # Decide overwrite strategy for MergedAlignments
        export_dir, action = prompt_overwrite_strategy(self.merged_export_dir, "MergedAlignments", auto=self.auto_mode)
        if action == 'cancel' or export_dir is None:
            print("Merge/export cancelled by user.")
            return []

        if not self.instance_started:
            self.start_instance()

        print("Merging components in RealityScan...")
        print(f"Merged alignments will be exported to: {export_dir}")
        # Clear scene, set preferences, import and merge
        self._delegate("-newScene")
        # Set the features source preference globally (best‑available CLI key)
        self._delegate("-set", f"lisPreferImagesAsFeatureSource={'true' if prefer_images else 'false'}")
        # Import components
        for comp in initial_components:
            self._delegate("-importComponent", str(comp))
        # Perform merge and export
        self._current_step = "merge"
        self._delegate(
            "-setMinComponentSize", str(min_comp),
            "-mergeComponents",
            "-exportLatestComponents", str(export_dir) + "\\",
            "-save", str(self.project_path),
        )
        self._current_step = ""

        # Rename merged exported components (handle both extensions just in case)
        merged = sorted(list(export_dir.glob("Component*.rcalign")) + list(export_dir.glob("Component*.rsalign")))
        renamed: list[Path] = []
        for idx, src in enumerate(merged, start=1):
            dst = export_dir / f"{self.project_name}_Merged_{idx}{src.suffix}"
            if dst.exists():
                dst.unlink()
            src.replace(dst)
            renamed.append(dst)
            print(f"  {src.name} -> {dst.name}")
        # Validation: ensure merged files exist in destination
        if not renamed:
            print("Validation FAILED: no merged component files were produced.")
        else:
            missing = [p for p in renamed if not p.exists()]
            if missing:
                print(f"Validation FAILED: {len(missing)} merged file(s) not found in destination.")
            else:
                print(f"Validation: saved {len(renamed)} merged component file(s) to {export_dir}.")
        # Deliberate save barrier after merge/export
        try:
            if self.rc_exe and self.project_path and self.instance_started:
                self._delegate("-save", str(self.project_path))
        except Exception:
            pass
        return renamed

    # ---- workflow orchestration ----
    def _init_progress(self, steps: list[tuple[str, callable]]) -> list[dict]:
        return [{"name": n, "status": "Pending", "reruns": 0} for n, _ in steps]

    def _print_progress(self, progress: list[dict]) -> None:
        print("\nProgress summary:")
        for i, item in enumerate(progress, start=1):
            extra = f" (rerun x{item['reruns']})" if item.get('reruns', 0) else ""
            print(f"  {i:>2}. [{item['status']:^9}] {item['name']}{extra}")

    def _checkpoint(self, step_index: int, steps: list[tuple[str, callable]], progress: list[dict]) -> str:
        self._print_progress(progress)
        ans = input(
            f"\nPress Enter to run step {step_index + 1} ('{steps[step_index][0]}'), enter a step number to jump, 'r' to rerun selected step(s), or 'q' to save and exit: "
        ).strip().lower()
        if ans == 'q':
            return 'quit'
        if ans == 'r':
            return 'rerun'
        # Numeric jump support
        if ans.isdigit():
            target = int(ans)
            if 1 <= target <= len(steps):
                return f'jump:{target-1}'
        return 'continue'

    def _step_key_from_index(self, idx: int) -> str:
        return (
            "launch" if idx == 0 else
            "save" if idx == 1 else
            "load_images" if idx == 2 else
            "flight_log" if idx == 3 else
            "set_image_params" if idx == 4 else
            "align" if idx == 5 else
            "export_initial" if idx == 6 else
            "merge" if idx == 7 else
            "export_merged"
        )

    def _format_step_result(self, result) -> list[str]:
        lines: list[str] = []
        try:
            from pathlib import Path as _P
        except Exception:
            _P = None  # type: ignore
        # Dict summary
        if isinstance(result, dict):
            for k, v in result.items():
                lines.append(f"{k}: {v}")
            return lines
        # List summary (count + examples)
        if isinstance(result, list):
            count = len(result)
            lines.append(f"Items: {count}")
            if count > 0:
                examples = []
                for x in result[:3]:
                    try:
                        if _P is not None and isinstance(x, _P):
                            examples.append(x.name)
                        else:
                            examples.append(str(x))
                    except Exception:
                        examples.append(repr(x))
                lines.append("Examples: " + ", ".join(examples))
            return lines
        # Path
        try:
            from pathlib import Path as _Path
            if isinstance(result, _Path):
                lines.append(f"Path: {result}")
                return lines
        except Exception:
            pass
        # Bool/None or other
        if result is True or result is None:
            lines.append("Status: Completed")
        elif result is False:
            lines.append("Status: No changes")
        else:
            lines.append(f"Result: {result}")
        return lines

    def _execute_step(self, idx: int, steps: list[tuple[str, callable]], progress: list[dict]) -> None:
        name, fn = steps[idx]
        print(f"\n=== Step {idx + 1}: {name} ===")
        progress[idx]["status"] = "Running"
        # Track step key for export guard and allow only in align/merge
        self._current_step = self._step_key_from_index(idx)
        start_ts = time.time()
        try:
            result = fn()
            progress[idx]["status"] = "Done"
            elapsed = time.time() - start_ts
            # Step completion banner
            banner_line = "-" * 75
            print("\n" + banner_line)
            print(f"--------------------------- Step {idx + 1} Complete ---------------------------")
            print(f"## Summary Stats:")
            print(f"## Name: {name}")
            print(f"## Elapsed: {elapsed:.1f}s")
            for line in self._format_step_result(result):
                print("## " + line)
            print(banner_line + "\n")
            # Enforce minimum wait between steps
            print("Waiting 10 seconds before next step...")
            time.sleep(10.0)
        except subprocess.CalledProcessError as e:
            progress[idx]["status"] = "Failed"
            print("Error executing RealityScan command.")
            if e.stdout:
                print(e.stdout)
            if e.stderr:
                print(e.stderr)
            print("Aborting workflow after saving project.")
            raise
        except KeyboardInterrupt:
            progress[idx]["status"] = "Aborted"
            print("\nInterrupted by user. Saving and exiting...")
            raise
        finally:
            # After each step, reset step context
            self._current_step = ""

    def _parse_selection(self, s: str) -> list[int]:
        result = set()
        parts = [p.strip() for p in s.split(',') if p.strip()]
        for p in parts:
            if '-' in p:
                try:
                    a, b = p.split('-', 1)
                    a_i, b_i = int(a), int(b)
                    for k in range(min(a_i, b_i), max(a_i, b_i) + 1):
                        result.add(k)
                except ValueError:
                    continue
            else:
                try:
                    result.add(int(p))
                except ValueError:
                    continue
        return sorted(result)

    def _rerun_menu(self, steps: list[tuple[str, callable]], progress: list[dict]) -> None:
        print("\n=== Rerun Steps ===")
        for i, (name, _) in enumerate(steps, start=1):
            print(f"  {i}. {name}")
        sel = input("Enter step numbers to rerun (comma/range), or press Enter to cancel: ").strip()
        if not sel:
            print("Rerun cancelled.")
            return
        idxs = self._parse_selection(sel)
        if not idxs:
            print("No valid steps selected.")
            return
        for one_based in idxs:
            idx = one_based - 1
            if idx < 0 or idx >= len(steps):
                continue
            # Warn for step 1 (new scene) as it resets the scene
            if idx == 0:
                if not prompt_yes_no("Rerun 'Launch new project' will reset the scene. Continue?", False):
                    continue
            try:
                self._execute_step(idx, steps, progress)
                progress[idx]['reruns'] = progress[idx].get('reruns', 0) + 1
            except Exception:
                # Execution already reported error/abort; stop rerun sequence
                break

    def run(self) -> None:
        try:
            if not self.prompt_initial():
                return

            steps = [
                ("Launch new project", self.step_launch_new_project),
                ("Save project with filename in base directory", self.step_save_project),
                ("Load images into project", self.step_load_images),
                ("Load flight_log", self.step_load_flight_log),
                ("Set per-camera parameters", self.step_set_image_parameters),
                ("Align images", self.step_align_images),
                ("Rename and Export components in 'Alignments/InitialAlignments'", self.step_export_initial),
                ("Merge components", self.step_merge_components),
                ("Rename and Export merged components in 'Alignments/MergedAlignments'", lambda: True),
            ]

            progress = self._init_progress(steps)

            # Offer to resume at step 8 (merge) if initial components exist
            start_index = 0
            try:
                has_initial = any(self.initial_export_dir.glob("*.rcalign")) or any(self.initial_export_dir.glob("*.rsalign"))
            except Exception:
                has_initial = False

            # Offer to resume at step 8
            if has_initial and prompt_yes_no("Detected initial components in 'Alignments/InitialAlignments'. Start directly at step 8 (Merge components)?", False):
                start_index = 7
                for i in range(start_index):
                    if progress[i]["status"] == "Pending":
                        progress[i]["status"] = "Skipped"
                print("Resuming at step 8 (Merge components) using existing initial components.")
            else:
                # Offer to resume at step 7 if temporary components exist
                try:
                    has_temp = any(self.temp_components_dir.glob("Component*.rcalign")) or any(self.temp_components_dir.glob("Component*.rsalign"))
                except Exception:
                    has_temp = False
                if has_temp and prompt_yes_no("Detected temporary components in 'Alignments/_TempExport'. Start directly at step 7 (Export Initial)?", False):
                    start_index = 6
                    for i in range(start_index):
                        if progress[i]["status"] == "Pending":
                            progress[i]["status"] = "Skipped"
                    print("Resuming at step 7 (Export Initial) using existing temporary components.")

            print("\nWorkflow (sequential):")
            for i, (name, _) in enumerate(steps, start=1):
                print(f"  {i}. {name}")
            print("  r. Rerun selected step(s) at any checkpoint")
            print("  q. Exit at any checkpoint (project will be saved)")
            print("  Tip: Enter a step number at a checkpoint to jump directly to that step (earlier Pending steps will be marked Skipped).")

            quitting = False
            idx = start_index
            while idx < len(steps):
                # Allow user to open rerun menu or jump before running this step
                while True:
                    action = self._checkpoint(idx, steps, progress)
                    if action == 'quit':
                        quitting = True
                        break
                    if action == 'rerun':
                        self._rerun_menu(steps, progress)
                        continue
                    if action.startswith('jump:'):
                        try:
                            target_idx = int(action.split(':', 1)[1])
                        except Exception:
                            continue
                        if target_idx < 0 or target_idx >= len(steps):
                            continue
                        # Mark intermediate pending steps as Skipped when jumping forward
                        if target_idx > idx:
                            for i in range(idx, target_idx):
                                if progress[i]['status'] == 'Pending':
                                    progress[i]['status'] = 'Skipped'
                        idx = target_idx
                        # Re-display prompt at the new index
                        continue
                    # continue -> proceed to run this step
                    break
                if quitting:
                    break
                try:
                    self._execute_step(idx, steps, progress)
                except Exception:
                    # Any error/abort already reported by _execute_step
                    break
                idx += 1

        finally:
            # Final save/quit
            try:
                if getattr(self, 'rc_exe', None) and getattr(self, 'project_path', None):
                    if getattr(self, 'instance_started', False):
                        try:
                            self._delegate("-save", str(self.project_path))
                        except Exception:
                            pass
                        try:
                            self._delegate("-quit")
                        except Exception:
                            pass
                        # Wait explicitly for instance to stop before exiting
                        try:
                            if hasattr(self, '_wait_instance_stopped'):
                                stopped = self._wait_instance_stopped(30.0)
                                if not stopped:
                                    print("[Warn] Instance did not stop within timeout; continuing.")
                        except Exception:
                            pass
                    else:
                        run_rc_command(self.rc_exe, ["-save", str(self.project_path), "-quit"], self.display_output)
            except Exception:
                pass
            print(f"\nProject saved at: {self.project_path}")
            print("Goodbye.")


def _parse_exp_dive_from_parents(zone_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """Attempt to infer Expedition and Dive from parent folder names like 'NA173_H2103c'."""
    for parent in zone_dir.parents:
        name = parent.name
        if '_' in name:
            parts = [p for p in name.split('_') if p]
            if len(parts) >= 2:
                return parts[0], parts[1]
    # Fallbacks
    return None, None


def _parse_zone_label(zone_dir: Path) -> str:
    """Convert folder name like 'zone_1' or 'Zone-2' to 'Zone1'. If no number found, title-case name."""
    name = zone_dir.name
    digits = ''.join(ch for ch in name if ch.isdigit())
    if digits:
        return f"Zone{int(digits)}"
    # Remove common prefixes like 'zone', whitespace, underscores/dashes
    base = name.replace('zone', '').replace('Zone', '').replace('_', ' ').replace('-', ' ').strip()
    base = base or name
    return base.title()


def _has_images(folder: Path) -> bool:
    exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}
    try:
        for p in folder.rglob('*'):
            if p.suffix.lower() in exts:
                return True
    except Exception:
        pass
    return False


def _find_flight_log(zone_dir: Path) -> Optional[Path]:
    """Find the flight log in the zone root. Prefer files matching '*_UTM.txt'."""
    try:
        # Primary preference: explicit UTM suffix
        utm_candidates = sorted(zone_dir.glob("flight_log_*_UTM.txt"))
        if utm_candidates:
            return utm_candidates[0]
        # Fallback: any flight_log_*.txt in the root
        any_candidates = sorted(zone_dir.glob("flight_log_*.txt"))
        if any_candidates:
            return any_candidates[0]
    except Exception:
        pass
    return None


def batch_process_zones(zones_root: Path) -> None:
    """
    Iterate zone subfolders and run the existing workflow automatically per zone.
    Exports are saved inside each zone's Alignments directory.
    """
    zones = [d for d in sorted(zones_root.iterdir()) if d.is_dir()]
    if not zones:
        print(f"No subfolders found under: {zones_root}")
        return

    rc = find_rc_executable()
    if rc is None:
        print("RealityScan executable not found. Please install or use interactive mode.")
        return

    print(f"Batch processing {len(zones)} subfolder(s) under: {zones_root}")

    def _fmt_result(res) -> list[str]:
        if isinstance(res, dict):
            return [f"{k}: {v}" for k, v in res.items()]
        if isinstance(res, list):
            lines = [f"Items: {len(res)}"]
            if res:
                try:
                    names = []
                    for x in res[:3]:
                        names.append(getattr(x, 'name', str(x)))
                    lines.append("Examples: " + ", ".join(names))
                except Exception:
                    pass
            return lines
        if res is True or res is None:
            return ["Status: Completed"]
        if res is False:
            return ["Status: No changes"]
        return [f"Result: {res}"]

    def _run(step_index: int, label: str, func):
        start = time.time()
        out = func()
        elapsed = time.time() - start
        # Step completion banner for batch mode
        banner_line = "-" * 75
        print("\n" + banner_line)
        print(f"--------------------------- Step {step_index} Complete ---------------------------")
        print("## Summary Stats:")
        print(f"## Name: {label}")
        print(f"## Elapsed: {elapsed:.1f}s")
        for line in _fmt_result(out):
            print("## " + line)
        print(banner_line + "\n")
        # Enforce minimum wait between steps in batch mode
        print("Waiting 5 seconds before next step...")
        time.sleep(5.0)
        return out

    for zone_dir in zones:
        if not _has_images(zone_dir):
            print(f"[Skip] No images found under {zone_dir}")
            continue
        expedition, dive = _parse_exp_dive_from_parents(zone_dir)
        if not expedition or not dive:
            # Try to interpret immediate grandparent if it contains combined token
            parent_combo = zone_dir.parents[1].name if len(zone_dir.parents) > 1 else None
            if parent_combo and '_' in parent_combo:
                parts = [p for p in parent_combo.split('_') if p]
                if len(parts) >= 2:
                    expedition, dive = parts[0], parts[1]
        zone_label = _parse_zone_label(zone_dir)
        if not expedition or not dive:
            print(f"[Warn] Could not parse Expedition/Dive from parents for {zone_dir}. Using folder-based defaults.")
            expedition = expedition or "UnknownExpedition"
            dive = dive or "UnknownDive"

        project_name = build_project_name(expedition, dive, zone_label)

        print(f"\n=== Processing zone: {zone_dir.name} ===")
        print(f"  Project: {project_name}")

        L = InteractiveLauncher()
        # Use unique instance name per zone to prevent cross-talk and ensure strict sequencing
        L.instance_name = f"RC_{zone_label}"
        L.auto_mode = True
        L.base_images_dir = zone_dir
        L.expedition = expedition
        L.dive = dive
        L.zone = zone_label
        L.project_name = project_name
        L.project_path = zone_dir / f"{project_name}.rcproj"
        L.rc_exe = rc
        L.display_output = False

        # Prepare export directories inside the zone folder
        align_root = zone_dir / "Alignments"
        L.initial_export_dir = align_root / "InitialAlignments"
        L.merged_export_dir = align_root / "MergedAlignments"
        L.temp_components_dir = align_root / "_TempExport"
        for d in (L.initial_export_dir, L.merged_export_dir, L.temp_components_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Discover flight log in the zone root and set it if present
        fl_path = _find_flight_log(zone_dir)
        if fl_path is not None:
            print(f"  Detected flight log: {fl_path}")
            L.flight_log_path = fl_path
        else:
            # Mandatory: prompt user until a valid flight log path is provided for this zone
            print("  No flight log found in zone root. A flight log is REQUIRED to continue.")
            while True:
                user_path = input(f"Enter path to the flight log for zone '{zone_dir.name}': ").strip().strip('"')
                if not user_path:
                    print("  Flight log path cannot be empty.")
                    continue
                p = Path(os.path.expandvars(user_path))
                if not p.exists() or not p.is_file():
                    print("  Provided path is invalid or not a file. Please try again.")
                    continue
                L.flight_log_path = p
                print(f"  Using flight log: {p}")
                break

        try:
            # Steps: launch -> load images -> load flight log (if present) -> params -> align -> export initial -> merge
            _run(1, "Launch new project", L.step_launch_new_project)
            _run(2, "Load images (keep existing functionality)", L.step_load_images)
            _run(3, "Load flight_log (keep existing functionality)", L.step_load_flight_log)
            _run(4, "Set image parameters (stub for future script)", L.step_set_image_parameters)
            _run(5, "Align images (adjust variables)", L.step_align_images)
            _run(6, "Rename and Export components in 'Alignments/InitialAlignments'", L.step_export_initial)
            _run(7, "Merge components (adjust variables)", L.step_merge_components)
        except Exception as e:
            print(f"[Error] Processing failed for {zone_dir.name}: {e}")
        finally:
            try:
                if L.rc_exe and L.project_path:
                    if L.instance_started:
                        try:
                            L._delegate("-save", str(L.project_path))
                        except Exception:
                            pass
                        try:
                            L._delegate("-quit")
                        except Exception:
                            pass
                        # Ensure instance fully stops before next zone
                        try:
                            print("Waiting for RealityScan instance to fully stop before moving to next zone...")
                            while True:
                                stopped = L._wait_instance_stopped(30.0)
                                if stopped:
                                    break
                                print("  Still stopping... continuing to wait.")
                            print("Instance stopped.")
                        except Exception:
                            pass
            except Exception:
                pass
            print(f"----------------------------------------------------------------")
            print(f"===  Saved project at: {L.project_path}")
            print(f"===  Completed zone: {zone_dir.name} ... proceeding to next zone ===\n")
            print(f"----------------------------------------------------------------")

if __name__ == "__main__":
    # Offer batch processing of zone subfolders first
    batch_root = input("Enter folder containing zone subfolders to batch process (press Enter to use interactive mode): ").strip().strip('"')
    if batch_root:
        root_path = Path(batch_root)
        if root_path.exists() and root_path.is_dir():
            batch_process_zones(root_path)
        else:
            print("Provided path is not a valid directory. Launching interactive mode...")
            InteractiveLauncher().run()
    else:
        InteractiveLauncher().run()
