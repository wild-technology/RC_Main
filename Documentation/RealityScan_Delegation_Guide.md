# RealityScan Delegation Guide: Commanding an Open Instance

## Overview

RealityScan (formerly RealityCapture) supports **command delegation** — the ability to send CLI commands to an already-running instance of the application from an external process (script, batch file, Python program). This is the foundational mechanism for automating complex photogrammetry workflows without launching a new instance for each operation.

This guide is written **independently of any specific codebase implementation** and covers the delegation API from first principles.

---

## 1. Core Concepts

### 1.1 Instances and Instance Names

When RealityScan launches, it registers itself as a named instance. By default, the instance name follows the pattern `RS1`, `RS2`, etc. You can also launch with a specific name:

```
RealityScan.exe -setInstanceName MyInstance
```

The wildcard `*` targets the **first available instance**, which is the most common pattern for single-instance workflows.

### 1.2 Two Execution Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Direct CLI** | Launch RealityScan with a command chain; it opens, executes all commands sequentially, and (optionally) quits | One-shot batch processing |
| **Delegation** | Send commands to an *already running* instance from an external process | Interactive automation, multi-step pipelines, monitoring |

**Direct CLI example** (synchronous, self-contained):
```
RealityScan.exe -newScene -addFolder C:\Images\ -align -save C:\project.rsproj -quit
```

**Delegation example** (asynchronous, to a running instance):
```
RealityScan.exe -delegateTo * -align
```

### 1.3 Why Delegation Matters

- **Persistent state**: The project stays open between commands. You can inspect results in the GUI between steps.
- **Selective execution**: Run only the commands you need, skip steps, retry failed operations.
- **Progress monitoring**: Poll the instance for real-time status (progress %, ETA, elapsed time).
- **Safe abort**: Cancel long-running operations without killing the process.
- **Checkpoint/resume**: Save after each major step; resume from the last checkpoint on failure.

---

## 2. The Delegation Command Set

### 2.1 `-delegateTo <instance> <command> [args...]`

Queues a command (or chain of commands) for execution by the target instance.

```
RealityScan.exe -delegateTo * -align
RealityScan.exe -delegateTo RS1 -add D:\images\img001.JPG
RealityScan.exe -delegateTo * -simplify C:\params.xml
```

**Behavior**:
- Returns immediately after queuing (does NOT wait for execution).
- Commands are queued FIFO — they execute in the order received.
- You can chain multiple commands in one delegation call:
  ```
  RealityScan.exe -delegateTo * -selectAllImages -enableAlignment false
  ```

**Critical caveat**: Because delegation returns immediately, you must use a wait mechanism to know when the operation finishes.

### 2.2 `-waitCompleted <instance>`

Blocks the calling process until the target instance finishes its current operation.

```
RealityScan.exe -delegateTo * -align
RealityScan.exe -waitCompleted *
echo Alignment finished.
```

**Behavior**:
- Blocks until the instance reports idle.
- If the instance is already idle when called, returns immediately.

**Known limitation**: `-waitCompleted` can return prematurely if called before the instance picks up the queued command (race condition). For long operations, combine with status polling (see Section 3).

### 2.3 `-getStatus <instance>`

Queries the current status of the target instance. Returns a string to stdout.

```
RealityScan.exe -getStatus *
```

**Output format**:
```
id:0x10001 progress:57.5% runtime:4.26sec endEstimation:3.40sec
```

**Status fields**:

| Field | Description |
|-------|-------------|
| `id` | Operation identifier. `0xffffffff` = idle/no operation |
| `progress` | Completion percentage (0.0% to 100.0%) |
| `runtime` | Elapsed time for current operation |
| `endEstimation` | Estimated remaining time |
| `rev` | Revision counter (increments on state changes) |
| `lastError` | Last error code (0 = no error) |

**Idle detection**:
- `id:0xffffffff` with `progress:0.0%` = idle
- `progress:100.0%` = operation just completed
- The word "idle" may appear in some status strings

### 2.4 `-abortInstance <instance>`

