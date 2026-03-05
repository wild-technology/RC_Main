# Integration Plan: Standalone Utilities → Main Codestack + GUI

## Context

The main.py codestack runs a 4-module pipeline (Extract → Georeference → Batch → RealityCaptureAlignment) using direct subprocess.run() calls to RealityScan. Five standalone utilities were created to fix bugs and add functionality: BatchSequentialAlignment (delegation + checkpoint), ModelGenerator (25-step model pipeline with two-phase idle detection), SetCameraParams (camera calibration groups), text_rsalign_exporter (component export), and clahe1 (image enhancement). These must be carefully merged into the main codestack for testing, with a GUI planned on PySide6.

**Problem**: The main codestack has no delegation support, no progress monitoring of RC operations, no model generation, no camera setup, and no image enhancement. The standalone utilities solve all of these but with duplicated infrastructure (6+ independent implementations of delegation, status parsing, and idle detection).

**Goal**: Expand main.py with standalone functionality incrementally, harmonize progress reporting, add a PySide6 GUI layer, and let the user choose how overlapping functionality is resolved.

---

## Phase 0: Merge Decision Questions (User Must Answer)

These overlap decisions shape the entire implementation. Answers marked **[RECOMMENDED]** are the robust-implementation defaults.

### Q1. Camera Type Detection
Three implementations exist with slight differences:
- `realitycapture_interface.py:__determine_camera_subfolder()` — returns 'camupper'|'cammid'|'camlower'|'zeuss'|'other'
- `batch_directory_exif.py:__determine_camera_subfolder()` — identical logic
- `georeference_images.py:_get_camera_type()` — returns 'camupper'|'cammid'|'camlower'|'zeuss'|'unknown'

**[RECOMMENDED]**: Consolidate into single function in `modules/file_metadata_parser.py`. Use 'unknown' as fallback (not 'other'). Expose as public utility.

### Q2. RC Executable Discovery
Four implementations scan different paths:
- `realitycapture_interface.py` — checks param, then Program Files, then shutil.which
- `BatchSequentialAlignment.py` — checks 2.1, 2.0, base RealityScan paths
- `SetCameraParams.py` — same as BatchSequentialAlignment
- `rc_module_batcher4.py` — checks single hardcoded path

**[RECOMMENDED]**: Consolidate into shared function. Search order: user param → env var `RC_EXECUTABLE` → 2.1 → 2.0 → base → shutil.which. Include `SetVariables.bat`-style env var support.

### Q3. Min Component Size Default
- `realitycapture_interface.py` hardcodes 100 in the command, but has parameter defaulting to 400
- `ModelGenerator.py` / `MakeModels.py` have no filtering (process all found .rsalign files)

**[RECOMMENDED]**: Make it a user-facing parameter (default 100). Filter at export time but allow override at model generation time.

### Q4. Camera Calibration Values — CONFLICT TABLE (must resolve)

Three sources have **conflicting** values for the same cameras:

| Property | Camera | SetCameraParams.py | rc_module_batcher4.py | georeference_images.py | batch_directory_exif.py |
|----------|--------|--------------------|-----------------------|------------------------|------------------------|
| Calib Group | CamLower | **1** | **3** | N/A | N/A |
| Calib Group | CamMid | 2 | 2 | N/A | N/A |
| Calib Group | CamUpper | **3** | **1** | N/A | N/A |
| Calib Group | Zeuss | 4 | 4 | N/A | N/A |
| Lens Group | CamLower | 1 | 3 | N/A | N/A |
| Lens Group | Zeuss | **4** | **3** | N/A | N/A |
| Focal (mm) | CamLower | 18 | 18 | **15** | **16** |
| Focal (mm) | CamMid | 14 | 14 | **14** | **13.5** |
| Focal (mm) | CamUpper | **12** | **13** | **13** | **13** |
| Focal (mm) | Zeuss | **28** | **24** | **24** | **5.2** |
| Distortion | CamMid | **Brown3WithTang2** | **Division** | N/A | N/A |
| Distortion | CamUpper | **Brown3WithTang2** | **Division** | N/A | N/A |
| Pitch Offset | CamLower | N/A | N/A | 10° | N/A |
| Pitch Offset | CamMid | N/A | N/A | 20° | N/A |
| Pitch Offset | CamUpper | N/A | N/A | 70° | N/A |
| Pos Accuracy | All | N/A | N/A | 10m, 10m, 1m | N/A |

