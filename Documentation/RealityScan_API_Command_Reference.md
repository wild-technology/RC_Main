# RealityScan API Command & Execution Reference

## Quick Reference: Execution Modes

| Mode | Syntax | Waits? | Use Case |
|------|--------|--------|----------|
| **Direct** | `RealityScan.exe -command1 -command2 -quit` | Yes (sequential) | One-shot batch processing |
| **Delegate** | `RealityScan.exe -delegateTo <inst> -command` | No (queues) | Automation of running instance |
| **Wait** | `RealityScan.exe -waitCompleted <inst>` | Yes (blocks) | Synchronize after delegation |
| **Status** | `RealityScan.exe -getStatus <inst>` | No (instant) | Monitor progress |
| **Abort** | `RealityScan.exe -abortInstance <inst>` | No (instant) | Cancel + clear queue |

**Instance targeting**: Use `*` for first available instance, or a specific name like `RS1`.

---

## 1. Instance Management Commands

### `-delegateTo <instance> <commands...>`
Queue commands for execution by a running instance.
```
RealityScan.exe -delegateTo * -align
RealityScan.exe -delegateTo RS1 -selectAllImages -enableAlignment false
```
**Returns**: Immediately (does not wait for execution).

### `-waitCompleted <instance>`
Block until the instance finishes its current operation.
```
RealityScan.exe -waitCompleted *
```
**Returns**: When instance becomes idle.
**Caveat**: May return before queued command starts (race condition). Use status polling for long operations.

### `-getStatus <instance>`
Query instance status. Output to stdout.
```
RealityScan.exe -getStatus *
```
**Output**: `id:0x10001 progress:57.5% runtime:4.26sec endEstimation:3.40sec rev:473 lastError:0`

| Field | Meaning |
|-------|---------|
| `id` | Operation ID (`0xffffffff` = idle) |
| `progress` | 0.0% to 100.0% |
| `runtime` | Elapsed time |
| `endEstimation` | ETA |
| `rev` | State revision counter |
| `lastError` | Last error code (0 = none) |

### `-abortInstance <instance>`
Abort current operation and clear command queue.
```
RealityScan.exe -abortInstance *
```

### `-newInstance <name>`
Create a new named instance.
```
RealityScan.exe -newInstance MyWorker
```

### `-setInstanceName <name>`
Set the name of the current instance.

---

## 2. Project Management Commands

### `-newScene`
Create a new empty scene (usually not needed — application launches with a new scene).

### `-load <filepath.rsproj>`
Load a project file.
```
RealityScan.exe -load C:\projects\myproject.rsproj
```

### `-save [filepath.rsproj]`
Save the current project. Optional path for save-as.
```
-save                              # Save to current location
-save C:\projects\backup.rsproj    # Save-as
```

### `-quit`
Close the instance after all queued commands finish.

### `-draft`
Close without saving (discard unsaved changes).

---

## 3. Image Input Commands

### `-add <path>`
Add image(s). Accepts single file or `.imagelist` text file (one path per line).
```
-add C:\images\img001.JPG
-add C:\images\imagelist.txt
```

### `-addFolder <dirpath>`
Add all supported images from a directory (recursive).
```
-addFolder C:\dataset\images\
```

**Supported formats**: JPG, JPEG, PNG, HEIF, TIF, TIFF

### `-importFlightLog <filepath> [params.xml]`
Import GPS/pose data from a flight log file (CSV, semicolon-delimited).
```
-importFlightLog C:\dataset\flight_log_17S_UTM.txt
-importFlightLog C:\dataset\flight_log.txt C:\params\FlightLogParams.xml
```

---

## 4. Image Selection Commands

### `-selectAllImages`
Select all images in the project.

### `-deselectAllImages`
Deselect all images.

### `-selectImage <pattern>`
Select images matching a pattern. Supports regex via `g/pattern/` syntax.

```
-selectImage "image001.jpg"              # Exact match
-selectImage "g/camlower/"               # Path contains "camlower"
-selectImage "g/[/\\\\]Zone_01[/\\\\]/"  # Exact directory boundary match
```