Aborts the current operation and clears the command queue.

```
RealityScan.exe -abortInstance *
```

**Use cases**:
- Cancel a long-running alignment or model calculation.
- Clear queued commands from a previous failed script run.
- Graceful shutdown handler (Ctrl+C).

### 2.5 `-newInstance <name>`

Creates a new named instance of RealityScan.

```
RealityScan.exe -newInstance MyWorker
```

---

## 3. Reliable Command Execution Patterns

### 3.1 Basic Pattern: Delegate + Wait

The simplest pattern for commands that complete quickly:

```batch
RealityScan.exe -delegateTo * -selectAllImages
RealityScan.exe -waitCompleted *
RealityScan.exe -delegateTo * -enableAlignment false
RealityScan.exe -waitCompleted *
```

**When to use**: Fast operations (selection, renaming, saving, parameter changes).

### 3.2 Robust Pattern: Two-Phase Idle Detection

For long operations (alignment, model calculation, texturing), the basic pattern is unreliable because `-waitCompleted` may return before the operation starts. The robust solution:

**Phase 1 — Wait for the operation to START** (transition from idle to busy):
- Poll `-getStatus` until the operation ID changes, or progress appears, or the idle flag clears.

**Phase 2 — Wait for the operation to COMPLETE** (return to idle):
- Continue polling until idle state is confirmed (triple-check recommended).

```python
# Pseudocode
def execute_and_wait(command, args):
    # Get baseline status
    initial_status = get_status()

    # Send command
    delegate(command, *args)
    sleep(1.5)  # Give RC time to pick up the command

    # PHASE 1: Wait for operation to start
    while not operation_started:
        status = get_status()
        if status.id != initial_status.id:
            operation_started = True
        elif was_idle and not status.is_idle:
            operation_started = True
        sleep(1.0)

    # PHASE 2: Wait for operation to complete
    while True:
        status = get_status()
        if status.is_idle:
            # Triple-check to avoid false positives
            sleep(0.5)
            if get_status().is_idle:
                sleep(0.5)
                if get_status().is_idle:
                    break
        sleep(2.0)
```

**When to use**: `-align`, `-calculateHighModel`, `-calculateNormalModel`, `-calculateTexture`, `-simplify` (large models), `-unwrap`, `-reprojectTexture`.

### 3.3 One-Command-Per-Delegation Pattern

For pipelines where safe abort is critical, send exactly **one command per delegation call**:

```python
# Instead of:
delegate("-selectComponent", name, "-calculateHighModel", "-simplify")

# Do:
delegate("-selectComponent", name)
wait_until_idle()
delegate("-calculateHighModel")
wait_until_idle()
delegate("-simplify")
wait_until_idle()
```

**Why**: If you queue multiple commands and need to abort, `-abortInstance` cancels the current operation but doesn't clear subsequently queued commands predictably. One-command-per-call ensures you can stop between any two steps.

### 3.4 Startup Queue Clear

Always clear the command queue at script startup to avoid executing leftover commands from a previous failed run:

```python
delegate("-abortInstance")
sleep(1.0)
# Now safe to begin
```

---

## 4. Command Categories for Delegation

### 4.1 Image Management

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-add` | `<filepath>` or `<imagelist.txt>` | Add image(s) to project |
| `-addFolder` | `<dirpath>` | Add all images from directory |
| `-selectAllImages` | | Select all images |
| `-deselectAllImages` | | Deselect all images |
| `-selectImage` | `<pattern>` | Select images matching pattern (supports regex with `g/pattern/`) |
| `-enableAlignment` | `true\|false` | Enable/disable alignment for selected images |
| `-editInputSelection` | `"key=value"` | Edit properties of selected images (focal, distortion, calibration, etc.) |
| `-setPriorCalibrationGroup` | `<group_id>` | Set calibration group for selected images |
| `-setPriorLensGroup` | `<group_id>` | Set lens group for selected images |

### 4.2 Alignment

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-align` | `[params.xml]` | Run image alignment |
| `-importFlightLog` | `<filepath> [params.xml]` | Import GPS/pose flight log |
| `-exportXMP` | | Export XMP sidecar files next to images |
| `-set` | `key=value` | Set alignment/export parameters (e.g., `xmpCamera=3`) |