**Conflicts requiring decision** (bold cells above):
- CamLower vs CamUpper calib/lens groups are **swapped** between the two files
- Zeuss focal: 28mm vs 24mm vs 5.2mm (three different values!)
- CamUpper focal: 12mm vs 13mm
- CamMid/CamUpper distortion: Brown3WithTangential2 vs Division

**[RECOMMENDED]**: Create a single `config/camera_profiles.json` with all camera data unified. You must pick the correct values for each conflict. Each profile has: name, keywords, make, model, focal_length, position_offsets, accuracy, calibration_group, lens_group, distortion_model, pitch_offset. All modules read from this file.

### Q5. Model Pipeline Step Order
`ModelGenerator.py` and `MakeModels.py` differ on ordering:

| Step | ModelGenerator.py | MakeModels.py |
|------|-------------------|---------------|
| After high model | Simplify 70% FIRST, then filter triangles | Filter triangles FIRST, then clean |
| Hole closing | closeHoles(80000) after smooth | closeHoles(5000) after clean, then clean again |
| Simplify passes | 2 passes (70% each) | 2 passes (with XML params) |
| Smoothing | After simplify+filter | After filter+clean |

**[RECOMMENDED]**: Make step order **user-configurable** via a pipeline definition. Default to MakeModels.py order (filter → clean → smooth → close holes → texture), but expose toggles for each step and iteration counts.

### Q6. Triangle Filtering Thresholds
- `ModelGenerator.py`: selectLargeTrianglesRel 3.0 (aggressive)
- `MakeModels.py`: selectLargeTrianglesRel 2.0 (moderate)

**[RECOMMENDED]**: User-facing parameter, default 2.0 (MakeModels.py), range [1.0, 10.0].

### Q7. Hole Closing Thresholds
- `ModelGenerator.py`: 80000 edges (single pass)
- `MakeModels.py`: 5000 small + 600000 large (two-pass strategy)

**[RECOMMENDED]**: Two-pass approach from MakeModels.py. Expose both thresholds as parameters.

### Q8. Export Formats
- `ModelGenerator.py`: FBX + Cesium 3D Tiles (hardcoded)
- `MakeModels.py`: FBX + Cesium (toggleable booleans + XML param files)

**[RECOMMENDED]**: Toggleable per format. Add OBJ and GLB options. Each with optional XML param file path. Default: FBX=True, Cesium=True.

### Q9. Simplification Target
- `ModelGenerator.py`: Simplify to 70% (hardcoded ratio)
- `MakeModels.py`: Uses XML parameter files (configurable)

**[RECOMMENDED]**: Support BOTH modes — ratio (percentage) or XML param file. If XML provided, use it; otherwise use ratio. Default ratio: 50%.

### Q10. Image Enhancement Parameters
`clahe1.py` has all hardcoded values:
- Fine CLAHE: clipLimit=1.5, tiles=16×16
- Coarse CLAHE: clipLimit=1.2, tiles=64×64
- Blend weights: 0.4/0.4/0.2
- Contrast alpha: 1.15
- JPEG quality: 90

**[RECOMMENDED]**: Expose only the most impactful: clip_limit_fine, clip_limit_coarse, contrast_alpha, jpeg_quality as parameters. Keep tile sizes and blend weights as internal defaults.

### Q11. Flight Log Handling
- `georeference_images.py`: Reads CSV with specific column names, generates RC-format flight log
- `batch_directory_exif.py`: Reads same CSV, subsets per zone
- `realitycapture_interface.py`: Validates flight log, imports via `-importFlightLog`

**[RECOMMENDED]**: Keep separate (they do different things at different pipeline stages). Consolidate the CSV reading/validation into a shared utility.

### Q12. Alignment Mode
- Current main codestack: Direct CLI (launch RC, execute all, quit) — one instance per zone
- `BatchSequentialAlignment.py`: Delegation to running instance, sequential per-batch
- `rc_module_batcher4.py`: Delegation with full workflow management

