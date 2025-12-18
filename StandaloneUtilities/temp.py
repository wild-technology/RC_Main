#!/usr/bin/env python3
"""
Set Camera Parameters by Folder Name

Selects images from specific subfolders (camlower, cammid, camupper, zeuss)
and applies camera priors: calibration group, lens group, focal length, distortion model,
absolute pose, and prior calibration.

Each camera group maintains consistent settings across all batches.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional


class CameraParameterSetter:
    def __init__(self, images_root: Path, instance_name: str = "*"):
        self.images_root = images_root
        self.instance_name = instance_name
        self.rc_exe: Optional[Path] = None

        # Distortion model mapping to numeric codes for editInputSelection
        # Corrected based on actual RealityCapture behavior
        self.distortion_codes = {
            "Division": 0,
            "Brown3": 1,
            "Brown4": 2,
            "Brown3WithTangential2": 3,
            "Brown4WithTangential2": 4,
            "KplusBrown3WithTangential2": 5,
            "KplusBrown4WithTangential2": 6,
        }

        # Camera group definitions
        # Each group has fixed settings that apply regardless of batch
        self.camera_groups = [
            {
                "name": "CamLower",
                "keywords": ["camlower"],
                "calib_group": 1,
                "lens_group": 1,
                "focal_mm": 18,
                "distortion_model": "Brown3"
            },
            {
                "name": "CamMid",
                "keywords": ["cammid"],
                "calib_group": 2,
                "lens_group": 2,
                "focal_mm": 14,
                "distortion_model": "Brown3WithTangential2"
            },
            {
                "name": "CamUpper",
                "keywords": ["camupper"],
                "calib_group": 3,
                "lens_group": 3,
                "focal_mm": 12,
                "distortion_model": "Brown3WithTangential2"
            },
            {
                "name": "Zeuss",
                "keywords": ["zeuss"],
                "calib_group": 4,
                "lens_group": 4,
                "focal_mm": 28,
                "distortion_model": "Brown3"
            },
        ]

        self.image_exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}

    def find_rc_executable(self) -> Path:
        """Find RealityCapture executable."""
        candidates = [
            Path(r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe"),
            Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"),
            Path(r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe"),
        ]

        for c in candidates:
            if c.exists():
                print(f"Found RealityScan: {c}")
                return c

        custom = input("Path to RealityScan executable: ").strip().strip('"')
        if not custom:
            raise RuntimeError("RealityScan executable not found")

        rc = Path(custom)
        if not rc.exists():
            raise RuntimeError(f"Executable not found: {rc}")
        return rc

    def _delegate(self, *args: str) -> subprocess.CompletedProcess:
        """Execute RealityCapture command via delegation."""
        cmd = [str(self.rc_exe), "-delegateTo", self.instance_name] + list(args)
        print(f"  CMD: {' '.join(args)}")
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _wait_completed(self) -> subprocess.CompletedProcess:
        """Wait for current operation to complete."""
        cmd = [str(self.rc_exe), "-waitCompleted", self.instance_name]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _count_matching_images(self, keywords: list[str]) -> int:
        """Count images where any keyword appears in the path (case-insensitive)."""
        count = 0
        for img_path in self.images_root.rglob("*"):
            if img_path.suffix.lower() not in self.image_exts:
                continue

            path_lower = str(img_path).lower()
            if any(kw.lower() in path_lower for kw in keywords):
                count += 1

        return count

    def _get_status(self) -> Optional[str]:
        """Query RC instance status."""
        cmd = [str(self.rc_exe), "-getStatus", self.instance_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode == 0 and result.stdout:
            lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            return lines[-1] if lines else None
        return None

    def apply_camera_settings(self, group: dict) -> bool:
        """Apply camera settings for a specific camera group."""
        name = group["name"]
        keywords = group["keywords"]
        calib_group = group["calib_group"]
        lens_group = group["lens_group"]
        focal_mm = group["focal_mm"]
        distortion_model = group["distortion_model"]
        distortion_code = self.distortion_codes.get(distortion_model)

        if distortion_code is None:
            print(f"  ERROR: Unknown distortion model '{distortion_model}'")
            return False

        print(f"\n=== Processing Camera Group: {name} ===")
        print(f"  Calibration Group: {calib_group} (constant across all batches)")
        print(f"  Lens Group: {lens_group} (constant across all batches)")

        count = self._count_matching_images(keywords)
        if count == 0:
            print(f"  No matching images found for keywords: {keywords}")
            return False

        print(f"  Found ~{count} images matching keywords: {keywords}")
        print(f"  Settings to apply:")
        print(f"    Focal Length: {focal_mm}mm")
        print(f"    Distortion Model: {distortion_model} (code: {distortion_code})")
        print(f"    Absolute Pose: Position and Orientation")
        print(f"    Prior Calibration: Approximate")

        patterns = []
        for kw in keywords:
            patterns.append(f"g/{kw}/")
            patterns.append(f"g/{kw.lower()}/")
            patterns.append(f"g/{kw.upper()}/")

        patterns = list(dict.fromkeys(patterns))

        # STEP 1: Deselect all images
        print(f"  Selecting all images for group '{name}'...")
        self._delegate("-deselectAllImages")
        self._wait_completed()

        # STEP 2: Build up selection
        for pattern in patterns:
            print(f"    Adding pattern: {pattern}")
            self._delegate("-selectImage", pattern)
            self._wait_completed()

        # STEP 3: Apply settings in this specific order
        print(f"  Applying settings to all selected images in group '{name}'...")

        # Apply calibration and focal FIRST (before grouping)
        self._delegate("-editInputSelection", f'"inpCalibration=1"')
        self._wait_completed()

        self._delegate("-editInputSelection", f'"inpFocal={focal_mm}"')
        self._wait_completed()

        # Apply distortion model BEFORE setting lens group
        self._delegate("-editInputSelection", f'"inpDistortion=1"')
        self._wait_completed()

        self._delegate("-editInputSelection", f'"inpDistortionModel={distortion_code}"')
        self._wait_completed()

        # Set absolute pose and prior calibration
        self._delegate("-editInputSelection", f'"inpAbsolutePose=2"')
        self._wait_completed()

        self._delegate("-editInputSelection", f'"inpPriorCalibration=1"')
        self._wait_completed()

        # NOW set the groups (after individual settings are applied)
        self._delegate("-setPriorCalibrationGroup", str(calib_group))
        self._wait_completed()

        self._delegate("-setPriorLensGroup", str(lens_group))
        self._wait_completed()

        print(f"✓ Camera group '{name}' configured successfully")
        return True

    def run(self) -> None:
        """Execute camera parameter setup workflow."""
        print("=" * 80)
        print("CAMERA PARAMETER SETUP BY FOLDER NAME")
        print("=" * 80)

        # Setup
        self.rc_exe = self.find_rc_executable()

        if not self.images_root.exists() or not self.images_root.is_dir():
            raise RuntimeError(f"Invalid images directory: {self.images_root}")

        print(f"\nImages Root: {self.images_root}")

        # Verify RC is running
        status = self._get_status()
        if not status:
            print("\nError: Could not communicate with RealityCapture.")
            print(f"Please ensure RealityCapture instance '{self.instance_name}' is running.")
            return

        print(f"Connected to RealityCapture instance '{self.instance_name}'")
        print(f"Status: {status}")

        # Show planned configuration
        print("\nCamera groups to configure (settings constant across all batches):")
        for i, group in enumerate(self.camera_groups, 1):
            print(f"  {i}. {group['name']}: calib_group={group['calib_group']}, "
                  f"lens_group={group['lens_group']}, "
                  f"focal={group['focal_mm']}mm, "
                  f"model={group['distortion_model']}")
        print("\nAll groups will also have:")
        print("  - Absolute Pose: Position and Orientation")
        print("  - Prior Calibration: Approximate")

        confirm = input("\nProceed with configuration? [Y/n]: ").strip().lower()
        if confirm and confirm not in ('y', 'yes'):
            print("Cancelled.")
            return

        # Apply settings for each camera group
        success_count = 0
        for group in self.camera_groups:
            try:
                if self.apply_camera_settings(group):
                    success_count += 1
            except Exception as e:
                print(f"  ERROR: Failed to configure {group['name']}: {e}")

        # Save project
        print("\n=== Saving Project ===")
        self._delegate("-save")
        self._wait_completed()
        print("✓ Project saved")

        # Summary
        print("\n" + "=" * 80)
        print("CONFIGURATION COMPLETE")
        print("=" * 80)
        print(f"Successfully configured {success_count}/{len(self.camera_groups)} camera groups")
        print("\nVerify in RealityCapture UI that each lens group has consistent settings:")
        print("  - Lens Group 1 (CamLower): Brown3, focal=18mm")
        print("  - Lens Group 2 (CamMid): Brown3WithTangential2, focal=14mm")
        print("  - Lens Group 3 (CamUpper): Brown3WithTangential2, focal=12mm")
        print("  - Lens Group 4 (Zeuss): Brown3, focal=28mm")


def main():
    print("Camera Parameter Setup for RealityCapture")
    print("=" * 80)

    # Get images root directory
    images_root = input("Enter root directory containing image folders: ").strip().strip('"')
    if not images_root:
        print("No directory provided. Exiting.")
        return

    root_path = Path(images_root)
    if not root_path.exists() or not root_path.is_dir():
        print(f"Invalid directory: {images_root}")
        return

    # Get RC instance name
    instance = input("RealityCapture instance name [*]: ").strip() or "*"

    # Run configuration
    setter = CameraParameterSetter(root_path, instance)
    setter.run()


if __name__ == "__main__":
    main()