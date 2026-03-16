"""
Standalone component exporter for NA173 / H2103d.
Run from the RC_Main directory:
    python scripts/export_components_NA173_H2103d.py
"""
import logging
import shutil
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.rc_common.rc_delegation import RCDelegationClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Hardcoded settings ────────────────────────────────────────────────────────
OUTPUT_DIR          = Path(r"C:\Users\Public\Documents\Final_NA173_datasets")
BASE_NAME           = "NA173_H2103d"
MAX_COMPONENT_INDEX = 98   # 33 components × 3 sub-components, indices 0–98
MIN_COMPONENT_SIZE  = 44

RC_EXE = None  # auto-detect below
for _candidate in [
    r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityScan 2.0\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityScan\RealityScan.exe",
]:
    if Path(_candidate).exists():
        RC_EXE = _candidate
        break
if RC_EXE is None:
    RC_EXE = shutil.which("RealityScan") or r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe"
# ─────────────────────────────────────────────────────────────────────────────


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = RCDelegationClient(rc_exe=Path(RC_EXE), instance_name="*", logger=log)
    client.on_progress = lambda op, pct, elapsed, eta: log.info(
        "  [%s] %.1f%%  elapsed=%.0fs  eta=%.0fs", op, pct, elapsed, eta
    )

    if not client.verify_connection():
        log.error("Cannot reach RealityScan — make sure a project is open.")
        return

    log.info("Clearing RC command queue …")
    client.clear_queue()

    exported = []
    export_index = 0

    for idx in range(MAX_COMPONENT_INDEX + 1):
        client.run_quick(f"setMinComponentSize", "-setMinComponentSize", str(MIN_COMPONENT_SIZE))

        rev_before = client.get_revision()
        client.run_quick(f"selectComponent({idx})", "-selectComponent", str(idx))
        time.sleep(0.3)
        rev_after = client.get_revision()

        if rev_after == rev_before:
            log.info("  [%d] no component (revision unchanged)", idx)
            continue

        out_path = OUTPUT_DIR / f"{BASE_NAME}_comp{export_index:02d}.rsalign"
        log.info("  [%d] exporting → %s", idx, out_path.name)

        client.delegate("-exportSelectedComponent", str(out_path))
        try:
            client.wait_idle_two_phase(f"export_component_{idx}")
        except TimeoutError:
            log.warning("  [%d] pickup timeout, falling back to waitCompleted", idx)
            client.wait_completed()
            time.sleep(3.0)

        if out_path.exists() and out_path.stat().st_size > 0:
            log.info("  [%d] OK  (%d bytes)", idx, out_path.stat().st_size)
            exported.append(out_path)
            export_index += 1
        else:
            log.warning("  [%d] file not created", idx)

    log.info("Done — %d component(s) exported to %s", len(exported), OUTPUT_DIR)


if __name__ == "__main__":
    main()