**[RECOMMENDED]**: Support BOTH modes via parameter toggle. Default: delegation mode (new). Direct CLI preserved as fallback. User can choose per-run.

### Q13. Progress Reporting Style
- Current: tqdm progress bars (steps-based, terminal)
- Standalone: print() with timestamps + parsed RC status (%, ETA, elapsed)
- Future GUI: PySide6 signals

**[RECOMMENDED]**: Create a `ProgressReporter` abstraction with backends: `TqdmBackend` (CLI), `LogBackend` (file), `SignalBackend` (GUI). All modules emit standardized progress events. RC operations report: operation name, progress %, elapsed time, ETA.

---

## Phase 1: Shared Infrastructure

### 1.1 Create `modules/rc_common/rc_delegation.py`

**Source**: Merge from `ModelGenerator.py` (two-phase detection) + `rc_module_batcher4.py` (component stability) + `text_rsalign_exporter.py` (revision detection)

```
class RCDelegationClient:
    __init__(rc_exe, instance_name="*", poll_interval=2.0, logger=None)

    # Core delegation
    delegate(*args) → CompletedProcess
    get_status() → dict  # {id, progress, runtime, estimation, is_idle, rev}
    wait_completed() → CompletedProcess
    abort_instance() → None

    # Robust monitoring (from ModelGenerator.py)
    wait_idle_two_phase(operation_name, timeout=36000) → None
      # Phase 1: wait for START (30s timeout for pickup)
      # Phase 2: wait for COMPLETE (poll, triple-verify idle)

    # Quick operations (delegate + waitCompleted)
    run_quick(operation_name, *args) → None

    # Component stability (from rc_module_batcher4.py)
    wait_for_stable_files(directory, pattern, min_stable_sec=10, timeout=900) → list[Path]

    # Utility
    verify_connection() → bool
    clear_queue() → None
    get_revision() → int  # from text_rsalign_exporter.py

    # Progress callback
    on_progress: Callable[[str, float, float, float], None] = None
      # (operation_name, progress_pct, elapsed_sec, eta_sec)
```

**Key design**: The `on_progress` callback is the harmonization point. CLI mode sets it to a tqdm updater. GUI mode sets it to a Qt signal emitter. All RC operations flow through this single interface.

**Race condition prevention** (strictly per documentation):
- One command per delegation call
- Two-phase detection: wait for START then COMPLETE
- Triple-verify idle (3 checks × 0.5s)
- `-abortInstance` at startup to clear stale queues
- No hardcoded timeouts for operations (only for pickup detection: 30s)
- Poll interval configurable (default 2.0s)

### 1.2 Create `modules/rc_common/rc_status.py`

**Source**: Extract from `ModelGenerator.py:RCStatusParser`

```
class RCStatusParser:
    @staticmethod
    parse(status_text: str) → dict
      # Returns: {id, progress, runtime, estimation, is_idle, rev, last_error}

    IDLE_INDICATORS = ["idle", "id:0xffffffff"]
```

### 1.3 Create `modules/rc_common/progress.py`

**Unified progress system**:

```
class ProgressEvent:
    module_name: str
    operation_name: str
    progress_pct: float  # 0.0-100.0
    elapsed_sec: float
    eta_sec: float
    message: str

class ProgressReporter:
    __init__(backends: list[ProgressBackend])
    report(event: ProgressEvent) → None
    start_operation(name, total_steps=None) → None
    update(increment=1) → None
    finish() → None

class TqdmBackend(ProgressBackend):   # CLI
class LogBackend(ProgressBackend):    # File logging
class SignalBackend(ProgressBackend): # GUI (emits Qt signal)
```

### 1.4 Create `config/camera_profiles.json`

Consolidate all camera data from georeference_images.py, SetCameraParams.py, batch_directory_exif.py:

```json
{
  "cameras": [
    {
      "name": "CamLower",
      "keywords": ["camlower"],
      "make": "ZCAM", "model": "F6 16-35mm III Lower",
      "focal_length_mm": 16,
      "calibration_group": 1, "lens_group": 1,
      "distortion_model": "Brown3",
      "position_offsets": {"forward_m": 1.0, "lateral_m": 0.0, "down_m": 1.0},
      "accuracy": {"yaw": 5.0, "pitch": 5.0, "roll": 5.0},
      "pitch_offset_deg": 10
    },
    ...
  ]
}
```

### 1.5 Create `modules/rc_common/camera_utils.py`

Single camera type detection function + camera profile loader:

```
def detect_camera_type(filename: str) → str
def load_camera_profiles(config_path: str) → dict
def get_camera_profile(filename: str, profiles: dict) → dict
```

### Files to create:
- `modules/rc_common/__init__.py`
- `modules/rc_common/rc_delegation.py`
- `modules/rc_common/rc_status.py`
- `modules/rc_common/progress.py`
- `modules/rc_common/camera_utils.py`
- `config/camera_profiles.json`

### Files to modify:
- `module_base/rc_module.py` — Add ProgressReporter integration, replace raw tqdm
- `main.py` — Initialize ProgressReporter, pass to modules

---

## Phase 2: Image Enhancement Module

### 2.1 Create `modules/image_enhancement/image_enhancement.py`

**Source**: `StandaloneUtilities/clahe1.py`

Wrap as RCModule. New parameters:
- `enhance_enabled` (bool, default=False) — opt-in
- `enhance_clip_limit_fine` (float, default=1.5)
- `enhance_clip_limit_coarse` (float, default=1.2)
- `enhance_contrast_alpha` (float, default=1.15)
- `enhance_jpeg_quality` (int, default=90)

Pipeline position: After ExtractImages, before GeoreferenceImages.
Preserves folder structure. Copies non-image files unchanged.

### Files to create:
- `modules/image_enhancement/__init__.py`
- `modules/image_enhancement/image_enhancement.py`

### Files to modify:
- `main.py` — Register module in `initialize_modules()`

---

## Phase 3: Camera Setup Module

### 3.1 Create `modules/camera_setup/camera_setup.py`

**Source**: `StandaloneUtilities/SetCameraParams.py`

Requires delegation client (Phase 1). New parameters:
- `cam_setup_enabled` (bool, default=True)
- `cam_profiles_path` (str, default='config/camera_profiles.json')

Applies per-camera settings in the correct order (from SetCameraParams.py):
1. Deselect all → select by keyword pattern
2. Set calibration (inpCalibration=1)
3. Set focal (inpFocal=XX)
4. Set distortion (inpDistortion=1, inpDistortionModel=code)
5. Set pose (inpAbsolutePose=2)
6. Set prior calibration (inpPriorCalibration=1)
7. Set calibration group
8. Set lens group

### Files to create:
- `modules/camera_setup/__init__.py`
- `modules/camera_setup/camera_setup.py`

---

## Phase 4: Enhanced Alignment with Delegation

### 4.1 Modify `modules/realitycapture_interface/realitycapture_interface.py`

Add delegation mode alongside existing direct CLI mode.

New parameters:
- `rc_use_delegation` (bool, default=False) — opt-in for testing
- `rc_batch_sequential` (bool, default=False) — sequential alignment within single project
- `rc_checkpoint_file` (str, default=None) — checkpoint path for resume
- `rc_instance_name` (str, default="*") — target RC instance

New method: `_run_delegation_mode()`:
- Uses RCDelegationClient
- Implements batch sequential alignment (from BatchSequentialAlignment.py)
- Two-phase monitoring for alignment
- Checkpoint save/resume
- Falls back to direct CLI if delegation fails

Existing `__run_realityscan_command()` preserved as `_run_direct_mode()`.

### Files to modify:
- `modules/realitycapture_interface/realitycapture_interface.py`

---

## Phase 5: Component Export Module

### 5.1 Create `modules/component_export/component_export.py`

**Source**: `StandaloneUtilities/text_rsalign_exporter.py`

New parameters:
- `export_enabled` (bool, default=True)
- `export_output_dir` (str)
- `export_base_name` (str)
- `export_max_component_num` (int, default=66)
- `export_component_prefix` (str)

Uses revision-based detection from text_rsalign_exporter.py.

