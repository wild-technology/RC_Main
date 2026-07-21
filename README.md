# RC_Main — ROV Photogrammetry Pipeline for RealityScan 2.2

Processing pipeline for underwater ROV photogrammetry: extract and
georeference dive imagery, batch it, and drive **RealityScan 2.2**
(Epic Games, formerly RealityCapture) through its CLI to align images and
generate textured models.

## Requirements

- Windows 10/11 (RealityScan is Windows-only; the data-prep scripts are
  Windows-oriented too)
- RealityScan 2.2 — the scripts auto-detect the executable under
  `C:\Program Files\Epic Games\RealityScan_2.2\` (and fall back to 2.1/2.0
  and `Capturing Reality` install folders). Override with the
  `RS_EXECUTABLE` environment variable or `"realityscan": {"executable": ...}`
  in `rs_settings.json`.
- Python 3.11+ with `inquirer`, `tqdm`, and the imaging/geo dependencies used
  by the individual modules.
- One or more CUDA GPUs. RealityScan uses **all** GPUs by default; see
  [Multi-GPU](#multi-gpu) to pin instances to specific GPUs.

## Repository layout

| Path | Purpose |
|---|---|
| `main.py` | Interactive orchestrator: Extract Images → Georeference → Batch Directory → RealityScan Alignment |
| `geoall.py` | Standalone georeferencing (ROV nav CSV → RealityScan flight logs). The most up-to-date georeferencing implementation. |
| `decimator.py` | Copy a percentage of images to a new folder (dataset thinning) |
| `masking.py` | Rename `cam*_TIMESTAMP.jpg` → `TIMESTAMP_cam*.jpg` and validate JPEG integrity |
| `organize_by_date.py` | Sort images into per-date subfolders (was `test.py`) |
| `module_base/` | Framework: `RSModule` base class, `Parameter`, `SettingsStore` |
| `modules/realityscan_interface/` | Everything that talks to RealityScan — see below |
| `modules/extract_images/`, `modules/georeference/`, `modules/image_batcher/` | Pipeline modules used by `main.py` |
| `archive/colmap/` | Retired COLMAP scripts (reference only) |
| `flightlogs.xml`, `sensorsdb.xml` | RealityScan reference data |

### Known duplication

`geoall.py` (standalone) and `modules/georeference/georeference_images.py`
(pipeline module) implement the same georeferencing workflow. The standalone
is the newer, faster implementation (multiprocessing + binary-search
timestamp matching); the module is the version wired into `main.py`. Prefer
`geoall.py` for standalone runs. When the module needs improvements, port
them from `geoall.py` rather than diverging further.

## Persisted settings (`rs_settings.json`)

All standalone scripts and `main.py` prompts remember your last answers.
Values are stored in `rs_settings.json` at the repo root (gitignored,
human-editable) via `module_base/settings_store.py`, and offered as the
default on the next run — press Enter to reuse them.

Reserved section `"realityscan"`:

```json
{
  "realityscan": {
    "executable": "C:\\Program Files\\Epic Games\\RealityScan_2.2\\RealityScan.exe",
    "instance_name": "RS1",
    "gpu_devices": "0,1"
  }
}
```

All keys are optional; omit the file entirely for auto-detection and
defaults.

## How RealityScan execution works (read before touching it)

**Every** RealityScan run goes through one execution layer —
`modules/realityscan_interface/realityscan_cli.py` on the Python side and
the shared `:run` pattern in the `RS_CLI/Scripts/*.bat` workflow scripts.
Do not add new code that shells out to RealityScan directly; reuse this
layer so monitoring and race-condition handling stay uniform.

The design (informed by hard-won lessons — see
[Lessons learned](#lessons-learned)):

1. `startRealityScan.bat` boots one persistent **headless** instance named
   `RS1` (`-setInstanceName`), or attaches to it with a fresh scene if it
   already exists, and waits for readiness by polling `-getStatus` (bounded
   at 120 s).
2. The instance is started with RealityScan's built-in monitoring hooks
   (all marker files are namespaced per instance so parallel instances
   stay isolated):
   - `-writeProgress Errors\progress_<instance>.txt 600` — progress
     stream, tailed live by `RealityScanCLI` for logging and stall
     warnings;
   - `appProcessAction=ExecuteProgram` + `appProcessExecCmd` →
     `Errors\ErrorWriter.bat` — RealityScan itself reports every finished
     process (`$(processResult)`, `$(processId)`, `$(processDuration)`).
     Completions append to `results_<instance>.log`; failures (result
     codes other than 0/1) append to `errors_<instance>.txt`;
   - `-silent <Errors dir>` so crash dialogs can never hang an unattended
     run (a crash exits with code 3 and a minidump instead).
3. Workflow scripts execute every operation through the `:run` subroutine:
   `-delegateTo <instance> <cmd>` → loop `-waitCompleted` until
   `results_<instance>.log` grows (event-driven completion via
   RealityScan's own process trigger; `-waitCompleted` alone can return
   prematurely before the instance picks the queued command up), bounded
   so commands that never register as a process cannot hang → abort the
   workflow if `errors_<instance>.txt` is non-empty. One command per
   delegation, always.
4. `RealityScanCLI.run_batch_script()` wraps the whole workflow:
   - a per-instance **lock file** (with PID liveness check) prevents two
     orchestrators from driving the same instance name concurrently;
   - a leftover instance from an interrupted run is shut down (never
     silently attached to) before the workflow starts;
   - marker files are cleared before each run so stale state can never be
     misread, and read back only **after** verified shutdown so a failure
     in the final save can never be missed;
   - **no overall timeout** — alignment/reconstruction on large datasets
     legitimately runs 10+ hours; a stall only logs a warning after 2 h of
     silence;
   - after the workflow ends, the instance is verified to have actually
     shut down via `-getStatus` before the next run may start, so
     consecutive runs can never share a scene.
5. Completion is never inferred from process names. (Historical bug: the
   old code polled `tasklist` for `RealityCapture.exe` after the executable
   had been renamed `RealityScan.exe`, so the wait always returned
   immediately and raced ahead of the CLI.)

### Multi-GPU

RealityScan uses every CUDA GPU by default (`sfmGPUAcceleration=true` in
`Metadata/AlignmentParams.xml`) — a single instance already benefits from
the multi-GPU machine with no configuration.

To run **parallel instances pinned to specific GPUs** (e.g. two zones at
once), give each its own instance name and GPU set:

- Python: `RealityScanCLI(logger, instance_name="RS_GPU0")` and
  `run_batch_script(..., gpu_devices="0")`, or set `instance_name` /
  `gpu_devices` in `rs_settings.json`;
- Batch: set `RS_INSTANCE=RS_GPU0` and `RS_GPU_DEVICES=0` before calling a
  workflow script (`RS_GPU_DEVICES` is exported as `CUDA_VISIBLE_DEVICES`
  for the launched instance).

The per-instance lock makes concurrent same-instance runs fail fast instead
of corrupting each other.

## Lessons learned

Collected from prior iterations of this repo (some of which only survive in
git history — see `git log`):

- **Delegation pickup race**: `-waitCompleted` returns prematurely when
  called before the instance has picked up the queued command. Mitigation:
  wait until RealityScan's own process trigger (`ErrorWriter.bat` →
  `results_<instance>.log`) confirms the operation finished; the trigger
  is also the authoritative per-operation result.
- **No operation timeouts**: 10+ hour alignments are normal on these
  datasets. Only *startup* (120 s) and *shutdown* (300 s) are bounded.
- **Never detect completion by process name** — see the
  `RealityCapture.exe`/`RealityScan.exe` bug above.
- **Suppress dialogs for unattended runs**: `-silent` + `appAutoSaveMode=false`;
  a modal dialog on a headless box hangs the pipeline forever.
- **`-set` keys changed with the RealityScan rename**: the app settings are
  `appQuitOnError`, `appProcessAction`, `appProcessExecCmd`,
  `appProcessActionTime` — the legacy `RealityCapture*` key names the old
  scripts used are not valid in 2.x.
- **Network drives are slow for RealityScan file operations** — export to a
  local disk first, then copy to network storage.
- **One instance, one orchestrator** — enforced by the lock file.

## Typical workflows

Full interactive pipeline:

```
python main.py
```

Standalone zone alignment (from `RS_CLI/Scripts`):

```
AlignZonesSequentially.bat "D:\zones\zone_01" "D:\zones\zone_01\components"
```

Standalone georeferencing:

```
python geoall.py
```

All prompts default to your previous answers (see `rs_settings.json`).