### `-enableAlignment <true|false>`
Enable or disable alignment for currently selected images.
```
-selectAllImages -enableAlignment false    # Disable all
-selectImage "g/batch_1/" -enableAlignment true  # Enable batch_1 only
```

---

## 5. Image Property Commands

### `-editInputSelection "key=value"`
Edit properties of selected images. Key properties:

| Key | Values | Description |
|-----|--------|-------------|
| `inpCalibration` | `0`=None, `1`=Approximate | Prior calibration quality |
| `inpFocal` | float (mm) | Prior focal length |
| `inpDistortion` | `0`=None, `1`=Known | Distortion model prior |
| `inpDistortionModel` | 1-7 (see below) | Distortion model type |
| `inpAbsolutePose` | `0`=None, `1`=Position, `2`=Position+Orientation | Pose prior |
| `inpPriorCalibration` | `0`=None, `1`=Approximate | Prior calibration |

**Distortion model codes**:
1. Division
2. Brown3
3. Brown4
4. Brown3WithTangential2
5. Brown4WithTangential2
6. KplusBrown3WithTangential2
7. KplusBrown4WithTangential2

### `-setPriorCalibrationGroup <group_id>`
Set calibration group for selected images (integer ID).

### `-setPriorLensGroup <group_id>`
Set lens group for selected images (integer ID).

---

## 6. Alignment Commands

### `-align [params.xml]`
Run image alignment (structure from motion).
```
-align
-align C:\params\AlignmentParams.xml
```
**Duration**: Minutes to hours depending on image count. Use progress monitoring.

### `-exportXMP`
Export XMP sidecar files next to source images (contains calibration, pose, flags).

### XMP Export Settings (set before `-exportXMP`):
```
-set xmpCamera=3         # Camera calibration detail level
-set xmpMerge=true       # Include merge info
-set xmpRig=true         # Include rig info
-set xmpCalibGroups=true # Include calibration groups
-set xmpFlags=true       # Include flag data
-set xmpExGps=true       # Include GPS coordinates
```

---

## 7. Component Commands

### `-selectComponent <name>`
Select a component by name.
```
-selectComponent "NA168_H2080_zone_001_20250129_1430_1"
```

### `-selectMaximalComponent`
Select the component with the most aligned cameras.

### `-setMinComponentSize <count>`
Set minimum image count for component export filtering.
```
-setMinComponentSize 100
```

### `-exportComponent <dirpath>`
Export all components as .rsalign files to directory.

### `-exportSelectedComponentFile <filepath>`
Export the currently selected component as a single .rsalign file.
```
-exportSelectedComponentFile C:\output\component_01.rsalign
```

### `-exportLatestComponents <dirpath>`
Export components from the most recent alignment.
```
-exportLatestComponents C:\output\components\
```

### `-importComponent <pattern>`
Import component file(s). Supports `%s` wildcard for batch import.
```
-importComponent "C:\components\*.rsalign"
-importComponent "C:\components\%s"
```

---

## 8. Reconstruction Commands

### `-setReconstructionRegionAuto`
Automatically set the reconstruction bounding box to cover the aligned point cloud.

### `-scaleReconstructionRegion <x> <y> <z> center factor`
Scale the reconstruction region. Common usage: double the region.
```
-scaleReconstructionRegion 2 2 2 center factor
```

### `-moveReconstructionRegion <x> <y> <z>`
Translate reconstruction region by offset values.

### `-rotateReconstructionRegion <rx> <ry> <rz>`
Rotate reconstruction region by angles (degrees).

### `-offsetReconstructionRegion <x> <y> <z>`
Offset reconstruction region boundaries.

### `-exportReconstructionRegion <filepath.rsbox>`
Export reconstruction region to file.

### `-setReconstructionRegion <filepath.rsbox>`
Import/set reconstruction region from file.

---

## 9. Model Generation Commands