### Files to create:
- `modules/component_export/__init__.py`
- `modules/component_export/component_export.py`

---

## Phase 6: Model Generation Module

### 6.1 Create `modules/model_generation/model_generation.py`

**Source**: Merge `MakeModels.py` (primary, more configurable) + `ModelGenerator.py` (signal handling, two-phase detection)

New parameters:
- `model_enabled` (bool, default=True)
- `model_alignment_dir` (str) — path to .rsalign files
- `model_export_dir` (str) — output path
- `model_project_prefix` (str)
- `model_test_mode` (bool, default=True) — first component only
- `model_step_order` (str, default="filter,clean,smooth,holes,texture") — configurable
- `model_large_triangle_threshold` (float, default=2.0)
- `model_small_hole_max_edges` (int, default=5000)
- `model_large_hole_max_edges` (int, default=600000)
- `model_enable_simplify` (bool, default=True)
- `model_simplify_ratio` (float, default=0.5) — used if no XML
- `model_simplify_params` (str, default=None) — XML param file
- `model_simplify_passes` (int, default=2)
- `model_export_fbx` (bool, default=True)
- `model_export_cesium` (bool, default=True)
- `model_export_obj` (bool, default=False)
- `model_export_glb` (bool, default=False)
- `model_fbx_params` (str, default=None) — XML
- `model_cesium_params` (str, default=None) — XML
- `model_texture_params` (str, default=None) — XML
- `model_smooth_params` (str, default=None) — XML
- `model_unwrap_params` (str, default=None) — XML
- `model_reprojection_params` (str, default=None) — XML

Pipeline (default order from MakeModels.py):
1. Select component
2. Set reconstruction region auto
3. Scale region 2× center
4. Calculate high model
5. Select marginal triangles → remove
6. Select large triangles (threshold) → remove
7. Select largest component → invert → remove
8. Clean model
9. Smooth
10. Close small holes (5000)
11. Clean model
12. Calculate texture
13. (If simplify) Rename _HighPoly → simplify × N → close large holes → rename _LowPoly → unwrap → reproject
14. Save
15. Export (per format toggles)

Signal handling: SIGINT/SIGTERM → abort_instance → save → exit.

### Files to create:
- `modules/model_generation/__init__.py`
- `modules/model_generation/model_generation.py`

---

## Phase 7: Updated main.py Pipeline

### 7.1 Modify `main.py`

New module registration order:
```python
available_modules = {
    'Extract Images': ExtractImages(logger),
    'Enhance Images': ImageEnhancement(logger),        # NEW
    'Georeference Images': GeoreferenceImages(logger),
    'Batch Directory': BatchDirectory(logger),
    'Camera Setup': CameraSetup(logger),               # NEW
    'RealityCapture Alignment': RealityCaptureAlignment(logger),  # ENHANCED
    'Component Export': ComponentExport(logger),        # NEW
    'Model Generation': ModelGeneration(logger),        # NEW
}
```

New global parameters:
- `rc_executable_path` (str) — moved to global, shared by all RC modules
- `rc_instance_name` (str, default="*") — shared
- `camera_profiles_path` (str, default="config/camera_profiles.json") — shared

### 7.2 Re-Georeference Step

The user mentioned "re-georeference batches with hardcoded file paths" as part of the flow. This means after batching, the flight logs need to be regenerated per-zone with correct paths. The current BatchDirectory already creates per-zone flight logs. If re-georeferencing is needed (e.g., with different parameters), add a parameter to GeoreferenceImages to accept a batched directory and process each zone's flight log.

New parameter on GeoreferenceImages:
- `geo_reprocess_batches` (bool, default=False) — re-run georeferencing on already-batched zones

---

## Phase 8: GUI Architecture (PySide6)

### 8.1 Framework Choice: PySide6

**Why**: LGPL license, QProcess for RC subprocess, QThread for long operations, signals/slots for thread-safe updates, QTableWidget for statistics, cross-platform, mature packaging with PyInstaller.

### 8.2 Application Structure

