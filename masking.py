#!/usr/bin/env python3
"""
Copy video proxy files into dive_site folders when file timestamps fall within
[On Bottom Time, Off Bottom Time] windows from the provided dive table.

Defaults:
  - Input root:  X:\Cruise Data\NA173\Videoproxy\NA173_H.264
  - Output root: X:\Cruise Data\NA173\Videoproxy\Organized
  - Time source: filename timestamp if present, else file mtime (treated as UTC)

Usage (no args; uses embedded DIVE_CSV below):
  python organize_proxies_by_bottom_time.py

Optional flags:
  --input-root "X:\\Cruise Data\\NA173\\Videoproxy\\NA173_H.264"
  --output-root "X:\\Cruise Data\\NA173\\Videoproxy\\Organized"
  --dives-csv "path\\to\\dives.csv"   # if you prefer an external CSV instead of the embedded one
  --time-source filename|mtime        # default=auto (filename then mtime)
"""

from __future__ import annotations
import argparse
import csv
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# --- Paste your table here if you want to run without an external file ---
DIVE_CSV = """expedition,dive,site,Launch Time,inwaternav,Recovery Time,ondecknav,On Bottom Time,onnav,ondepth,Off Bottom Time,offnav,offdepth,Herc Max Depth,Herc Avg Depth,Atalanta Max Depth,Atalanta Avg Depth,Total Time (hours),Bottom Time (hours),sampleIDs(range),Dive End,Objective
NA173,H2102,USS Vincennes,2025-07-04T21:23:09Z,-9.048695 159.8757395,2025-07-05T08:10:19Z,-9.081737 159.872069,2025-07-04T22:00:00Z,-9.04878782768 159.876714403,613.66,2025-07-05T07:20:33Z,-9.081737 159.872069,804.58,1028.56,977.77,1016.37,966.42,10.79,9.34, - ,2025-07-05T08:10:33Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2103a,USS Astoria,2025-07-05T13:03:03Z,-9.105086 159.873679,2025-07-05T17:38:08Z,-9.10418 159.874937,2025-07-05T13:03:04Z,-9.104881 159.8734885,852.21,2025-07-05T17:38:08Z,-9.10418 159.874937,870.42,871.4,853.78,853.78,840.59,4.58,4.58, - ,2025-07-05T17:37:50Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2103b,USS Quincy,2025-07-05T23:00:38Z,-9.11014 159.914038,2025-07-06T02:38:10Z,-9.1108375 159.9134805,2025-07-05T23:00:39Z,-9.1101235 159.913765,916.08,2025-07-06T02:38:09Z,-9.1108375 159.9134805,908.28,924.5,912.22,909.89,897.38,3.63,3.62, - ,2025-07-06T02:38:26Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2103c,UNK1 USS New Orleans,2025-07-05T16:58:48Z,-9.104844 159.874685,2025-07-06T19:57:02Z,-9.2351575 159.8512285,2025-07-05T16:58:49Z,-9.104557 159.8742355,853.49,2025-07-06T19:57:01Z,-9.2351575 159.8512285,679.05,924.5,738.16,909.89,732.54,26.97,26.97, - ,2025-07-06T19:57:00Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2103d,USS Northampton,2025-07-06T23:37:48Z,-9.245586 159.8618725,2025-07-07T06:06:22Z,-9.2470285 159.861561,2025-07-06T23:37:49Z,-9.245341 159.8615045,663.16,2025-07-07T06:06:21Z,-9.2470285 159.861561,664.52,666.81,652.06,653.7,637.93,6.48,6.48, - ,2025-07-07T06:06:36Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2103e,HMAS Canberra,2025-07-07T12:01:27Z,-9.225609 159.912178,2025-07-07T16:10:07Z,-9.22624282293 159.910185197,2025-07-07T12:01:28Z,-9.2254605 159.911802,687.79,2025-07-07T16:10:06Z,-9.22624282293 159.910185197,688.46,702.25,692.19,682.72,673.78,4.14,4.14, - ,2025-07-07T16:09:51Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104a,USS Laffey,2025-07-08T02:49:35Z,-9.217481 159.914082,2025-07-08T06:43:02Z,-9.21711469116 159.913081,2025-07-08T02:49:36Z,-9.217373 159.9135705,685.7,2025-07-08T06:43:01Z,-9.21711469116 159.913081,671.18,693.61,683.64,677.94,664.52,3.89,3.89, - ,2025-07-08T06:42:59Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104b,HMAS Canberra,2025-07-08T07:45:03Z,-9.2254105 159.9121,2025-07-08T09:05:18Z,-9.2261045 159.91028,2025-07-08T07:45:04Z,-9.225409 159.9118775,699.39,2025-07-08T09:05:17Z,-9.2261045 159.91028,681.75,703.68,692.08,690.56,677.43,1.34,1.34, - ,2025-07-08T09:05:27Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104c,IJN Yudachi,2025-07-08T15:18:28Z,-9.2110875 159.8495395,2025-07-08T18:24:30Z,-9.21064386667 159.849966352,2025-07-08T15:18:29Z,-9.21081 159.849405,686.71,2025-07-08T18:24:59Z,-9.21064386667 159.849966352,679.41,695.77,690.95,686.84,674.79,3.1,3.11, - ,2025-07-08T18:24:28Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104d,USS DeHaven,2025-07-08T22:12:24Z,-9.206394 159.8295025,2025-07-09T02:22:15Z,-9.206623 159.8286185,2025-07-08T22:12:25Z,-9.206346 159.829186,712.97,2025-07-09T02:22:14Z,-9.206623 159.8286185,723.76,729.64,722.06,717.11,703.21,4.16,4.16, - ,2025-07-09T02:22:00Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104e,USS Preston,2025-07-09T07:15:52Z,-9.209429 159.792305,2025-07-09T11:16:57Z,-9.20926 159.7924705,2025-07-09T07:15:53Z,-9.209275 159.791919,843.15,2025-07-09T11:16:56Z,-9.20926 159.7924705,833.91,852.36,841.37,840.68,822.32,4.02,4.02, - ,2025-07-09T11:17:04Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104f,USS Walke,2025-07-09T14:01:57Z,-9.211436 159.7721425,2025-07-09T16:47:33Z,-9.212106 159.771863,2025-07-09T14:01:58Z,-9.2115685 159.7719835,909.11,2025-07-09T16:47:32Z,-9.212106 159.771863,901.45,916.45,908.12,902.37,890.72,2.76,2.76, - ,2025-07-09T16:47:33Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2104g,UNK49-IJN Teruzuki,2025-07-09T19:24:24Z,-9.220765 159.778554,2025-07-09T23:10:22Z,-9.2202525 159.777657,2025-07-09T19:24:25Z,-9.2204355 159.7780405,852.63,2025-07-09T23:10:21Z,-9.2202525 159.777657,846.72,868.23,861.26,856.7,846.03,3.77,3.77, - ,2025-07-09T23:10:36Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2105a,UNK49-IJN Teruzuki,2025-07-10T11:54:42Z,-9.2205255 159.7790165,2025-07-10T13:27:29Z,-9.2203305 159.77836,2025-07-10T11:54:43Z,-9.22037 159.7785805,858.07,2025-07-10T13:27:28Z,-9.2203305 159.77836,859.23,866.62,860.04,845.53,838.06,1.55,1.55, - ,2025-07-10T13:27:42Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2105b,UNK49-IJN Teruzuki Stern,2025-07-10T14:05:22Z,-9.2201145 159.7804745,2025-07-10T14:37:39Z,-9.2203925 159.7801935,2025-07-10T14:05:23Z,-9.2202245 159.7802925,859.49,2025-07-10T14:37:38Z,-9.2203925 159.7801935,813.23,860.1,855.79,846.49,838.68,0.54,0.54, - ,2025-07-10T14:37:46Z,None Specified
, ,nan,,,,,,,,,,,,,,,,,,,None Specified
NA173,H2106,unk 52,2025-07-11T01:22:58Z,-9.1206685 159.891568,2025-07-11T10:59:19Z,-9.1224075 159.8937925,2025-07-11T01:59:29Z,-9.1207565 159.891571,827.77,2025-07-11T03:05:27Z,-9.1205465 159.8905235,806.16,837.8,818.28,829.89,806.81,9.61,1.1, - ,2025-07-11T10:59:34Z,None Specified
"""