### 4.3 Component Management

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-selectComponent` | `<name>` | Select component by name |
| `-selectMaximalComponent` | | Select largest component |
| `-setMinComponentSize` | `<int>` | Set minimum component image count |
| `-exportComponent` | `<dirpath>` | Export all components |
| `-exportSelectedComponentFile` | `<filepath>` | Export selected component as .rsalign |
| `-exportLatestComponents` | `<dirpath>` | Export components from latest alignment |
| `-importComponent` | `<pattern>` | Import component file(s) |

### 4.4 Reconstruction

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-setReconstructionRegionAuto` | | Auto-set reconstruction region from point cloud |
| `-scaleReconstructionRegion` | `<x> <y> <z> center factor` | Scale region (e.g., `2 2 2 center factor` = double) |
| `-calculatePreviewModel` | | Generate preview-quality mesh |
| `-calculateNormalModel` | `[params.xml]` | Generate normal-quality mesh |
| `-calculateHighModel` | `[params.xml]` | Generate high-quality mesh |

### 4.5 Model Processing

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-selectMarginalTriangles` | | Select low-quality triangles |
| `-selectLargeTrianglesRel` | `<factor>` | Select triangles larger than factor * median |
| `-selectLargestModelComponent` | | Select largest connected mesh part |
| `-invertTrianglesSelection` | | Invert triangle selection |
| `-removeSelectedTriangles` | | Delete selected triangles |
| `-simplify` | `[count\|params.xml]` | Simplify mesh (by count or XML params) |
| `-smooth` | `[params.xml]` | Smooth mesh surface |
| `-cleanModel` | | Fix geometry issues (non-manifold edges, etc.) |
| `-closeHoles` | `[max_edges]` | Close holes (optional max edge count) |
| `-unwrap` | `[params.xml]` | UV unwrap for texturing |
| `-calculateTexture` | `[params.xml]` | Generate texture maps |
| `-reprojectTexture` | `<source_model> <target_model> [params.xml]` | Reproject texture between models |

### 4.6 Model Selection and Naming

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-selectModel` | `<name>` | Select model by name |
| `-renameSelectedModel` | `<new_name>` | Rename the currently selected model |

### 4.7 Export

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-exportModel` | `<model_name> <filepath> [params.xml]` | Export named model |
| `-exportSelectedModel` | `<filepath> [params.xml]` | Export currently selected model |
| `-export3dTiles` | `<filepath> [params.xml]` | Export as Cesium 3D Tiles |
| `-exportOrthoProjection` | `<filepath> [params.xml]` | Export orthographic projection |

### 4.8 Project Management

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-newScene` | | Create new empty scene |
| `-load` | `<filepath.rsproj>` | Load project |
| `-save` | `[filepath.rsproj]` | Save project (optional path for save-as) |
| `-quit` | | Close the instance |
| `-draft` | | Close without saving (discard changes) |

### 4.9 Error Handling

| Command | Arguments | Description |
|---------|-----------|-------------|
| `-getLastError` | | Query last error code |
| `-set` `progressFile=<path>` | | Write progress to file |
| `-set` `errorFile=<path>` | | Write errors to file |

---

## 5. Complete Workflow Examples

### 5.1 Batch Alignment with Delegation (Batch Script)

```batch
@echo off
set RC="C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"

:: Add images from multiple folders
FOR /L %%A IN (1,1,10) DO (
    %RC% -delegateTo * -addFolder "D:\Images\Zone_%%A"
    %RC% -waitCompleted *
)

:: Import flight log
%RC% -delegateTo * -importFlightLog "D:\flightlog.txt"
%RC% -waitCompleted *

:: Run alignment
%RC% -delegateTo * -align
%RC% -waitCompleted *

:: Save project
%RC% -delegateTo * -save "D:\output\aligned.rsproj"
%RC% -waitCompleted *
```