```
gui/
├── __init__.py
├── main_window.py          # QMainWindow with toolbar + central wizard
├── pipeline_wizard.py      # QStackedWidget with step panels
├── panels/
│   ├── georeference_panel.py
│   ├── batch_panel.py
│   ├── alignment_panel.py
│   ├── model_panel.py
│   └── export_panel.py
├── widgets/
│   ├── log_viewer.py       # QPlainTextEdit for real-time RC output
│   ├── progress_widget.py  # Progress bar + elapsed + ETA
│   ├── stats_table.py      # QTableWidget for file/process statistics
│   └── parameter_form.py   # Auto-generated form from Parameter objects
├── workers/
│   ├── pipeline_worker.py  # QRunnable for running modules
│   └── rc_process.py       # QProcess wrapper for RealityScan.exe
├── state/
│   ├── session.py          # Save/load session state (JSON)
│   ├── checkpoint.py       # Checkpoint management
│   └── metadata_db.py      # SQLite for expedition/dive/sensor data
└── app.py                  # QApplication entry point
```

### 8.3 Main Window Layout

```
┌─────────────────────────────────────────────────────────┐
│  Menu: File | Edit | Tools | Help                       │
│  Toolbar: [New] [Open] [Save] [Load Checkpoint]        │
├────────────┬────────────────────────────────────────────┤
│            │                                            │
│  Steps:    │  Active Panel (changes per step)           │
│            │  ┌──────────────────────────────────────┐  │
│  ● Georef  │  │  Parameters Form                     │  │
│  ○ Batch   │  │  (auto-generated from Parameter)     │  │
│  ○ Align   │  │                                      │  │
│  ○ Model   │  ├──────────────────────────────────────┤  │
│  ○ Export  │  │  Statistics Table                     │  │
│            │  │  (file counts, match rates, etc.)     │  │
│            │  ├──────────────────────────────────────┤  │
│            │  │  [Run Step]  [Skip]  Progress: ████░ │  │
│            │  └──────────────────────────────────────┘  │
├────────────┴────────────────────────────────────────────┤
│  Log Panel (collapsible, real-time RC output)           │
│  ┌──────────────────────────────────────────────────┐   │
│  │ [14:32:01] Delegating: -align                    │   │
│  │ [14:32:03] Status: progress:12.5% ETA:3m42s      │   │
│  │ [14:35:45] Alignment complete (3m42s)             │   │
│  └──────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  Status Bar: Expedition: NA168 | Dive: H2080 | Step 3/5│
└─────────────────────────────────────────────────────────┘
```

### 8.4 Statistics Displayed Per Step

| After Step | Statistics to Display |
|-----------|----------------------|
| Georeference | Images matched: N/M (X%), time buckets (exact/1-4s/5-15s/>15s), UTM zone, cameras detected |
| Batch | Zones created: N, images/zone (min/max/avg), overlap %, KDE bandwidth, zone map plot |
| Alignment | Components exported: N, XMP files: N, per-zone success/fail, cameras per component |
| Camera Setup | Images per camera group, settings applied per group |
| Model Gen | Per-component: triangles, texture resolution, export sizes, duration per step |

### 8.5 Log Panel Implementation

- QPlainTextEdit (read-only, monospace font)
- Auto-scroll to bottom
- Color coding: INFO=white, WARNING=yellow, ERROR=red, PROGRESS=cyan
- Filter buttons: [All] [Warnings] [Errors] [RC Commands]
- When RC delegation commands execute: show the actual command string
- When RC status polled: show parsed progress (not raw status string)
- Timestamps on every line

### 8.6 Save/Load/Checkpoint System

**Session State** (JSON file):
```json
{
  "expedition": "NA168",
  "dive": "H2080",
  "parameters": { ... all Parameter values ... },
  "completed_steps": ["georeference", "batch"],
  "current_step": "alignment",
  "step_outputs": {
    "georeference": { "flight_log_path": "...", "stats": {...} },
    "batch": { "zones": [...], "output_dir": "..." }
  },
  "timestamp": "2026-03-04T14:30:00"
}
```

**Checkpoint** (per long operation):
```json
{
  "operation": "batch_sequential_alignment",
  "completed_batches": ["zone_1", "zone_2"],
  "project_path": "D:\\output\\project.rsproj",
  "timestamp": "2026-03-04T16:45:00"
}
```

