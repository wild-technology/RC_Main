#!/usr/bin/env python3
# NA173 copy-by-dive-windows (Windows-safe, hardcoded paths, verbose with progress bars)
# Uses: Start Archaeological Survey Time -> Departure from Wreck Site

from __future__ import annotations

import csv
import io
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple

# ----------------------------
# Hardcoded inputs and paths
# ----------------------------

# Tab-delimited table pasted inline (exactly as provided)
CSV_TSV = """Dive Number\tWreck Site\tArrival at Wreck Site\tNorbit Survey Start Time\tNorbit Survey End Time\tStart Archaeological Survey Time\tDeparture from Wreck Site\tNorbit Passes\tNotes
H2102\tUSS Vincennes\t2025-07-04T22:35:00Z\t2025-07-04T23:18:00Z\t2025-07-05T01:16:12Z\t2025-07-05T01:32:08Z\t2025-07-05T04:34:49Z\t\t
H2103a\tUSS Astoria\t2025-07-05T10:26:31Z\t2025-07-05T10:40:51Z\t2025-07-05T12:34:23Z\t2025-07-05T13:03:04Z\t2025-07-05T17:38:08Z\t\t
H2103b\tUSS Quincy\t2025-07-05T20:20:00Z\t2025-07-05T20:20:34Z\t2025-07-05T22:47:00Z\t2025-07-05T23:00:39Z\t2025-07-06T02:38:09Z\t\t
H2103c\tUnknown 1 - USS New Orleans\t2025-07-06T16:42:37Z\t2025-07-06T18:56:59Z\t2025-07-06T19:32:27Z\t2025-07-06T16:58:49Z\t2025-07-06T19:57:01Z\t\tPotential bow of New Orleans; note that Norbit survey happened after the visual survey for this target
H2103d\tUSS Northampton\t2025-07-06T20:44:11Z\t2025-07-06T21:29:53Z\t2025-07-06T23:21:09Z\t2025-07-06T23:37:49Z\t2025-07-07T06:06:21Z\t\t
H2103e\tHMAS Canberra\t2025-07-07T09:38:02Z\t2025-07-07T09:53:27Z\t2025-07-07T11:46:47Z\t2025-07-07T12:01:28Z\t2025-07-07T16:10:06Z\t\t
H2104a\tUSS Laffey\t2025-07-08T02:46:32Z \tN/A\tN/A\t2025-07-08T02:49:36Z\t2025-07-08T06:43:01Z\t\tarchaeological survey only (norbit on previous dive)
H2104b\tHMAS Canberra\t2025-07-08T07:38:40Z \tN/A\tN/A\t2025-07-08T07:45:04Z\t2025-07-08T09:05:17Z\t\treturn for only imaging
H2104c\tIJN Yudachi\t2025-07-08T15:18:29Z\tN/A\tN/A\t2025-07-08T15:18:29Z\t2025-07-08T18:24:59Z \t\tarchaeological visual survey only (no norbit)
H2104d\tUSS DeHaven\t2025-07-08T20:07:32Z\t2025-07-08T20:27:09Z \t2025-07-08T21:56:48Z\t2025-07-08T22:12:25Z\t2025-07-09T02:22:14Z\t\tHad bell on torpedo launcher
H2104e\tUSS Preston \t2025-07-09T05:23:39Z \t2025-07-09T05:34:04Z\t2025-07-09T07:10:57Z \t2025-07-09T07:15:53Z\t2025-07-09T11:16:56Z\t\t
H2104f\tUSS Walke\t2025-07-09T13:57:54Z\tN/A\tN/A\t2025-07-09T14:01:58Z\t2025-07-09T16:47:32Z\t\tarchaeological visual survey only; note that USS Walke was at Unknown 47
H2104g\tUnknown 49 - IJN Teruzuki\t2025-07-09T19:18:58Z\tN/A\tN/A\t2025-07-09T19:24:25Z\t2025-07-09T23:10:21Z \t\tpartial visual archaeological survey completed 
H2105a\tUnknown 49 - IJN Teruzuki\t2025-07-10T09:00:03Z \t2025-07-10T09:34:08Z \t2025-07-10T11:28:43Z\t2025-07-10T11:54:43Z \t2025-07-10T13:27:28Z\t\t
H2105b\tIJN Teruzuki - stern\t2025-07-10T14:05:23Z \tN/A\tN/A\t2025-07-10T14:05:23Z \t2025-07-10T14:37:38Z\t\tnot a planned target - identified from Norbit while en route to Unknown 44. Completed full visual inspection. 
"""