### 5.2 Sequential Batch Alignment (Python)

Process image batches one at a time, enabling/disabling alignment per batch:

```python
# 1. Load all images
delegate("-add", imagelist_path)
wait_completed()

# 2. Disable alignment for everything
delegate("-selectAllImages", "-enableAlignment", "false")
wait_completed()

# 3. For each batch:
for batch_name in batches:
    # Enable only this batch
    delegate("-deselectAllImages",
             "-selectImage", f"g/{batch_name}/",
             "-enableAlignment", "true")
    wait_completed()

    # Align
    delegate("-align")
    monitor_until_complete("Alignment")

    # Disable batch
    delegate("-deselectAllImages",
             "-selectImage", f"g/{batch_name}/",
             "-enableAlignment", "false")
    wait_completed()

    # Checkpoint save
    delegate("-save", project_path)
    wait_completed()
```

### 5.3 Model Generation Pipeline (Python)

Full pipeline from aligned component to exported model:

```python
# Select component
delegate("-selectComponent", component_name)
wait_idle()

# Set reconstruction region
delegate("-setReconstructionRegionAuto")
wait_idle()
delegate("-scaleReconstructionRegion", "2", "2", "2", "center", "factor")
wait_idle()

# Generate mesh
delegate("-calculateHighModel")
wait_idle()  # This is LONG — use progress monitoring

# Clean up mesh
delegate("-selectMarginalTriangles")
wait_idle()
delegate("-removeSelectedTriangles")
wait_idle()
delegate("-selectLargeTrianglesRel", "2.0")
wait_idle()
delegate("-removeSelectedTriangles")
wait_idle()
delegate("-selectLargestModelComponent")
wait_idle()
delegate("-invertTrianglesSelection")
wait_idle()
delegate("-removeSelectedTriangles")
wait_idle()
delegate("-cleanModel")
wait_idle()
delegate("-smooth")
wait_idle()
delegate("-closeHoles", "5000")
wait_idle()
delegate("-cleanModel")
wait_idle()

# Texture
delegate("-calculateTexture")
wait_idle()

# Rename as HighPoly (preserve textured source)
delegate("-renameSelectedModel", f"{name}_HighPoly")
wait_idle()

# Simplify for LowPoly
delegate("-simplify", params_xml)
wait_idle()
delegate("-closeHoles", "600000")
wait_idle()
delegate("-simplify", params_xml)
wait_idle()
delegate("-closeHoles")
wait_idle()
delegate("-renameSelectedModel", f"{name}_LowPoly")
wait_idle()

# UV unwrap and reproject
delegate("-unwrap")
wait_idle()
delegate("-reprojectTexture", f"{name}_HighPoly", f"{name}_LowPoly")
wait_idle()

# Save
delegate("-save")
wait_idle()

# Export
delegate("-exportModel", f"{name}_LowPoly", fbx_path)
wait_idle()
delegate("-selectModel", f"{name}_HighPoly")
wait_idle()
delegate("-export3dTiles", cesium_path)
wait_idle()
```

### 5.4 Camera Parameter Setup (Python)

Apply calibration settings by folder-based camera group:

```python
for camera_group in camera_groups:
    # Deselect all
    delegate("-deselectAllImages")
    wait_completed()

    # Select by path pattern
    delegate("-selectImage", f"g/{camera_group.keyword}/")
    wait_completed()

    # Apply settings
    delegate("-editInputSelection", f'"inpCalibration=1"')
    wait_completed()
    delegate("-editInputSelection", f'"inpFocal={camera_group.focal_mm}"')
    wait_completed()
    delegate("-editInputSelection", f'"inpDistortionModel={camera_group.distortion_code}"')
    wait_completed()
    delegate("-editInputSelection", f'"inpAbsolutePose=2"')
    wait_completed()

    # Set groups
    delegate("-setPriorCalibrationGroup", str(camera_group.calib_id))
    wait_completed()
    delegate("-setPriorLensGroup", str(camera_group.lens_id))
    wait_completed()
```

