# CLAUDE.md — Project Context for RC_Main

## Project Overview

RC_Main is a Python pipeline for ROV (Remotely Operated Vehicle) underwater photogrammetry processing. It orchestrates image processing through RealityScan (Epic Games' photogrammetry software, Windows-only) via CLI/delegation commands.

**Pipeline flow** — Two tracks:
- **Preparation track**: Extract Images → Enhance Images → Georeference → Batch
- **RC track**: Camera Setup → Alignment → Export Components → Model Generation → Prepare Model → Model Export

## Architecture

- **main.py** — Pipeline orchestrator. Initializes modules, parses CLI args, runs modules sequentially.
- **module_base/rc_module.py** — Abstract base class all modules extend (get_parameters, validate_parameters, run, finish).
- **module_base/parameter.py** — Parameter system with CLI args, types, defaults, prompts.
- **modules/** — Individual pipeline modules (each is an RCModule subclass).
- **modules/rc_common/** — Shared infrastructure: delegation client, status parser, progress system, camera utilities.
- **modules/prepare_model/** — Mesh cleanup (marginal/large/floating triangle removal, smooth, close holes).
- **modules/model_export/** — Bulk FBX/LAS/3D Tiles export per component.
- **legacy/** — Archived standalone scripts (predecessors to current modules, kept for reference).
- **config/** — Configuration files (camera profiles, XML parameter templates).
- **gui/** — PySide6 GUI (planned).
- **tests/** — Test suite with fixtures.

## Critical Conventions

### File Naming
ALL generated files MUST include `{expedition}_{dive}_UTM{ZONE}{N/S}` in the filename.
Example: `NA173_H2102_UTM57N_zone_001_20250705_0347.rsalign`

### RealityScan XML Format
All XML parameter files MUST follow RealityScan's format:
```xml
<Configuration id="{GUID-HERE}">
  <entry key="parameterName" value="parameterValue"/>
</Configuration>
```
Never use any other XML schema for RC parameter files.

### RealityScan Delegation (Race Condition Prevention)
When delegating commands to a running RealityScan instance:
1. Always clear queue at startup: `-abortInstance`
2. One command per delegation call
3. Two-phase idle detection: wait for START then COMPLETE
4. Triple-verify idle (3 checks x 0.5s)
5. No hardcoded operation timeouts (operations can take 10+ hours)
6. Only pickup detection has a timeout (30s)
7. Poll interval: 2.0s default

### RC Track Startup
- RC connection verified (warn + override if not available)
- XML presets copied to %LOCALAPPDATA%\Capturing Reality\RealityScan\{2.0,2.1}\
- Expedition/dive validated against flight log filename

### Progress & Logging
- All modules emit progress via ProgressReporter (supports tqdm CLI, file log, and GUI signal backends)
- Console MUST log the exact file being processed at each step
- Console MUST show: operation name, progress %, elapsed time, ETA, file counts
- Use Python logging module (INFO level for progress, WARNING for issues, ERROR for failures)

### Input Validation
Every module MUST validate input file formats early in validate_parameters():
- Flight logs: Check semicolon delimiter, required column headers (16 columns), row count
- CSV ROV logs: Check required columns (Timestamp, kalman_lat, kalman_long, kalman_depth, etc.)
- Images: Verify file exists, is readable, is valid image (PIL verify)
- XML params: Validate `<Configuration>` root element and `<entry>` structure
- Paths: Check existence, permissions, disk space for output

### Camera Profiles
Camera configuration is centralized in `config/camera_profiles.json`. All modules read from this single source:
- CamLower: calib_group=1, lens_group=1, focal=18mm, Division
- CamMid: calib_group=2, lens_group=2, focal=14mm, Division
- CamUpper: calib_group=3, lens_group=3, focal=12mm, Division
- Zeuss: calib_group=4, lens_group=4, focal=28mm, Division
- Per-camera prior fields: yaw_offset_deg, roll_offset_deg, pp_u, pp_v

### Save/Checkpoint System
- Session state saved as JSON after each completed step
- Checkpoints saved per long operation (alignment, model generation)
- Variables stored in `config/` directory
- RC parameter XMLs follow `<Configuration id="..."><entry key="..." value="..."/></Configuration>` format

## Development Environment

- **Target OS**: Windows (RealityScan.exe is Windows-only)
- **Development OS**: Linux (this environment) — code is cross-platform Python, but RC integration tests require Windows
- **Python**: 3.13+
- **RealityScan**: 2.0 / 2.1
- **GUI Framework**: PySide6
- **Key dependencies**: opencv-python, numpy, geopandas, scikit-learn, tqdm, piexif, utm, PIL/Pillow

## Testing Strategy

- **Unit tests** run on Linux: status parsing, file format validation, camera detection, progress system, parameter system
- **Integration tests** require Windows with RealityScan installed — marked with `@pytest.mark.windows_only`
- **Mock subprocess** for delegation client tests on Linux
- **Test fixtures** in `tests/fixtures/` with sample flight logs, ROV CSV, XML params

## File Formats

### Flight Log (semicolon-delimited, 16 columns, generated by Georeference step)
```
filename;X (East);Y (North);Alt;X Accuracy;Y Accuracy;Alt Accuracy;Yaw;Pitch;Roll;Yaw Accuracy;Pitch Accuracy;Roll Accuracy;FocalLength;PrincipalU;PrincipalV
```

### ROV Data CSV (tab-delimited, input to Georeference)
```
Timestamp\tVehicle\tx_usbl\ty_usbl\t...kalman_lat\tkalman_long\tkalman_depth\tkalman_yaw_deg\tkalman_pitch_deg\tkalman_roll_deg...
```

### Image Filename Patterns
- `camupper_20250705T034843Z.jpg` — ZCam upper camera
- `cammid_20250705T034843Z.jpg` — ZCam mid camera
- `camlower_20250705T034843Z.jpg` — ZCam lower camera
- `20250705T013705Z_0018_HERC_H.264_H2102_NA173_prob4_frame0.jpg` — Hercules frame grab

## Git Workflow

- Feature branches: `claude/{feature-name}-{session-id}`
- Commit messages describe the "why", not just the "what"
- Never force push, never skip hooks
- Push with `-u origin <branch-name>`