### `-calculatePreviewModel`
Generate a preview-quality mesh (fastest, lowest detail).

### `-calculateNormalModel [params.xml]`
Generate a normal-quality mesh.

### `-calculateHighModel [params.xml]`
Generate a high-quality mesh (slowest, highest detail).

**Duration**: Can take hours for large datasets. Always use progress monitoring.

---

## 10. Mesh Processing Commands

### `-selectMarginalTriangles`
Select low-confidence/low-quality triangles on the mesh boundary.

### `-selectLargeTrianglesRel <factor>`
Select triangles larger than `factor` times the median triangle size.
```
-selectLargeTrianglesRel 2.0    # Select triangles >2x median
-selectLargeTrianglesRel 3.0    # More aggressive filtering
```

### `-selectLargestModelComponent`
Select the largest connected part of the mesh. Useful for isolating the main subject from floating fragments.

### `-invertTrianglesSelection`
Invert the current triangle selection.

### `-removeSelectedTriangles`
Delete all selected triangles from the mesh.

### `-simplify [count | params.xml]`
Simplify the mesh. Accepts target triangle count or parameter file.
```
-simplify 100000                    # Reduce to 100K triangles
-simplify C:\params\simplify.xml    # Use custom parameters
-simplify                           # Use current RC settings
```

### `-smooth [params.xml]`
Smooth the mesh surface.

### `-cleanModel`
Fix geometry issues (non-manifold edges, degenerate triangles, etc.).

### `-closeHoles [max_edges]`
Close holes in the mesh. Optional maximum hole size in edges.
```
-closeHoles            # Close all holes
-closeHoles 5000       # Only close holes with <=5000 edges
-closeHoles 600000     # Close very large holes
```

---

## 11. Texturing Commands

### `-unwrap [params.xml]`
Generate UV unwrapping for the current model.

### `-calculateTexture [params.xml]`
Calculate texture maps for the current model. Requires prior unwrap (or auto-unwraps).
```
-calculateTexture
-calculateTexture C:\params\Texturing_MaxTextureCount1_8k.xml
```

### `-reprojectTexture <source_model> <target_model> [params.xml]`
Reproject texture from a textured source model to a target model (e.g., high-poly to low-poly).
```
-reprojectTexture "Component_01_HighPoly" "Component_01_LowPoly"
-reprojectTexture "HighPoly" "LowPoly" C:\params\ReprojectionParams.xml
```
Creates normal maps and displacement maps when configured.

---

## 12. Model Selection & Naming Commands

### `-selectModel <name>`
Select a model by name.
```
-selectModel "Component_01_HighPoly"
```

### `-renameSelectedModel <new_name>`
Rename the currently selected model.
```
-renameSelectedModel "Component_01_HighPoly"
```

**Important**: Many RC operations (simplify, unwrap, calculateTexture) create a NEW model that becomes the active selection. Track the "model flow" carefully through your pipeline.

---

## 13. Export Commands

### `-exportModel <model_name> <filepath> [params.xml]`
Export a named model to file.
```
-exportModel "LowPoly" C:\output\model.obj
-exportModel "Component_01_LowPoly" C:\output\model.fbx C:\params\ModelExportParamsFBX_UDIM.xml
```

### `-exportSelectedModel <filepath> [params.xml]`
Export the currently selected model.

### `-export3dTiles <filepath> [params.xml]`
Export as Cesium 3D Tiles format.
```
-export3dTiles C:\output\model.json
```
**Note**: RealityScan prepends `tileset_` to the output filename.

### `-exportOrthoProjection <filepath> [params.xml]`
Export an orthographic projection (orthomosaic).

**Supported export formats**: OBJ, FBX, GLB/GLTF, Cesium 3D Tiles, PLY, XYZ, E57, LAS

---

## 14. Settings Commands

### `-set <key>=<value>`
Set application parameters. Key settings:

