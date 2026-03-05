# CLAUDE.md — Windows Console Claude Code Agents Workflow

## Overview

This is the configuration for running Claude Code Agents locally on a Windows machine with RealityScan 2.1 installed. The workflow performs direct integration testing of the RC_Main pipeline with real photogrammetry data.

**Target environment**: Windows 10/11, Python 3.11+, RealityScan 2.1, Claude Code CLI

## Environment Setup

### Prerequisites
```powershell
# Python 3.11+ required
python --version

# Install dependencies
pip install -r requirements.txt

# Verify RealityScan 2.1 is installed
# Default locations (checked in order):
#   C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe
#   C:\Program Files\Capturing Reality\RealityScan 2.0\RealityScan.exe
#   C:\Program Files\Capturing Reality\RealityScan\RealityScan.exe

# Set environment variable if RC is in a non-standard location
set RC_EXECUTABLE=D:\RealityScan\RealityScan.exe
```

### Test Data Locations
Place sample datasets in the `tests/sample_data/` directory:
```
tests/sample_data/
    images/                   # Small set of test images (10-50 per camera type)
        camupper_*.jpg
        cammid_*.jpg
        camlower_*.jpg
    rov_data.csv              # Tab-delimited ROV navigation CSV
    flight_log.csv            # Semicolon-delimited flight log (or generate via pipeline)
    rc_params/                # RealityScan XML parameter files
        simplify_params.xml
        texture_params.xml
```

## Architecture

- **main.py** -- Pipeline orchestrator. Runs modules sequentially.
- **module_base/rc_module.py** -- Base class for all pipeline modules.
- **module_base/parameter.py** -- Parameter system with CLI args, validation constraints.
- **modules/rc_common/** -- Shared infrastructure: delegation, status parsing, progress, camera utils, session, XML tools.
- **modules/** -- Pipeline modules (Extract, Enhance, Georeference, Batch, Camera Setup, Alignment, Component Export, Model Generation).
- **config/camera_profiles.json** -- Centralized camera calibration data.
- **tests/** -- Test suite (unit + integration).

## Pipeline Flow

```
Extract Images -> Enhance Images -> Georeference -> Batch -> Camera Setup -> Alignment -> Component Export -> Model Generation
```

## Critical Conventions

### File Naming
ALL generated files MUST include `{expedition}_{dive}_{utm_zone}` in filename.
Example: `NA173_H2102_57L_zone_001_20250705_0347.rsalign`

### RealityScan Delegation Protocol
When delegating commands to a running RealityScan instance:
1. Always clear queue at startup: `-abortInstance`
2. One command per delegation call
3. Two-phase idle detection: wait for START then COMPLETE
4. Triple-verify idle (3 checks x 0.5s)
5. No hardcoded operation timeouts (operations can take 10+ hours)
6. Only pickup detection has a timeout (30s)
7. Poll interval: 2.0s default

### Camera Profiles (config/camera_profiles.json)
- CamLower: calib_group=1, lens_group=1, focal=18mm, Brown3
- CamMid: calib_group=2, lens_group=2, focal=14mm, Brown3WithTangential2
- CamUpper: calib_group=3, lens_group=3, focal=12mm, Brown3WithTangential2
- Zeuss: calib_group=4, lens_group=4, focal=28mm, Brown3

### RealityScan XML Format
```xml
<Configuration id="{GUID-HERE}">
  <entry key="parameterName" value="parameterValue"/>
</Configuration>
```

## Testing Strategy

### Unit Tests (no RC needed)
```powershell
python -m pytest tests/ -v -m "not windows_only"
```

### Integration Tests (requires running RC instance)
```powershell
# Start RealityScan 2.1 first, then:
python -m pytest tests/ -v -m "windows_only"
```

### Quick Smoke Test
```powershell
# Non-interactive mode with minimal modules
set RC_NO_INTERACTIVE=1
set RC_MODULES=Extract Images,Georeference Images
python main.py --expedition_name TEST --dive_name D001 --output_dir D:\output\test
```

### Full Pipeline Test (with RC delegation)
```powershell
set RC_NO_INTERACTIVE=1
python main.py ^
  --expedition_name NA173 ^
  --dive_name H2102 ^
  --output_dir D:\output\NA173_H2102 ^
  --rc_executable_path "C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe" ^
  --rc_instance_name "*" ^
  --r_use_delegation true ^
  --enhance_enabled true ^
  --enhance_input_dir D:\images\NA173
```

## Debugging

### Check RC delegation connection
```python
from modules.rc_common.rc_delegation import RCDelegationClient
client = RCDelegationClient(r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe")
print(client.verify_connection())
print(client.get_status())
```

### Check RC status parsing
```python
from modules.rc_common.rc_status import RCStatusParser
parser = RCStatusParser()
result = parser.parse("id:0x1234 progress:57.5 runtime:120sec estimation:80sec")
print(result)
```

### Validate input files
```python
from modules.rc_common.file_validators import validate_flight_log, validate_rov_csv
print(validate_flight_log(r"D:\data\flight_log.csv"))
print(validate_rov_csv(r"D:\data\rov_data.csv"))
```

## Common Issues

### FileNotFoundError for RC executable
Set `RC_EXECUTABLE` environment variable or pass `--rc_executable_path` on CLI.

### Delegation timeout (30s pickup)
RealityScan must be running and idle before delegation commands are sent. Check Task Manager for the RealityScan process.

### Encoding errors on Windows
All file I/O uses `encoding="utf-8"`. If you encounter encoding issues, ensure your console codepage is UTF-8: `chcp 65001`.

### Path separators
The codebase uses `pathlib.Path` throughout, which handles Windows backslashes correctly. For camera type detection from filenames, `PureWindowsPath` is used to handle both `\` and `/` separators on any platform.

## Key Parameters

| Parameter | CLI Flag | Default | Description |
|-----------|----------|---------|-------------|
| expedition_name | --expedition_name | None | Expedition ID (e.g., NA173) |
| dive_name | --dive_name | None | Dive ID (e.g., H2102) |
| output_dir | --output_dir | None | Pipeline output directory |
| rc_executable_path | --rc_executable_path | auto-detect | Path to RealityScan.exe |
| rc_instance_name | --rc_instance_name | * | RC instance for delegation |
| r_use_delegation | --r_use_delegation | false | Use delegation mode |
| enhance_enabled | --enhance_enabled | false | Enable CLAHE enhancement |
| continue_automatically | --continue_automatically | false | Skip inter-module prompts |

## Git Workflow

- Feature branches: `claude/{feature-name}-{session-id}`
- Commit messages describe "why", not just "what"
- Never force push, never skip hooks
- Push with `-u origin <branch-name>`
