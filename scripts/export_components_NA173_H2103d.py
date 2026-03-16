#!/usr/bin/env python3
"""
Standalone component exporter — NA173 / H2103d
Selects each component by name and exports it as .rsalign.

Component naming pattern in this project:
    Component 0
    Component 0 (1)
    Component 0 (2)
    Component 1
    Component 1 (1)
    Component 1 (2)
    ...
    Component 32
    Component 32 (1)
    Component 32 (2)

Run from RC_Main directory:
    python scripts/export_components_NA173_H2103d.py
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Settings ──────────────────────────────────────────────────────────────────
OUTPUT_DIR        = Path(r"C:\Users\Public\Documents\Final_NA173_datasets")
BASE_NAME         = "NA173_H2103d"
NUM_COMPONENTS    = 33   # Component 0 .. Component 32
SUBS_PER_COMP     = 3    # main + (1) + (2)  per component

RC_EXE = None
for _p in [
    r"C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe",
    r"C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe",
    r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityScan 2.0\RealityScan.exe",
]:
    if Path(_p).exists():
        RC_EXE = Path(_p)
        break

if RC_EXE is None:
    print("ERROR: Could not find RealityScan.exe — edit RC_EXE in this script.")
    sys.exit(1)
# ─────────────────────────────────────────────────────────────────────────────


def delegate(*args):
    cmd = [str(RC_EXE), "-delegateTo", "*"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def wait_completed():
    cmd = [str(RC_EXE), "-waitCompleted", "*"]
    subprocess.run(cmd, capture_output=True, text=True)


def get_revision():
    cmd = [str(RC_EXE), "-getStatus", "*"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    for part in result.stdout.split():
        if part.startswith("rev:"):
            try:
                return int(part.split(":", 1)[1])
            except ValueError:
                pass
    return None


def generate_component_names():
    """Yield (component_name, label) for every sub-component."""
    for n in range(NUM_COMPONENTS):
        yield f"Component {n}", f"comp{n:02d}_main"
        for s in range(1, SUBS_PER_COMP):
            yield f"Component {n} ({s})", f"comp{n:02d}_sub{s}"


def try_export(component_name, output_file):
    if output_file.exists():
        output_file.unlink()

    rev_before = get_revision()
    delegate("-selectComponent", component_name)
    wait_completed()
    time.sleep(0.3)
    rev_after = get_revision()

    if rev_before is not None and rev_after == rev_before:
        return False   # component doesn't exist — revision unchanged

    delegate("-exportSelectedComponentFile", str(output_file))
    wait_completed()
    time.sleep(0.3)

    if output_file.exists() and output_file.stat().st_size > 0:
        return True

    if output_file.exists():
        output_file.unlink()
    return False


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"RC exe    : {RC_EXE}")
    print(f"Output    : {OUTPUT_DIR}")
    print(f"Pattern   : Component 0 / Component 0 (1) / Component 0 (2) ...")
    print()

    # Verify RC is reachable
    cmd = [str(RC_EXE), "-getStatus", "*"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if not result.stdout.strip():
        print("ERROR: No response from RealityScan — make sure a project is open.")
        sys.exit(1)
    print(f"Connected. Status: {result.stdout.strip()}")
    print()

    export_index = 0
    log = []

    for component_name, label in generate_component_names():
        out_file = OUTPUT_DIR / f"{BASE_NAME}_comp{export_index:02d}.rsalign"

        print(f"Trying '{component_name}' ...", end=" ", flush=True)
        if try_export(component_name, out_file):
            print(f"OK  ->  {out_file.name}  ({out_file.stat().st_size} bytes)")
            log.append((component_name, out_file.name))
            export_index += 1
            time.sleep(5.0)
        else:
            print("not found")
            time.sleep(1.0)

    # Summary
    print()
    print("=" * 60)
    print(f"Exported {len(log)} component(s)  [{datetime.now():%Y-%m-%d %H:%M:%S}]")
    print("=" * 60)
    for orig, fname in log:
        print(f"  {orig:<28} ->  {fname}")

    summary_path = OUTPUT_DIR / f"{BASE_NAME}_export_summary.txt"
    with open(summary_path, "w") as f:
        f.write(f"NA173 H2103d component export — {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
        for orig, fname in log:
            f.write(f"{orig:<28} -> {fname}\n")
    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