| Key | Values | Description |
|-----|--------|-------------|
| `xmpCamera` | 0-3 | XMP camera detail level |
| `xmpMerge` | true/false | Include merge data in XMP |
| `xmpRig` | true/false | Include rig data in XMP |
| `xmpCalibGroups` | true/false | Include calibration groups |
| `xmpFlags` | true/false | Include flag data |
| `xmpExGps` | true/false | Include GPS in XMP export |
| `progressFile` | filepath | Write progress to file |
| `errorFile` | filepath | Write errors to file |

---

## 15. Error Handling Commands

### `-getLastError`
Query the last error code. Returns 0 if no error.

### Progress/Error File Redirection
```
-set progressFile=C:\logs\progress.txt
-set errorFile=C:\logs\errors.txt
```

### Status Output Redirection
```
RealityScan.exe -getStatus * > D:\statusreport.txt
```

---

## 16. Available XML Parameter Files (from this repository)

Located in `modules/realitycapture_interface/RC_CLI/Metadata/`:

### Alignment
- `AlignmentParams.xml` — Alignment sensitivity and matching settings
- `FlightLogParams.xml` — Flight log import format definition

### Model Export
- `ModelExportParams.xml` — Generic model export
- `ModelExportParamsObj.xml` — OBJ format
- `ModelExportParamsGLB.xml` — GLB format
- `ModelExportParamsFBX_U1V1.xml` — FBX with U1V1 UV tiling
- `ModelExportParamsFBX_U1V1_material.xml` — FBX U1V1 + materials
- `ModelExportParamsFBX_UV.xml` — FBX standard UV
- `ModelExportParamsFBX_UDIM.xml` — FBX UDIM tiling
- `ModelExportParamsFBX_UDIM_material.xml` — FBX UDIM + materials

### Texturing
- `Texturing_MaxTextureCount1_8k.xml` — 1x 8K texture
- `Texturing_MaxTextureCount4_8k.xml` — 4x 8K textures
- `Texturing_MaxTextureCount1_16k.xml` — 1x 16K texture
- `Texturing_FixedTexelSize50perQuality.xml` — Fixed texel, 50% quality
- `Texturing_FixedTexelSize100perQuality.xml` — Fixed texel, 100% quality
- `Texturing_HighPolyTexture.xml` — High-poly optimized
- `Texturing_SimplifiedTexture.xml` — Simplified model optimized
- `ReprojectionParams.xml` — Texture reprojection settings
- `XMPExportParams.xml` — XMP sidecar export settings

### Simplification
- `Simplify500k_Params.xml` — Reduce to 500K triangles
- `Simplify50Per_Params.xml` — Reduce by 50%
- `Simplify25per_Params.xml` — Reduce by 25%
- `SimplifyAutomationParams.xml` — Automation-optimized settings

### Smoothing
- `Smoothing_02_2_Params.xml` — Strength 0.2, 2 iterations
- `SmoothingPeaks_05_5_Params.xml` — Peak smoothing, strength 0.5, 5 iterations
- `SmoothingSurface_02_2_Params.xml` — Surface smoothing, 0.2, 2 iterations

### UV Unwrapping
- `Unwrapping_Simplified.xml` — Unwrap optimized for simplified models

---

## 17. Execution Patterns Summary

### Pattern A: Fire-and-Forget (quick commands)
```python
delegate(command)
wait_completed()
```

### Pattern B: Monitored Execution (long commands)
```python
delegate(command)
# Phase 1: wait for start
while not started:
    poll_status()
# Phase 2: wait for completion
while not idle:
    poll_status()
    report_progress()
```

### Pattern C: Safe Pipeline (abort-capable)
```python
abort_instance()  # Clear queue at startup
for step in pipeline:
    if abort_requested:
        break
    delegate(step.command)
    wait_idle_two_phase()
    checkpoint_save()
```

### Pattern D: Batch Loop with Checkpoint
```python
load_checkpoint()
for batch in remaining_batches:
    enable_batch(batch)
    delegate("-align")
    monitor_until_complete()
    disable_batch(batch)
    save_project()
    save_checkpoint(batch)
```