ISO_Z_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
COMPACT_ISO_Z_RE = re.compile(r"(\d{8}T\d{6}Z)")

@dataclass(frozen=True)
class DiveWindow:
    dive: str
    site: str
    start_utc: datetime  # On Bottom Time
    end_utc: datetime    # Off Bottom Time

def parse_iso_z(s: str) -> Optional[datetime]:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' into aware UTC datetime."""
    if not s or s.strip().lower() in {"", "nan"}:
        return None
    s = s.strip()
    if s.endswith("Z"):
        try:
            # Python 3.11+: fromisoformat cannot parse trailing Z, replace with +00:00
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    # try compact 'YYYYMMDDTHHMMSSZ'
    if len(s) == 16 and s.endswith("Z") and "T" in s:
        try:
            dt = datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None

def load_dive_windows(rows: Iterable[dict]) -> List[DiveWindow]:
    out: List[DiveWindow] = []
    for r in rows:
        dive = (r.get("dive") or "").strip()
        site = (r.get("site") or "").strip()
        start = parse_iso_z(r.get("On Bottom Time", ""))
        end = parse_iso_z(r.get("Off Bottom Time", ""))
        if not dive or not site or not start or not end:
            continue
        if end < start:
            # swap if misordered
            start, end = end, start
        out.append(DiveWindow(dive=dive, site=site, start_utc=start, end_utc=end))
    ret