# Source tree to scan and destination root to create per-dive folders
SOURCE_STILLS = r"D:\NA173\NA173\wca_data\Images"
DEST_STILLS   = r"D:\NA173\NA173\wca_data\Images\proc"

# Use filename timestamps when possible. If none found, fall back to file mtime (UTC).
USE_FILE_MTIME_ONLY = False

# ----------------------------
# Timestamp parsing helpers
# ----------------------------

ISO_Z_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")
COMPACT_TZ_RE = re.compile(r"(\d{8}T\d{6})Z")           # yyyymmddThhmmssZ
COMPACT_RE = re.compile(r"(?<!\d)(\d{8})(\d{6})(?!\d)") # yyyymmddhhmmss

def _parse_iso_z(s: str) -> Optional[datetime]:
    m = ISO_Z_RE.search(s)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1) + "+00:00").astimezone(timezone.utc)
    except ValueError:
        return None

def _parse_compact_tz(s: str) -> Optional[datetime]:
    m = COMPACT_TZ_RE.search(s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def _parse_compact(s: str) -> Optional[datetime]:
    m = COMPACT_RE.search(s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def timestamp_from_name(name: str) -> Optional[datetime]:
    return _parse_iso_z(name) or _parse_compact_tz(name) or _parse_compact(name)

def file_mtime_utc(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)

# ----------------------------
# Dive window parsing (TSV)
# ----------------------------

def slug(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^\w\s\-\._]+", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:80] if len(s) > 80 else s

def build_dive_windows_from_tsv(tsv_text: str) -> List[Dict]:
    windows: List[Dict] = []
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    for row in reader:
        start_raw = (row.get("Start Archaeological Survey Time") or "").strip()
        end_raw   = (row.get("Departure from Wreck Site") or "").strip()
        dive = (row.get("Dive Number") or "").strip()
        site = (row.get("Wreck Site") or "").strip()
        if not start_raw or not end_raw or start_raw.upper() == "N/A" or end_raw.upper() == "N/A":
            continue
        try:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
            end_dt   = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if end_dt <= start_dt:
            continue
        label = f"{dive}_{site}" if dive or site else "UNLABELED"
        windows.append({
            "dive": dive,
            "site": site,
            "label": slug(label),
            "start": start_dt,
            "end": end_dt
        })
    # dedupe identical windows
    uniq = {(w["label"], w["start"], w["end"]): w for w in windows}
    return list(uniq.values())

def which_window_shortest(ts: datetime, windows: List[Dict]) -> Optional[Dict]:
    # Prefer the shortest matching window if overlaps ever exist
    matches = [w for w in windows if w["start"] <= ts <= w["end"]]
    if not matches:
        return None
    return min(matches, key=lambda w: (w["end"] - w["start"]))

# ----------------------------
# File scanning and planning
# ----------------------------

def scan_files_any(root: Path) -> List[Path]:
    return [p for p in root.rglob("*") if p.is_file()]

def plan_moves(
    files: List[Path],
    windows: List[Dict],
    dest_root: Path,
    use_file_mtime_only: bool
) -> Dict[str, List[Tuple[Path, Path]]]:
    plan: Dict[str, List[Tuple[Path, Path]]] = {}
    for src in files:
        ts = None
        if not use_file_mtime_only:
            ts = timestamp_from_name(src.name)
        if ts is None:
            ts = file_mtime_utc(src)
        win = which_window_shortest(ts, windows)
        if win is None:
            continue
        subdir = dest_root / win["label"]
        dst = subdir / src.name
        plan.setdefault(str(subdir), []).append((src, dst))
    return plan

def ensure_unique_destinations(plan: Dict[str, List[Tuple[Path, Path]]]) -> None:
    seen: dict[Path, Path] = {}  # dst -> first src
    for pairs in plan.values():
        for src, dst in pairs:
            if dst in seen:
                first_src = seen[dst]
                print(f"\nCollision detail:\n  First : {first_src}\n  Second: {src}\n  Dest  : {dst}")
                raise RuntimeError(f"Destination collision detected: {dst}")
            seen[dst] = src

def summarize_plan(plan: Dict[str, List[Tuple[Path, Path]]]) -> str:
    lines = []
    total = 0
    for subdir in sorted(plan.keys()):
        count = len(plan[subdir])
        total += count
        lines.append(f"{subdir}: {count} files")
    lines.append(f"TOTAL: {total} files")
    return "\n".join(lines)

# ----------------------------
# Progress display
# ----------------------------

def progress_bar(current: int, total: int, width: int = 40) -> str:
    if total <= 0:
        return "[{}] 0/0".format(" " * width)
    filled = int(width * current / total)
    return "[{}{}] {}/{}".format("#" * filled, "-" * (width - filled), current, total)

def do_copies_with_progress(plan: Dict[str, List[Tuple[Path, Path]]], label: str) -> None:
    all_pairs: List[Tuple[Path, Path]] = []
    for pairs in plan.values():
        all_pairs.extend(pairs)
    total = len(all_pairs)
    print(f"\nStarting copy: {label} | {total} files total")
    copied = 0
    for subdir in plan.keys():
        Path(subdir).mkdir(parents=True, exist_ok=True)
    for src, dst in all_pairs:
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            print(f"\nERROR copying {src} -> {dst}: {e}")
            raise
        copied += 1
        bar = progress_bar(copied, total)
        sys.stdout.write("\r" + bar + f"  {src.name}")
        sys.stdout.flush()
    sys.stdout.write("\nDone: " + label + "\n")
    sys.stdout.flush()

# ----------------------------
# Main
# ----------------------------

def main() -> None:
    windows = build_dive_windows_from_tsv(CSV_TSV)
    if not windows:
        print("No valid dive windows parsed from TSV.")
        sys.exit(1)

    print("Parsed dive windows (Start Archaeological Survey -> Departure):")
    for w in windows:
        print(f"  {w['label']}: {w['start'].isoformat()} to {w['end'].isoformat()}")

    still_root = Path(SOURCE_STILLS)
    if not still_root.exists():
        print(f"Stills source not found: {still_root}")
        sys.exit(1)

    still_files = scan_files_any(still_root)

    print(f"\nScanned counts:")
    print(f"  STILL files under {SOURCE_STILLS}: {len(still_files)}")

    stills_plan = plan_moves(still_files, windows, Path(DEST_STILLS), USE_FILE_MTIME_ONLY)

    print("\nDry-run summary for STILL files (by destination subdirectory):")
    print(summarize_plan(stills_plan))

    # Collision sanity check (should be none if windows are disjoint and basenames are unique per dive)
    ensure_unique_destinations(stills_plan)

    reply = input("\nType 'yes' to confirm copying with metadata preserved: ").strip().lower()
    if reply != "yes":
        print("Aborted.")
        return

    if sum(len(v) for v in stills_plan.values()) > 0:
        do_copies_with_progress(stills_plan, label=f"STILLS -> {DEST_STILLS}")
    else:
        print("\nNo STILL files matched dive windows.")

    print("\nAll requested copies completed.")

if __name__ == "__main__":
    main()