### 5.5 Iterative Simplification (Batch Script)

From the Epic Games example — delegate simplification multiple times:

```batch
set RC="C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe"
set Params=%RootFolder%simplificationParameters.xml
set Iterations=3

FOR /L %%A IN (1,1,%Iterations%) DO (
    %RC% -delegateTo * -simplify %Params%
)
```

---

## 6. Image Selection Patterns

The `-selectImage` command supports powerful pattern matching:

| Pattern | Description |
|---------|-------------|
| `"image001.jpg"` | Exact filename match |
| `g/pattern/` | Regex match against full image path |
| `g/camlower/` | All images with "camlower" in path |
| `g/[/\\]Zone_01[/\\]/` | Images in "Zone_01" directory (exact directory boundary match) |

**Regex examples for folder-based selection**:
```python
# Match images in "Zone_1" but not "Zone_10" or "Zone_11"
pattern = "g/[/\\\\]Zone_1[/\\\\]/"

# Match all camera lower images
pattern = "g/camlower/"

# Match images starting with "IMG_"
pattern = "g/IMG_/"
```

---

## 7. XML Parameter Files

Many commands accept an optional XML parameter file that overrides default settings. These are exported from the RealityScan GUI:

- **Simplification**: Target triangle count, preservation settings
- **Texturing**: Texture count, resolution, texel size, quality
- **Unwrapping**: UV layout method, island padding
- **Export**: Format-specific settings (FBX UV tiling, material options)
- **Smoothing**: Iteration count, strength
- **Reprojection**: Normal map generation, displacement maps
- **Alignment**: Feature detection sensitivity, matching parameters

**To create parameter files**: Configure settings in the RealityScan GUI, then export via the application's export settings dialog.

---

## 8. Error Handling and Recovery

### 8.1 Detecting Errors

```python
# Check last error after an operation
delegate("-getLastError")
result = subprocess.run([rc_exe, "-getStatus", "*"], capture_output=True, text=True)
# Parse lastError field from status output

# Or use error file redirection
delegate("-set", "errorFile=C:\\errors.txt")
```

### 8.2 Recovery Strategies

1. **Checkpoint saves**: Save the project after each major step. On failure, reload the last checkpoint.
2. **Abort + retry**: Send `-abortInstance`, wait, then retry the failed command.
3. **Skip + continue**: Log the failure, skip to the next component/zone.
4. **Revision tracking**: Use the `rev` field from `-getStatus` to detect whether a command actually executed (revision increments on state changes).

### 8.3 Signal Handling

For Python scripts, register a signal handler that sends `-abortInstance` before exiting:

```python
import signal

def handler(signum, frame):
    subprocess.run([rc_exe, "-abortInstance", "*"])
    sys.exit(1)

signal.signal(signal.SIGINT, handler)
signal.signal(signal.SIGTERM, handler)
```

---

## 9. Performance Considerations

- **Poll interval**: 2-5 seconds is a good balance between responsiveness and overhead.
- **Grace periods**: After operations complete, wait 0.5-3 seconds before starting the next command to allow internal file writes to finish.
- **Queue depth**: Avoid queuing hundreds of commands — send them one at a time with wait.
- **Network drives**: Export to local disk first, then copy to network storage. RC file operations may be slow over network shares.
- **Memory**: Large models (>50M triangles) require significant RAM. Monitor system resources during model calculation.

---

## 10. Platform Notes

- RealityScan.exe is **Windows-only**. All delegation commands execute via Windows command line.
- The delegation mechanism works via named pipes / shared memory — the calling process and the RC instance must be on the **same machine**.
- For remote automation, use SSH/RDP to execute commands on the Windows machine, or deploy a small REST API wrapper.
- Common executable locations:
  - `C:\Program Files\Epic Games\RealityScan_2.1\RealityScan.exe`
  - `C:\Program Files\Epic Games\RealityScan_2.0\RealityScan.exe`
  - `C:\Program Files\Epic Games\RealityScan\RealityScan.exe`
