#!/usr/bin/env python3
"""
Export all components from an open RealityCapture project with custom names as .rsalign files.
Uses delegation to communicate with the running RealityCapture instance.
Handles component naming pattern: "Component ##" and "Component ## (#)"

Based on RealityScan CLI documentation:
- Uses -delegateTo * to delegate commands to the first available instance
- Uses -waitCompleted * to wait for operations to finish
- Uses -getStatus * to check instance status
"""

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional


class ComponentExporter:
    """
    Export all components from a RealityCapture project using delegation.
    """

    def __init__(
            self,
            rc_exe: Path,
            output_dir: Path,
            base_name: str = "Component",
            max_component_num: int = 40,
            max_parenthesis_num: int = 15,
            poll_interval: float = 2.0,
    ):
        """
        Initialize the component exporter.

        Args:
            rc_exe: Path to RealityScan.exe
            output_dir: Directory where .rsalign files will be saved
            base_name: Base name for exported files
            max_component_num: Maximum component number to check
            max_parenthesis_num: Maximum parenthesis number to check
            poll_interval: Seconds between status checks
        """
        self.rc_exe = rc_exe
        self.output_dir = output_dir
        self.base_name = base_name
        self.max_component_num = max_component_num
        self.max_parenthesis_num = max_parenthesis_num
        self.poll_interval = poll_interval
        self.export_log: list[dict[str, str]] = []

        if not self.rc_exe.exists():
            raise FileNotFoundError(f"RealityScan executable not found: {self.rc_exe}")

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _delegate(self, *args: str) -> subprocess.CompletedProcess:
        """
        Send delegation command to running RealityCapture instance.

        Uses: RealityScan.exe -delegateTo * -command params

        The -delegateTo * delegates to the first available instance.

        Args:
            *args: Command arguments to delegate (e.g., "-selectComponent", "Component 0")

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-delegateTo", "*"] + list(args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    def _get_status(self) -> Optional[str]:
        """
        Query RealityCapture status.

        Uses: RealityScan.exe -getStatus *

        Returns:
            Status string or None if query failed
        """
        cmd = [str(self.rc_exe), "-getStatus", "*"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.strip()
        return None

    def _wait_completed(self) -> subprocess.CompletedProcess:
        """
        Wait for current process to complete using the CLI's built-in waitCompleted command.

        Uses: RealityScan.exe -waitCompleted *

        This pauses execution until the current process in the first available instance
        is finished.

        Returns:
            CompletedProcess object
        """
        cmd = [str(self.rc_exe), "-waitCompleted", "*"]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    def _is_idle(self) -> bool:
        """
        Check if RealityCapture is idle.

        Returns:
            True if idle, False if busy or cannot determine
        """
        status = self._get_status()
        if not status:
            return False

        status_lower = status.lower()

        # Check for idle indicators
        if "idle" in status_lower:
            return True

        # Check for 100% progress
        if "progress:100" in status_lower.replace(" ", ""):
            return True

        # Check for "ready" state
        if "ready" in status_lower:
            return True

        return False

    def _wait_until_idle(self, operation_name: str = "operation") -> None:
        """
        Wait until RealityCapture reports idle status.

        First uses the CLI's built-in -waitCompleted command, then polls
        status as a fallback to ensure the operation is truly finished.

        Args:
            operation_name: Name of operation for logging
        """
        print(f"  Waiting for {operation_name} to complete...")

        # Use the CLI's built-in wait mechanism
        self._wait_completed()

        # Additional polling as a safety measure
        start_time = time.time()
        last_status = None
        last_heartbeat = start_time

        while True:
            status = self._get_status()

            # Print status updates when it changes
            if status and status != last_status:
                print(f"  Status: {status}")
                last_status = status

            # Print periodic heartbeat so user knows we're still waiting
            elapsed = time.time() - last_heartbeat
            if elapsed >= 30.0:
                total_elapsed = int(time.time() - start_time)
                print(f"  Still waiting... ({total_elapsed}s elapsed)")
                last_heartbeat = time.time()

            # Check if idle
            if self._is_idle():
                total_elapsed = time.time() - start_time
                print(f"  {operation_name.capitalize()} completed ({total_elapsed:.1f}s)")
                return

            time.sleep(self.poll_interval)

    def _parse_status(self, status: Optional[str]) -> dict:
        """
        Parse status string into components.

        Example status: "id:0xffffffff progress:0.0% runtime:3137.67sec endEstimation:0.00sec rev:473 lastError:0"

        Returns:
            Dictionary with parsed values
        """
        result = {}
        if not status:
            return result

        # Parse key:value pairs
        parts = status.split()
        for part in parts:
            if ':' in part:
                key, value = part.split(':', 1)
                result[key] = value

        return result

    def _get_status_rev(self) -> Optional[int]:
        """
        Get the current revision counter from status.

        Returns:
            Revision number or None if unavailable
        """
        status = self._get_status()
        parsed = self._parse_status(status)
        rev_str = parsed.get('rev')
        if rev_str:
            try:
                return int(rev_str)
            except ValueError:
                pass
        return None

    def _try_export_component(
            self,
            component_name: str,
            output_file: Path,
    ) -> bool:
        """
        Attempt to export a single component using delegation.

        Strategy:
        1. Get current status revision
        2. Select the component
        3. Wait for completion
        4. Check if revision changed (indicates something happened)
        5. If revision changed, attempt export and verify file created

        Args:
            component_name: Name of the component in RealityCapture
            output_file: Path where the .rsalign file should be saved

        Returns:
            True if export succeeded, False otherwise
        """
        # Remove any existing file to ensure we detect new creation
        if output_file.exists():
            output_file.unlink()

        # Get revision before selection
        rev_before = self._get_status_rev()

        # Select the component
        # Command: RealityScan.exe -delegateTo * -selectComponent "Component 0"
        self._delegate("-selectComponent", component_name)

        # Wait for selection to complete
        self._wait_completed()

        # Small additional delay for RC to update internal state
        time.sleep(0.3)

        # Get revision after selection
        rev_after = self._get_status_rev()

        # If revision didn't change, the selection likely failed (component doesn't exist)
        # A successful selection should increment the revision
        if rev_before is not None and rev_after is not None:
            if rev_after == rev_before:
                # No change - component probably doesn't exist
                return False

        # Revision changed - attempt export
        # Command: RealityScan.exe -delegateTo * -exportSelectedComponentFile "path/to/file.rsalign"
        self._delegate("-exportSelectedComponentFile", str(output_file))

        # Wait for export to complete
        self._wait_completed()

        # Check if file was created - this is the definitive test
        time.sleep(0.3)  # Brief delay for file system

        if output_file.exists() and output_file.stat().st_size > 0:
            return True

        # No file created - export failed
        # Clean up any empty/partial file
        if output_file.exists():
            output_file.unlink()

        return False

    def export_all_components(self) -> list[Path]:
        """
        Export all components from the open project with custom names.

        Searches for components in the pattern:
        - Component 0, Component 1, ..., Component {max_component_num}
        - Component 0 (1), Component 0 (2), ..., Component 0 ({max_parenthesis_num})
        - etc.

        Returns:
            List of exported file paths
        """
        exported_files: list[Path] = []
        export_index = 0

        print(f"Output directory: {self.output_dir}")
        print(f"Searching for components 0-{self.max_component_num}")
        print(f"Checking parenthesis variants 1-{self.max_parenthesis_num}")
        print()

        # Verify RealityCapture is running by checking status
        status = self._get_status()
        if not status:
            print("Error: Could not communicate with RealityCapture.")
            print("Please ensure RealityCapture is running with a project loaded.")
            print()
            print("Note: The delegation commands require RealityCapture to be")
            print("running with an open project. Start RealityCapture first,")
            print("then run this script.")
            return []

        print(f"Connected to RealityCapture. Initial status: {status}")
        print()

        for comp_num in range(self.max_component_num + 1):
            # First, try without parentheses: "Component ##"
            component_name = f"Component {comp_num}"
            output_file = self.output_dir / f"{self.base_name}{export_index}.rsalign"

            print(f"Trying: {component_name}...", end=" ", flush=True)
            if self._try_export_component(component_name, output_file):
                print(f"FOUND -> {output_file.name}")
                exported_files.append(output_file)
                self.export_log.append({
                    "original_name": component_name,
                    "exported_name": output_file.name,
                    "export_index": str(export_index),
                })
                export_index += 1
            else:
                print("not found")

            # Then try with parentheses: "Component ## (1)" through "Component ## (max)"
            for paren_num in range(1, self.max_parenthesis_num + 1):
                component_name = f"Component {comp_num} ({paren_num})"
                output_file = self.output_dir / f"{self.base_name}{export_index}.rsalign"

                print(f"Trying: {component_name}...", end=" ", flush=True)
                if self._try_export_component(component_name, output_file):
                    print(f"FOUND -> {output_file.name}")
                    exported_files.append(output_file)
                    self.export_log.append({
                        "original_name": component_name,
                        "exported_name": output_file.name,
                        "export_index": str(export_index),
                    })
                    export_index += 1
                else:
                    print("not found")

        print()
        print(f"Completed. Exported {len(exported_files)} component(s).")
        return exported_files

    def generate_summary(self) -> None:
        """
        Generate and save a summary text file of all exports.
        Also prints summary to console.
        """
        if not self.export_log:
            print("\nNo components were exported. Summary not generated.")
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_file = self.output_dir / f"{self.base_name}_export_summary.txt"

        summary_lines = [
            "=" * 80,
            "RealityCapture Component Export Summary",
            "=" * 80,
            f"Export Date/Time: {timestamp}",
            f"Output Directory: {self.output_dir}",
            f"Total Components Exported: {len(self.export_log)}",
            "",
            "-" * 80,
            "Export Details:",
            "-" * 80,
            f"{'Index':<8} {'Original Component Name':<30} {'Exported Filename':<40}",
            "-" * 80,
        ]

        for entry in self.export_log:
            summary_lines.append(
                f"{entry['export_index']:<8} {entry['original_name']:<30} {entry['exported_name']:<40}"
            )

        summary_lines.extend([
            "-" * 80,
            "",
            "Export completed successfully.",
            "=" * 80,
        ])

        summary_text = "\n".join(summary_lines)

        # Write to file
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(summary_text)

        print(f"\n{summary_text}")
        print(f"\nSummary saved to: {summary_file}")


def get_user_input() -> tuple[Path, str]:
    """
    Prompt user for required paths and settings.

    Returns:
        Tuple of (output_dir, base_name)
    """
    # Default values
    default_output_dir = r"D:\NA168\Zeuss_NA168_H2080\aligned_components"
    default_base_name = "NA168_H2080_"

    print("=" * 80)
    print("RealityCapture Component Exporter (Delegation Mode)")
    print("=" * 80)
    print()
    print("This script will export all components from the currently open")
    print("RealityCapture project using delegation commands.")
    print()
    print("CLI Commands Used:")
    print("  -delegateTo *        : Delegate to first available instance")
    print("  -waitCompleted *     : Wait for operation to complete")
    print("  -getStatus *         : Check instance status")
    print("  -selectComponent     : Select a component by name")
    print("  -exportSelectedComponentFile : Export selected component")
    print()
    print("IMPORTANT: Ensure RealityCapture is running with your project loaded.")
    print()

    # Get output directory
    while True:
        output_input = input(f"Enter output directory for .rsalign files [{default_output_dir}]: ").strip()
        if not output_input:
            output_input = default_output_dir
        output_dir = Path(output_input)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            break
        except Exception as e:
            print(f"Error: Could not create directory: {e}")
            print()

    # Get base name for exported files
    base_name_input = input(f"Enter base name for exported files [{default_base_name}]: ").strip()
    base_name = base_name_input if base_name_input else default_base_name

    print()
    return output_dir, base_name


def main():
    """
    Main entry point for the component export script.
    """
    # RealityScan executable path
    rc_exe = Path(r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe")

    if not rc_exe.exists():
        print(f"Error: RealityScan executable not found: {rc_exe}")
        print("Please update the 'rc_exe' variable in the script.")
        sys.exit(1)

    try:
        # Get user input
        output_dir, base_name = get_user_input()

        # Create exporter and run
        exporter = ComponentExporter(
            rc_exe=rc_exe,
            output_dir=output_dir,
            base_name=base_name,
            max_component_num=13,
            max_parenthesis_num=7,
            poll_interval=2.0,
        )

        exported = exporter.export_all_components()

        # Generate summary
        exporter.generate_summary()

        if not exported:
            print("\nNo components were found or exported.")

    except KeyboardInterrupt:
        print("\n\nExport cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()