**Metadata DB** (SQLite, persistent across sessions):
- `expeditions` table: id, name, created_at
- `dives` table: id, expedition_id, name, created_at
- `sensors` table: id, name, camera_profile_json
- `sessions` table: id, dive_id, session_file_path, created_at

### 8.7 Expedition/Dive Parsing

The GUI assumes the user starts with georeferencing. On startup:
1. Prompt for Expedition Name + Dive Name (or load from session)
2. These propagate to all modules as global parameters
3. Used in file naming: `{expedition}_{dive}_zone_{N}_{timestamp}`
4. Stored in metadata DB for cross-session lookup
5. Status bar always shows current expedition/dive

---

## Phase 9: Key Codebase Improvements

### 9.1 Parameter System Enhancement
- Add `parameter_group` field to Parameter (for GUI grouping: "General", "Alignment", "Model", etc.)
- Add `min_value` / `max_value` for numeric parameters (GUI validation)
- Add `choices` list for enum-like parameters (GUI dropdown)
- Add `file_filter` for file path parameters (GUI file dialog filter)

### 9.2 Module Output Standardization
All modules should return output dicts with consistent keys:
- `Success` (bool)
- `Duration` (float, seconds)
- `Statistics` (dict of displayable stats)
- `Output_Files` (list of paths created)
- `Warnings` (list of warning strings)

### 9.3 Error Recovery
- All RC delegation operations: wrap in try/except, attempt save on failure
- Checkpoint after every zone/component completion
- GUI: "Retry" button on failed steps

---

## Implementation Order & Agent Assignment

### Sprint 1: Shared Infrastructure (Phase 1)
**Agent**: general-purpose (code generation)
- Create `modules/rc_common/` package (rc_delegation.py, rc_status.py, progress.py, camera_utils.py)
- Create `config/camera_profiles.json`
- Modify `module_base/rc_module.py` for ProgressReporter
- Unit test delegation client with mock subprocess

### Sprint 2: Image Enhancement (Phase 2)
**Agent**: general-purpose
- Create `modules/image_enhancement/` package
- Register in main.py
- Test with sample images

### Sprint 3: Enhanced Alignment (Phase 4)
**Agent**: general-purpose
- Add delegation mode to `realitycapture_interface.py`
- Add batch sequential alignment
- Add checkpoint support
- Integration test with RC instance

### Sprint 4: Camera Setup + Component Export (Phase 3 + 5)
**Agent**: general-purpose
- Create `modules/camera_setup/` package
- Create `modules/component_export/` package
- Register in main.py

### Sprint 5: Model Generation (Phase 6)
**Agent**: general-purpose
- Create `modules/model_generation/` package
- Configurable pipeline steps
- Signal handling
- Export validation

### Sprint 6: main.py Integration (Phase 7)
**Agent**: general-purpose
- Update module registration
- Add global parameters
- Re-georeference support
- End-to-end CLI testing

### Sprint 7: GUI Foundation (Phase 8)
**Agent**: general-purpose
- PySide6 main window + wizard structure
- Parameter form auto-generation
- Log viewer widget
- Progress widget

### Sprint 8: GUI Workers + State (Phase 8 cont.)
**Agent**: general-purpose
- Pipeline worker (QRunnable)
- RC process wrapper (QProcess)
- Session save/load
- Checkpoint management
- Metadata DB

### Sprint 9: GUI Panels + Polish
**Agent**: general-purpose
- Step-specific panels with statistics
- File/process stats display
- Expedition/dive management
- PyInstaller packaging

---

## QA/QC Plan

### Unit Tests
- `test_rc_status_parser.py` — Parse all known status string formats
- `test_rc_delegation.py` — Mock subprocess, verify two-phase detection logic
- `test_camera_utils.py` — Camera type detection for all known filename patterns
- `test_progress.py` — ProgressReporter with mock backends
- `test_image_enhancement.py` — CLAHE output quality checks
- `test_parameter.py` — New Parameter fields (group, min/max, choices)

