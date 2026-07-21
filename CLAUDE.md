# CLAUDE.md — project context for RC_Main

ROV underwater photogrammetry pipeline driving **RealityScan 2.2** (Epic
Games; the product formerly named RealityCapture) via its CLI. Runs on
Windows with a multi-GPU CUDA setup.

## Naming

Everything in this repo says **RealityScan** (`RS`), never RealityCapture.
Exceptions that must NOT be renamed:
- RealityScan API identifiers that happen to be current product strings
  (e.g. `reader="RealityScan.Import.CSVFlightLog"` in `flightlogs.xml`,
  feature-detector ids in `Metadata/AlignmentParams.xml`);
- legacy file extensions `.rcalign`/`.rcproj`, still accepted when reading
  old outputs (new saves use `.rsproj`).

## Architecture

- `main.py` — interactive orchestrator over the `RSModule` framework
  (`module_base/rs_module.py`): Extract Images → Georeference → Batch
  Directory → RealityScan Alignment.
- `modules/realityscan_interface/` — the ONLY place RealityScan is
  executed:
  - `realityscan_cli.py` — unified execution layer (`RealityScanCLI`).
    All new RealityScan-invoking code must go through it. It owns
    executable discovery, per-instance lock files, marker-file hygiene,
    progress tailing, stall warnings, and verified instance shutdown.
  - `RS_CLI/Scripts/*.bat` — workflow definitions. Every operation runs
    through the shared `:run` subroutine: `-delegateTo %RS_INSTANCE%` →
    double `-waitCompleted` with a grace period → abort if
    `RS_CLI/Errors/errors.txt` is non-empty.
  - `RS_CLI/Errors/ErrorWriter.bat` — invoked by RealityScan itself
    (`appProcessAction=ExecuteProgram`); appends every completion to
    `results.log`, failures to `errors.txt`.
  - `RS_CLI/Metadata/*.xml` — parameter presets passed to CLI commands.
- Standalone scripts at repo root (`geoall.py`, `decimator.py`,
  `masking.py`, `organize_by_date.py`) — data prep; they do not invoke
  RealityScan.
- `module_base/settings_store.py` — persists last-entered prompt answers
  to `rs_settings.json` (repo root, gitignored) and offers them as
  defaults. All user-facing path prompts must go through it.
- `archive/colmap/` — retired COLMAP scripts; do not resurrect into the
  active pipeline.

## RealityScan 2.2 CLI facts (verified against Epic docs)

- Delegated commands (`-delegateTo <instance> <cmd>`) are QUEUED; the
  delegating process can return before the operation finishes.
- `-waitCompleted <instance>` blocks until the current process finishes
  but can return prematurely if issued before the instance picks up the
  queued command — hence the double-wait in `:run`.
- `-getStatus <instance>` → errorlevel 0 iff the instance exists (used
  for readiness and shutdown verification).
- App settings use `app*` key names (`appQuitOnError`, `appAutoSaveMode`,
  `appProcessAction`, `appProcessActionTime`, `appProcessExecCmd`). The
  legacy `RealityCapture*` key names are dead.
- Exit codes: 0 = success; with `appQuitOnError=true` the error's decimal
  code; 3 = crash (minidump written to the `-silent` path).
- Multi-GPU: RealityScan uses all CUDA GPUs by default. Pin instances via
  `RS_INSTANCE` + `RS_GPU_DEVICES` (exported as `CUDA_VISIBLE_DEVICES`),
  one instance name per GPU set.

## Hard rules

1. Never add a second way to launch/monitor RealityScan — extend
   `RealityScanCLI` and the `:run` pattern instead.
2. Never infer completion from process names (`tasklist`); the pre-2.x
   code did that with `RealityCapture.exe` and silently broke.
3. No overall timeouts on RealityScan operations — 10+ hour runs are
   normal. Startup (120 s) and shutdown (300 s) are the only bounds.
4. Clear `progress.txt` / `errors.txt` / `results.log` only through
   `RealityScanCLI` (it does this pre-run); they are the source of truth
   while a run is live.
5. Data lives on large local/NAS volumes with user-specific paths — never
   hardcode them. Use `SettingsStore` prompts with the previous value as
   default.
6. `geoall.py` is the canonical georeferencing implementation; port
   improvements from it into `modules/georeference/` rather than letting
   the two diverge further.

## History notes

An earlier, richer iteration (delegation client, GUI, tests, docs) was
reverted by the `main_v2` merge — it survives only in git history around
commit `4bc8549`. Its race-condition lessons are baked into the current
execution layer; consult it before re-deriving old solutions.
