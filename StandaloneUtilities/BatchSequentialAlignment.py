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
   - Run alignment (with proper monitoring)
   - Disable batch images
5. Final save

Uses proper polling to detect operation start, monitor progress, and confirm completion.
"""

import os
import sys
import subprocess
import time
import re
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
        """Find RealityCapture executable (checks multiple versions)."""
        candidates = [
            Path(r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe"),
            Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"),
            Path(r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe"),
        ]
        for c in candidates:
            if c.exists():
                print(f"Found RealityScan: {c}")
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
        Does NOT wait for completion.
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

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-waitCompleted", self.instance_name]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    def _parse_status(self, status: Optional[str]) -> dict:
        """
        Parse status string into structured data with progress tracking.

        Example: "id:0x10001 progress:57.5% runtime:4.26sec endEstimation:3.40sec"

        Returns:
            Dictionary with keys: progress_pct, runtime_sec, eta_sec, is_idle
        """
        result = {
            'progress_pct': 0.0,
            'runtime_sec': 0.0,
            'eta_sec': 0.0,
            'is_idle': False
        }

        if not status:
            return result

        status_lower = status.lower()

        # Detect idle state
        if 'idle' in status_lower or 'progress:100' in status.replace(' ', ''):
            result['is_idle'] = True
            result['progress_pct'] = 100.0

        # Parse progress percentage
        progress_match = re.search(r'progress:(\d+\.?\d*)%', status)
        if progress_match:
            result['progress_pct'] = float(progress_match.group(1))

        # Parse runtime
        runtime_match = re.search(r'runtime:(\d+\.?\d*)sec', status)
        if runtime_match:
            result['runtime_sec'] = float(runtime_match.group(1))

        # Parse ETA
        eta_match = re.search(r'endEstimation:(\d+\.?\d*)sec', status)
        if eta_match:
            result['eta_sec'] = float(eta_match.group(1))

        # Check for idle ID pattern
        if 'id:0xffffffff' in status and result['progress_pct'] == 0.0:
            result['is_idle'] = True

        return result

    def _is_valid_image(self, img_path: Path) -> bool:
        """Validate that image file exists and is accessible."""
        try:
            return img_path.exists() and img_path.is_file()
        except Exception:
            return False

    def _monitor_operation(self, operation_name: str, timeout_sec: float = 3600.0,
                           poll_interval: float = 5.0) -> None:
        """
        Monitor a delegated operation using -getStatus polling.
        Displays progress, ETA, and elapsed time.

        CRITICAL: Detects operation start by transition from idle→busy, then monitors to completion.

        Args:
            operation_name: Human-readable operation name
            timeout_sec: Maximum time to wait before raising TimeoutError
            poll_interval: Seconds between status polls for progress display
        """
        start_time = time.time()
        last_print_time = start_time
        operation_started = False
        seen_idle = False

        print(f"\n[{operation_name}] Waiting for operation to start...")

        while True:
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed > timeout_sec:
                raise TimeoutError(f"{operation_name} exceeded {timeout_sec}s timeout")

            # Poll status
            status_text = self._get_status()
            status = self._parse_status(status_text)

            # CRITICAL: Detect operation start by transition from idle to non-idle
            if not operation_started:
                if status['is_idle']:
                    seen_idle = True
                    time.sleep(0.5)
                    continue
                elif seen_idle:
                    # Transitioned from idle to non-idle - operation has started!
                    operation_started = True
                    print(f"[{operation_name}] Operation started, monitoring progress...")
                else:
                    # Haven't seen idle yet, keep waiting
                    time.sleep(0.5)
                    continue

            # Now monitor progress with periodic updates
            current_time = time.time()
            if current_time - last_print_time >= poll_interval:
                progress_pct = status['progress_pct']
                eta_sec = status['eta_sec']

                elapsed_min = int(elapsed // 60)
                elapsed_sec = int(elapsed % 60)

                eta_display = f"{int(eta_sec)}s remaining" if eta_sec > 0 else "calculating..."

                print(f"[{operation_name}] Progress: {progress_pct:.1f}% | "
                      f"Elapsed: {elapsed_min}m {elapsed_sec}s | "
                      f"ETA: {eta_display}")

                last_print_time = current_time

            # Check completion
            if status['is_idle'] or status['progress_pct'] >= 100.0:
                elapsed_total = int(elapsed)
                print(f"[{operation_name}] ✓ Complete (took {elapsed_total}s)")
                # Grace period to ensure all file writes complete
                time.sleep(3.0)
                return

            time.sleep(2.0)  # Quick polls to catch completion

    def _run_command_quick(self, operation_name: str, *args: str) -> None:
        """
        Run a quick command and wait for completion using -waitCompleted.
        Use this for fast operations that don't need progress monitoring.

        Args:
            operation_name: Name for logging
            *args: Command arguments to delegate
        """
        print(f"  CMD: {' '.join(args)}")
        self._delegate(*args)
        self._wait_completed()
        time.sleep(0.5)
        print(f"  ✓ {operation_name} complete")

    def load_all_images(self) -> None:
        """
        Load all images from all batches into RC project.
        Creates a temporary imagelist file with only validated image paths.
        """
        print("\n=== Loading All Images ===")

        # Create temporary imagelist file in system temp directory (NOT in images folder)
        import tempfile
        temp_dir = Path(tempfile.gettempdir())
        imagelist = temp_dir / f"realityscan_batch_imagelist_{os.getpid()}.txt"

        # Collect all validated images
        all_images = []
        for images in self.batches.values():
            all_images.extend(images)

        # Verify all paths before writing
        validated_images = [img for img in all_images if self._is_valid_image(img)]

        if len(validated_images) != len(all_images):
            skipped = len(all_images) - len(validated_images)
            print(f"  Warning: Skipped {skipped} invalid file(s)")

        # Write imagelist with only validated images
        print(f"  Creating imagelist: {imagelist}")
        with open(imagelist, "w", encoding="utf-8") as f:
            for img in validated_images:
                f.write(f"{img}\n")

        print(f"Loading {len(validated_images)} validated images...")
        self._run_command_quick("load images", "-add", str(imagelist))

        # Cleanup temporary file
        try:
            imagelist.unlink()
            print(f"  Cleaned up temporary imagelist")
        except Exception as e:
            print(f"  Warning: Could not delete temporary imagelist: {e}")

        print("✓ All images loaded")

    def disable_all_images(self) -> None:
        """Disable alignment for all images in project."""
        print("\n=== Disabling All Images ===")
        self._run_command_quick(
            "disable all",
            "-selectAllImages",
            "-enableAlignment", "false"
        )
        print("✓ All images disabled for alignment")

    def enable_batch(self, batch_name: str) -> None:
        """Enable alignment for images in specified batch."""
        print(f"\n=== Enabling Batch: {batch_name} ===")

        # Use regex pattern with path separators to match exact directory name
        # This prevents "Zone 1" from matching "Zone 10", "Zone 11", etc.
        # Pattern matches: /batch_name/ or \batch_name\ (directory boundaries)
        escaped_name = re.escape(batch_name)
        pattern = f"g/[/\\\\]{escaped_name}[/\\\\]/"

        print(f"  CMD: -deselectAllImages -selectImage {pattern} -enableAlignment true")
        self._delegate("-deselectAllImages")
        self._wait_completed()
        self._delegate("-selectImage", pattern)
        self._wait_completed()
        self._delegate("-enableAlignment", "true")
        self._wait_completed()
        time.sleep(0.5)

        batch_size = len(self.batches.get(batch_name, []))
        print(f"✓ Enabled {batch_size} images from batch '{batch_name}'")

    def align_current_batch(self, batch_name: str) -> None:
        """
        Run alignment for currently enabled images.
        Uses proper monitoring to track progress until completion.
        """
        print(f"\n=== Aligning Batch: {batch_name} ===")

        # Delegate alignment command without waiting
        print("  CMD: -align")
        self._delegate("-align")

        # Monitor the alignment operation until completion
        self._monitor_operation("Alignment", timeout_sec=7200.0, poll_interval=5.0)

        print(f"✓ Batch '{batch_name}' aligned")

    def disable_batch(self, batch_name: str) -> None:
        """Disable alignment for images in specified batch."""
        print(f"\n=== Disabling Batch: {batch_name} ===")

        # Use regex pattern with path separators to match exact directory name
        escaped_name = re.escape(batch_name)
        pattern = f"g/[/\\\\]{escaped_name}[/\\\\]/"

        print(f"  CMD: -deselectAllImages -selectImage {pattern} -enableAlignment false")
        self._delegate("-deselectAllImages")
        self._wait_completed()
        self._delegate("-selectImage", pattern)
        self._wait_completed()
        self._delegate("-enableAlignment", "false")
        self._wait_completed()
        time.sleep(0.5)

        print(f"✓ Batch '{batch_name}' disabled")

    def save_project(self, project_path: Optional[Path] = None) -> None:
        """Save the RC project."""
        if project_path is None:
            project_path = self.images_root / "batch_alignment.rcproj"

        print(f"\n=== Saving Project ===")
        self._run_command_quick("save", "-save", str(project_path))
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

                # Run alignment WITH PROPER MONITORING
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
    instance = input("RealityCapture instance name [*]: ").strip() or "*"

    # Run processor
    processor = BatchAlignmentProcessor(root_path, instance)
    processor.run()


if __name__ == "__main__":
    main()