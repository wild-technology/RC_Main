#!/usr/bin/env python3
"""
Batch Sequential Alignment Script for RealityCapture

Scans image subdirectories, loads all images into an open RC instance,
then sequentially enables/aligns each batch (directory) one at a time.

Workflow:
1. Scan subdirectories and build image index
2. Load all images into open RC instance
3. Disable alignment for all images
4. For each batch (subdirectory):
   - Enable alignment for batch images only
   - Run alignment
   - Disable batch images
5. Final save

Uses proper polling commands to detect completion instead of idle timers.
"""

import os
import sys
import subprocess
import time
from pathlib import Path
from typing import Optional


class BatchAlignmentProcessor:
    def __init__(self, images_root: Path, instance_name: str = "RC1"):
        self.images_root = images_root
        self.instance_name = instance_name
        self.rc_exe: Optional[Path] = None

        # Image index: {batch_name: [image_paths]}
        self.batches: dict[str, list[Path]] = {}

        # Supported image extensions
        self.image_exts = {".jpg", ".jpeg", ".png", ".heif", ".tif", ".tiff"}

        # Polling configuration
        self.poll_interval = 2.0

    def find_rc_executable(self) -> Path:
        """Find RealityCapture executable."""
        candidates = [
            Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"),
            Path(r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe"),
        ]
        for c in candidates:
            if c.exists():
                return c

        # Prompt user
        custom = input("Path to RealityScan executable: ").strip().strip('"')
        if not custom:
            raise RuntimeError("RealityScan executable not found")
        rc = Path(custom)
        if not rc.exists():
            raise RuntimeError(f"Executable not found: {rc}")
        return rc

    def scan_directories(self) -> None:
        """
        Scan subdirectories under images_root and build batch index.
        Each immediate subdirectory becomes a batch.
        """
        print(f"\nScanning for image batches in: {self.images_root}")

        # Get immediate subdirectories
        subdirs = sorted([d for d in self.images_root.iterdir() if d.is_dir()])

        if not subdirs:
            raise RuntimeError(f"No subdirectories found in {self.images_root}")

        # Build index
        for subdir in subdirs:
            batch_name = subdir.name
            images = []

            # Recursively find images in this batch directory
            for img_path in subdir.rglob("*"):
                if img_path.suffix.lower() in self.image_exts:
                    images.append(img_path)

            if images:
                self.batches[batch_name] = sorted(images)
                print(f"  Batch '{batch_name}': {len(images)} images")
            else:
                print(f"  Batch '{batch_name}': no images found (skipped)")

        total_images = sum(len(imgs) for imgs in self.batches.values())
        print(f"\nTotal: {len(self.batches)} batches, {total_images} images")

    def _delegate(self, *args: str) -> subprocess.CompletedProcess:
        """
        Execute RealityCapture command via delegation.
        Does NOT wait for completion - use _wait_completed() or _wait_until_idle() for that.
        """
        cmd = [str(self.rc_exe), "-delegateTo", self.instance_name] + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _get_status(self) -> Optional[str]:
        """
        Query RC instance status using -getStatus command.

        Returns:
            Status string or None if query failed
        """
        cmd = [str(self.rc_exe), "-getStatus", self.instance_name]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode == 0 and result.stdout:
            lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            return lines[-1] if lines else None
        return None

    def _wait_completed(self) -> subprocess.CompletedProcess:
        """
        Wait for current operation to complete using CLI's built-in -waitCompleted command.

        This is the authoritative way to wait for RC operations per the documentation.

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-waitCompleted", self.instance_name]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _parse_status(self, status: Optional[str]) -> dict:
        """
        Parse status string into structured data.

        Example: "id:0xffffffff progress:0.0% runtime:10.5sec endEstimation:5.2sec"

        Returns:
            Dictionary with parsed key-value pairs
        """
        result = {}
        if not status:
            return result

        parts = status.split()
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                result[key] = value

        return result

    def _is_idle(self, status: Optional[str] = None) -> bool:
        """
        Check if RealityCapture is idle based on status indicators.

        Args:
            status: Optional pre-fetched status string

        Returns:
            True if idle, False if busy
        """
        if status is None:
            status = self._get_status()
        if not status:
            return False

        parsed = self._parse_status(status)
        status_lower = status.lower()

        # Check for explicit "idle" keyword
        if "idle" in status_lower:
            return True

        # Check for 100% progress
        progress = parsed.get('progress', '')
        if progress in ('100.0%', '100%'):
            return True

        # Check for idle state: id:0xffffffff with 0% progress
        op_id = parsed.get('id', '')
        if op_id == '0xffffffff' and progress in ('0.0%', '0%'):
            return True

        return False

    def _wait_until_idle(self, operation_name: str = "operation", timeout: float = 3600.0) -> None:
        """
        Wait until RealityCapture reports idle status.

        Uses two-stage approach:
        1. CLI's built-in -waitCompleted command (authoritative)
        2. Status polling as verification

        Args:
            operation_name: Name of operation for logging
            timeout: Maximum seconds to wait during polling phase
        """
        print(f"  Waiting for {operation_name}...", end=" ", flush=True)

        # Stage 1: Use CLI's built-in wait mechanism
        self._wait_completed()
        time.sleep(0.5)  # Brief grace period

        # Stage 2: Verify idle state through status polling
        status = self._get_status()
        if self._is_idle(status):
            print("done")
            return

        # Continue polling if not immediately idle
        start_time = time.time()
        last_progress = None

        while time.time() - start_time < timeout:
            status = self._get_status()
            parsed = self._parse_status(status)
            progress = parsed.get('progress', '')

            # Display progress updates
            if progress and progress != last_progress:
                print(f"{progress}", end=" ", flush=True)
                last_progress = progress

            # Check if idle
            if self._is_idle(status):
                elapsed = time.time() - start_time
                print(f"done ({elapsed:.1f}s)")
                return

            time.sleep(self.poll_interval)

        # Timeout reached
        print(f"timeout after {timeout}s")
        raise TimeoutError(f"Operation '{operation_name}' timed out after {timeout} seconds")

    def _run_command(self, operation_name: str, *args: str) -> None:
        """
        Run a command and wait for completion using proper polling.

        Args:
            operation_name: Name for logging
            *args: Command arguments to delegate
        """
        print(f"  CMD: {' '.join(args)}")
        self._delegate(*args)
        self._wait_until_idle(operation_name)

    def load_all_images(self) -> None:
        """Load all images from all batches into RC project."""
        print("\n=== Loading All Images ===")

        # Create temporary imagelist file
        imagelist = self.images_root / "_temp_imagelist.txt"
        all_images = []
        for images in self.batches.values():
            all_images.extend(images)

        with open(imagelist, "w", encoding="utf-8") as f:
            for img in all_images:
                f.write(f"{img}\n")

        print(f"Loading {len(all_images)} images...")
        self._run_command("load images", "-add", str(imagelist))

        # Cleanup
        imagelist.unlink()
        print("✓ All images loaded")

    def disable_all_images(self) -> None:
        """Disable alignment for all images in project."""
        print("\n=== Disabling All Images ===")
        self._run_command(
            "disable all",
            "-selectAllImages",
            "-enableImage", "false"
        )
        print("✓ All images disabled for alignment")

    def enable_batch(self, batch_name: str) -> None:
        """Enable alignment for images in specified batch."""
        print(f"\n=== Enabling Batch: {batch_name} ===")

        # Use path-based selection pattern
        pattern = f"g/{batch_name}/"

        self._run_command(
            "enable batch",
            "-deselectAllImages",
            "-selectImage", pattern,
            "-enableImage", "true"
        )

        batch_size = len(self.batches.get(batch_name, []))
        print(f"✓ Enabled {batch_size} images from batch '{batch_name}'")

    def align_current_batch(self, batch_name: str) -> None:
        """Run alignment for currently enabled images."""
        print(f"\n=== Aligning Batch: {batch_name} ===")

        # Run alignment with proper completion monitoring
        self._run_command("alignment", "-align")

        print(f"✓ Batch '{batch_name}' aligned")

    def disable_batch(self, batch_name: str) -> None:
        """Disable alignment for images in specified batch."""
        print(f"\n=== Disabling Batch: {batch_name} ===")

        pattern = f"g/{batch_name}/"

        self._run_command(
            "disable batch",
            "-deselectAllImages",
            "-selectImage", pattern,
            "-enableImage", "false"
        )
        print(f"✓ Batch '{batch_name}' disabled")

    def save_project(self, project_path: Optional[Path] = None) -> None:
        """Save the RC project."""
        if project_path is None:
            project_path = self.images_root / "batch_alignment.rcproj"

        print(f"\n=== Saving Project ===")
        self._run_command("save", "-save", str(project_path))
        print(f"✓ Project saved: {project_path}")

    def run(self) -> None:
        """Execute complete batch alignment workflow."""
        print("=" * 80)
        print("BATCH SEQUENTIAL ALIGNMENT WORKFLOW")
        print("=" * 80)

        # Setup
        self.rc_exe = self.find_rc_executable()
        self.scan_directories()

        if not self.batches:
            print("\nNo image batches found. Exiting.")
            return

        # Verify RC is running
        status = self._get_status()
        if not status:
            print("\nError: Could not communicate with RealityCapture.")
            print(f"Please ensure RealityCapture instance '{self.instance_name}' is running.")
            return

        print(f"\nConnected to RealityCapture instance '{self.instance_name}'")
        print(f"Status: {status}")

        # Confirm workflow
        print(f"\nThis will process {len(self.batches)} batches sequentially:")
        for i, batch_name in enumerate(self.batches.keys(), 1):
            print(f"  {i}. {batch_name} ({len(self.batches[batch_name])} images)")

        confirm = input("\nProceed? [Y/n]: ").strip().lower()
        if confirm and confirm not in ('y', 'yes'):
            print("Cancelled.")
            return

        try:
            # Load all images
            self.load_all_images()

            # Disable all images initially
            self.disable_all_images()

            # Process each batch sequentially
            for i, batch_name in enumerate(self.batches.keys(), 1):
                print("\n" + "=" * 80)
                print(f"PROCESSING BATCH {i}/{len(self.batches)}: {batch_name}")
                print("=" * 80)

                # Enable batch
                self.enable_batch(batch_name)

                # Run alignment
                self.align_current_batch(batch_name)

                # Disable batch (prepare for next)
                self.disable_batch(batch_name)

                # Save after each batch
                self.save_project()

            print("\n" + "=" * 80)
            print("BATCH PROCESSING COMPLETE")
            print("=" * 80)
            print(f"Processed {len(self.batches)} batches successfully")

        except Exception as e:
            print(f"\n[ERROR] Processing failed: {e}")
            # Try to save project before exit
            try:
                self.save_project()
            except Exception:
                pass
            raise


def main():
    print("Batch Sequential Alignment for RealityCapture")
    print("=" * 80)

    # Get images root directory
    images_root = input("Enter root directory containing image batch subdirectories: ").strip().strip('"')
    if not images_root:
        print("No directory provided. Exiting.")
        return

    root_path = Path(images_root)
    if not root_path.exists() or not root_path.is_dir():
        print(f"Invalid directory: {images_root}")
        return

    # Get RC instance name (optional)
    instance = input("RealityCapture instance name [RC1]: ").strip() or "RC1"

    # Run processor
    processor = BatchAlignmentProcessor(root_path, instance)
    processor.run()


if __name__ == "__main__":
    main()