### Integration Tests (require RC instance)
- Delegation client: connect, delegate, monitor, abort
- Batch sequential alignment: 2 zones, checkpoint, resume
- Model generation: single component, full pipeline
- Camera setup: apply settings, verify via status

### GUI Tests
- Widget rendering (QTest framework)
- Worker thread communication
- Save/load session roundtrip
- Checkpoint resume from UI

### Regression Tests
- Run existing main.py pipeline (direct CLI mode) — must still work
- All existing parameters must still function
- No import errors with new modules disabled

### Manual Testing Checklist
- [ ] Direct CLI alignment still works (no delegation)
- [ ] Delegation mode: align single zone
- [ ] Delegation mode: batch sequential with checkpoint
- [ ] Model generation: full pipeline, single component
- [ ] Model generation: export FBX + Cesium
- [ ] Image enhancement: before/after comparison
- [ ] Camera setup: verify groups applied correctly
- [ ] GUI: launch, configure, run georeference
- [ ] GUI: progress monitoring during alignment (10+ minute test)
- [ ] GUI: save session, close, reload, resume
- [ ] GUI: checkpoint resume after simulated failure

---

## Verification

To verify the implementation end-to-end:

1. **CLI mode**: `python main.py --r_use_delegation false` — runs existing direct CLI pipeline
2. **Delegation mode**: `python main.py --r_use_delegation true --r_instance_name "*"` — runs new delegation pipeline
3. **GUI mode**: `python gui/app.py` — launches PySide6 GUI
4. **Unit tests**: `python -m pytest tests/`
5. **Single module test**: `RC_MODULES="Model Generation" python main.py --model_test_mode true`

---

## Pre-Answered Questions (Independent Assessment)

The following questions from Phase 0 can be answered independently for robust implementation:

**Q1 (Camera Detection)**: Consolidate — all three implementations do the same thing with minor differences. Single function in `camera_utils.py`.

**Q2 (RC Exe Discovery)**: Consolidate — superset of all paths, include 2.1. No downside.

**Q3 (Min Component Size)**: Use the parameter value (currently defaults to 400), fix the hardcoded 100 at line 488 to respect the parameter. Default should be lowered to 100 to match current actual behavior.

**Q8 (Export Formats)**: Toggleable per format with XML overrides. Clear win — MakeModels.py pattern.

**Q9 (Simplification)**: Support both ratio and XML. If XML provided, use it. No downside.

**Q10 (Enhancement Params)**: Expose clip limits, contrast, quality. Keep tile sizes internal.

**Q11 (Flight Log)**: Keep separate handlers per stage. Add shared CSV validation utility.

**Q12 (Alignment Mode)**: Support both. Delegation as opt-in (default=False) for safe testing.

**Q13 (Progress)**: ProgressReporter abstraction with pluggable backends. Clear architecture win.

## User Decisions (Resolved)

**Q4 (Camera Calibration)**: Use SetCameraParams.py mapping (CamLower=1, CamMid=2, CamUpper=3, Zeuss=4). Zeuss focal=28mm. CamUpper focal=12mm.

**Q5 (Pipeline Order)**: **Fully rearrangeable** — user can drag/reorder steps in GUI. Default order: filter→clean→smooth→holes→texture. Must validate dependencies (e.g., can't texture before mesh exists).

**Q6 (Triangle Threshold)**: All configurable parameters. Default: selectLargeTrianglesRel=2.0.

**Q7 (Hole Thresholds)**: All configurable. Defaults: small_holes=5000, large_holes=600000 (MakeModels.py two-pass).

**Q_GUI**: PySide6.

**Q_Thresholds**: All configurable (CLI/GUI parameters with sensible defaults).

**Q_Re-Georef**: Automatic (transparent after batching).

**Q_Integration**: One module at a time (safer). Sprint 1 → Sprint 9 sequentially.

**Q_Distortion**: Brown3WithTangential2 as default for CamMid/CamUpper.

**Additional design note**: Step reordering in the GUI requires a validation layer that prevents invalid orderings. Steps will have dependency declarations (e.g., `calculateTexture` requires mesh, `reprojectTexture` requires both HighPoly and LowPoly). The GUI will show warnings for invalid orderings rather than hard-blocking, since some users may want experimental orderings.
