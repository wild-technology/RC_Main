# Standalone Utilities Integration Proposal

## Executive Summary

The `StandaloneUtilities/` folder contains five scripts that were created to fix bugs and add functionality not present in the main codestack (`main.py` + `modules/`). This document analyzes each utility, maps it against the existing main codestack, identifies the gaps, and proposes what is needed to integrate each utility's functionality.

---

## Current Architecture

### Main Codestack (`main.py`)

**Pipeline**: Sequential module execution orchestrated by `main.py`:
1. **ExtractImages** — Extract frames from video files
2. **GeoreferenceImages** — Apply GPS/pose data to images from flight logs
3. **BatchDirectory** — Cluster images into geographic zones (K-means + KDE)
4. **RealityCaptureAlignment** — Align images per zone, export components + XMP

**Architecture pattern**: Each module extends `RCModule` (abstract base class) with:
- `get_parameters()` → declares needed parameters
- `validate_parameters()` → pre-run validation
- `run()` → main logic
- Parameters managed globally via `Parameter` class + CLI argparse + interactive prompts

**Key limitation**: The main codestack's `RealityCaptureAlignment` module uses **direct CLI mode** (launches RealityScan with a full command chain, waits for exit). It does NOT use delegation to a running instance.

### Standalone Utilities (`StandaloneUtilities/`)

| Script | Lines | Purpose | Uses Delegation? |
|--------|-------|---------|-----------------|
| `BatchSequentialAlignment.py` | 548 | Sequential batch alignment with checkpoint/resume | Yes |
| `ModelGenerator.py` | 1121 | Full model generation pipeline (25 steps) | Yes |
| `SetCameraParams.py` | 306 | Apply camera calibration by folder pattern | Yes |
| `text_rsalign_exporter.py` | 541 | Export components from open project | Yes |
| `clahe1.py` | 262 | Underwater image enhancement for photogrammetry | No (image processing only) |

### AlignmentBatcher (`AlignmentBatcher/`)

| Script | Purpose |
|--------|---------|
| `rc_module_batcher4.py` | Interactive batch launcher with delegation support |
| `MakeModels.py` | Model generation pipeline (evolution of `ModelGenerator.py`) |

---

## Detailed Gap Analysis

### Gap 1: No Delegation Support in Main Codestack

**Problem**: The main `RealityCaptureAlignment` module (`modules/realitycapture_interface/realitycapture_interface.py`) launches RealityScan as a subprocess with a single command chain and waits for it to exit:

```python
# Current approach (line 382-416):
def __run_realityscan_command(self, realityscan_exe, command_list):
    full_command = [str(realityscan_exe)] + command_list
    result = subprocess.run(full_command, check=True, capture_output=True, text=True)
    return result
```

This means:
- No progress monitoring during execution
- No ability to abort mid-operation
- No checkpoint saves between steps
- If alignment fails on one zone, the whole process stops
- Cannot interact with an open RC project

**What standalone utilities do differently**: All four RC-facing utilities use delegation:
```python
# Delegation approach (from BatchSequentialAlignment.py, line 123-129):
def _delegate(self, *args):
    cmd = [str(self.rc_exe), "-delegateTo", self.instance_name] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)
```

### Gap 2: No Progress Monitoring

**Problem**: The main codestack has no way to report progress of long-running RC operations (alignment can take hours).

**What standalone utilities provide**: Both `BatchSequentialAlignment.py` and `ModelGenerator.py` implement real-time progress monitoring via `-getStatus` polling with parsed progress percentages, ETA, and elapsed time display.

### Gap 3: No Model Generation Pipeline

**Problem**: The main codestack aligns images and exports components, but has no post-alignment model generation. The `rc_model_generate`, `rc_model_texture`, `rc_model_simplify` parameters exist but are never used in the actual `run()` method — only alignment and component export happen.

**What standalone utilities provide**:
- `ModelGenerator.py` — Full 25-step pipeline: select component → high model → clean → smooth → texture → simplify → LOD → unwrap → reproject → export (FBX + Cesium 3D Tiles)
- `MakeModels.py` (AlignmentBatcher) — Similar pipeline with configurable steps and parameter file support

### Gap 4: No Camera Parameter Setup

**Problem**: The main codestack does not set camera priors (focal length, distortion model, calibration groups, lens groups) before alignment. This is critical for multi-camera ROV systems where each camera has different optics.

**What standalone utilities provide**: `SetCameraParams.py` applies per-camera-group settings by matching folder name patterns:
- Calibration group assignment
- Lens group assignment
- Focal length priors
- Distortion model selection
- Absolute pose configuration

### Gap 5: No Batch Sequential Alignment

**Problem**: The main codestack processes zones independently — each zone gets its own RealityScan process, a fresh scene, and no cross-zone continuity. There's no support for sequentially aligning batches within a single project while maintaining component relationships.

**What standalone utilities provide**: `BatchSequentialAlignment.py` loads all images into one project, then enables/disables alignment per batch, aligning sequentially with checkpoint support.

### Gap 6: No Component Export from Open Project

**Problem**: The main codestack exports components as part of the alignment command chain (using `-exportLatestComponents`), but cannot export named components from an already-open project.

**What standalone utilities provide**: `text_rsalign_exporter.py` connects to a running RC instance and exports individual components by name, with revision-based detection to verify component existence.

### Gap 7: No Image Pre-Processing

**Problem**: The main codestack does not enhance images before alignment. Underwater images often have poor contrast, color cast, and low feature detectability.

**What standalone utilities provide**: `clahe1.py` applies multi-scale CLAHE, texture enhancement, white balance correction, and edge-preserving sharpening optimized for photogrammetry feature matching.

---

## Integration Proposal

### Phase 1: Core Infrastructure (Prerequisite for all phases)

#### 1.1 Create `RCDelegationClient` Base Class

Create a shared delegation client that all RC-facing modules can use:

**New file**: `modules/realitycapture_interface/rc_delegation.py`

```python
class RCDelegationClient:
    """Shared RealityScan delegation client with status monitoring."""

    def __init__(self, rc_exe: Path, instance_name: str = "*",
                 poll_interval: float = 2.0, logger=None):
        self.rc_exe = rc_exe
        self.instance_name = instance_name
        self.poll_interval = poll_interval
        self.logger = logger

    def delegate(self, *args) -> subprocess.CompletedProcess
    def get_status(self) -> dict
    def wait_completed(self) -> subprocess.CompletedProcess
    def abort_instance(self) -> None
    def is_idle(self) -> bool
    def wait_idle_two_phase(self, operation_name, timeout) -> None
    def run_quick(self, operation_name, *args) -> None
    def monitor_operation(self, operation_name, timeout, poll_interval) -> None
    def verify_connection(self) -> bool
    def clear_queue(self) -> None
```

**Why**: This eliminates massive code duplication. Currently, `BatchSequentialAlignment.py`, `ModelGenerator.py`, `SetCameraParams.py`, `text_rsalign_exporter.py`, `MakeModels.py`, and `rc_module_batcher4.py` all independently implement the same delegation + status polling + wait logic. A single shared client ensures consistent behavior and a single place to fix bugs.

**Extract from**: `ModelGenerator.py` has the most robust implementation (two-phase idle detection, signal handling, abort support).

#### 1.2 Create `RCStatusParser` Utility

**New file**: `modules/realitycapture_interface/rc_status.py`

Extract the status parsing logic (currently duplicated in `ModelGenerator.py`, `temp.py`, and `BatchSequentialAlignment.py`) into a single utility:

```python
class RCStatusParser:
    """Parse RealityScan status strings into structured data."""

    @staticmethod
    def parse(status_text: str) -> dict:
        # Returns: {id, progress, runtime, estimation, is_idle, raw}
```

#### 1.3 Add Delegation Mode to `RealityCaptureAlignment`

Modify the existing module to support both execution modes:

```python
class RealityCaptureAlignment(RCModule):
    def run(self):
        if self.params['rc_use_delegation'].get_value():
            return self._run_delegation_mode()
        else:
            return self._run_direct_mode()  # Current behavior
```

Add a new parameter:
```python
Parameter(
    name='Use Delegation',
    cli_short='r_del',
    cli_long='r_use_delegation',
    type=bool,
    default_value=False,
    description='Use delegation to a running instance instead of launching a new process',
    prompt_user=True
)
```

---

### Phase 2: Image Pre-Processing Module

#### 2.1 Create `ImageEnhancement` Module

**New file**: `modules/image_enhancement/image_enhancement.py`

Integrate `clahe1.py` functionality as a proper `RCModule`:

```python
class ImageEnhancement(RCModule):
    def get_parameters(self):
        return {
            'enhance_input_dir': Parameter(...),
            'enhance_output_dir': Parameter(...),
            'enhance_clip_limit_fine': Parameter(default=1.5),
            'enhance_clip_limit_coarse': Parameter(default=1.2),
            'enhance_contrast_alpha': Parameter(default=1.15),
            'enhance_jpeg_quality': Parameter(default=90),
        }

    def run(self):
        # Multi-scale CLAHE + texture enhancement + white balance
        # Maintains folder structure, copies non-image files
```

**Register in `main.py`**:
```python
available_modules['Enhance Images'] = ImageEnhancement(logger)
```

**Pipeline position**: After ExtractImages, before GeoreferenceImages.

**Effort**: Low — `clahe1.py` is self-contained, only needs wrapping in the `RCModule` interface.

---

### Phase 3: Camera Parameter Setup Module

#### 3.1 Create `CameraSetup` Module

**New file**: `modules/camera_setup/camera_setup.py`

Integrate `SetCameraParams.py` functionality:

```python
class CameraSetup(RCModule):
    def get_parameters(self):
        return {
            'cam_setup_enabled': Parameter(default=True),
            'cam_groups_config': Parameter(
                description='Path to camera groups JSON config',
                default=None
            ),
        }

    def run(self):
        client = RCDelegationClient(rc_exe, instance_name)
        for group in camera_groups:
            client.delegate("-deselectAllImages")
            client.wait_completed()
            client.delegate("-selectImage", f"g/{group.keyword}/")
            client.wait_completed()
            # Apply calibration, focal, distortion, pose settings
            ...
```

**Externalize camera config**: Move the hardcoded camera groups from `SetCameraParams.py` to a JSON/YAML config file:

```json
{
  "camera_groups": [
    {
      "name": "CamLower",
      "keywords": ["camlower"],
      "calib_group": 1,
      "lens_group": 1,
      "focal_mm": 18,
      "distortion_model": "Brown3"
    }
  ]
}
```

**Pipeline position**: After alignment (images must be loaded in RC first), before model generation.

**Effort**: Medium — requires `RCDelegationClient` (Phase 1) and delegation mode in the main pipeline.

---

### Phase 4: Model Generation Module

#### 4.1 Create `ModelGeneration` Module

**New file**: `modules/model_generation/model_generation.py`

This is the largest integration, merging functionality from `ModelGenerator.py` and `MakeModels.py`:

```python
class ModelGeneration(RCModule):
    def get_parameters(self):
        return {
            'model_alignment_dir': Parameter(...),
            'model_export_dir': Parameter(...),
            'model_project_prefix': Parameter(...),
            'model_enable_simplify': Parameter(default=True),
            'model_export_fbx': Parameter(default=True),
            'model_export_cesium': Parameter(default=True),
            'model_simplify_params': Parameter(default=None),
            'model_texture_params': Parameter(default=None),
            'model_reprojection_params': Parameter(default=None),
            'model_test_mode': Parameter(default=True),
        }

    def run(self):
        client = RCDelegationClient(rc_exe, instance_name)
        client.clear_queue()  # Safety: clear previous commands

        components = self._scan_components()
        for component in components:
            self._process_component(client, component)

    def _process_component(self, client, name):
        # Select component
        # Set reconstruction region (auto + scale)
        # Calculate high model (monitored)
        # Mesh cleanup pipeline
        # Texture
        # Optional simplify + LOD + reproject
        # Export (FBX + Cesium)
        # Save checkpoint
```

**Pipeline position**: After alignment (requires aligned components to exist in open project).

**Key design decisions**:
- Use `MakeModels.py` as the primary source (it's the more evolved version with configurable parameter files)
- Include the signal handler / abort support from `ModelGenerator.py`
- Support both "process all" and "export only" modes
- Add checkpoint/resume via the same pattern as `BatchSequentialAlignment.py`

**Effort**: High — complex pipeline with many steps, needs thorough testing.

---

### Phase 5: Batch Sequential Alignment

#### 5.1 Enhance `RealityCaptureAlignment` with Batch Mode

Add batch sequential alignment as a sub-mode of the existing alignment module:

```python
class RealityCaptureAlignment(RCModule):
    def _run_delegation_mode(self):
        if self.params['rc_batch_sequential'].get_value():
            return self._run_batch_sequential()
        else:
            return self._run_delegation_standard()

    def _run_batch_sequential(self):
        client = RCDelegationClient(...)

        # Load all images
        client.run_quick("load images", "-add", imagelist_path)

        # Disable all
        client.run_quick("disable all", "-selectAllImages", "-enableAlignment", "false")

        # Sequential alignment per batch
        for batch in batches:
            if batch in completed_batches:
                continue  # Resume support
            self._enable_batch(client, batch)
            client.delegate("-align")
            client.monitor_operation("Alignment")
            self._disable_batch(client, batch)
            client.run_quick("save", "-save", project_path)
            self._save_checkpoint(batch)
```

New parameters:
```python
Parameter('rc_batch_sequential', default=False,
          description='Enable sequential batch alignment within a single project')
Parameter('rc_checkpoint_file', default=None,
          description='Path to checkpoint file for resume support')
```

**Effort**: Medium — `BatchSequentialAlignment.py` is well-structured, mainly needs adaptation to the `RCModule` interface.

---

### Phase 6: Component Export Module

#### 6.1 Create `ComponentExport` Module

**New file**: `modules/component_export/component_export.py`

Integrate `text_rsalign_exporter.py`:

```python
class ComponentExport(RCModule):
    def get_parameters(self):
        return {
            'export_output_dir': Parameter(...),
            'export_base_name': Parameter(...),
            'export_component_prefix': Parameter(...),
            'export_max_component_num': Parameter(default=66),
        }

    def run(self):
        client = RCDelegationClient(...)
        # Generate component names to search
        # For each: select, check revision, export if exists
        # Generate summary
```

**Effort**: Low — self-contained, mostly needs wrapping.

---

## Implementation Priority

| Priority | Phase | Module | Effort | Impact |
|----------|-------|--------|--------|--------|
| **1** | Phase 1 | `RCDelegationClient` + `RCStatusParser` | Medium | Blocks everything else; eliminates duplication |
| **2** | Phase 2 | `ImageEnhancement` | Low | Independent, no RC dependency |
| **3** | Phase 4 | `ModelGeneration` | High | Most requested missing functionality |
| **4** | Phase 3 | `CameraSetup` | Medium | Critical for multi-camera ROV systems |
| **5** | Phase 5 | Batch Sequential Alignment | Medium | Enhancement to existing module |
| **6** | Phase 6 | `ComponentExport` | Low | Utility for post-processing workflows |

---

## Shared Code Consolidation

The following code is currently duplicated across 4+ files and should be consolidated:

| Duplicated Code | Current Locations | Target Location |
|-----------------|-------------------|-----------------|
| RC executable finder | `BatchSequentialAlignment.py:52-71`, `SetCameraParams.py:76-96`, `realitycapture_interface.py:148-175`, `rc_module_batcher4.py:27-39` | `rc_delegation.py` |
| Delegation method | All 4 standalone utilities + `rc_module_batcher4.py` + `MakeModels.py` | `RCDelegationClient.delegate()` |
| Status polling | `BatchSequentialAlignment.py:131-144`, `ModelGenerator.py:305-330`, `text_rsalign_exporter.py:81-99`, `temp.py` | `RCDelegationClient.get_status()` |
| Status parsing | `ModelGenerator.py:114-175`, `temp.py:79-137`, `BatchSequentialAlignment.py:156-201` | `RCStatusParser.parse()` |
| Two-phase idle detection | `ModelGenerator.py:337-504`, `temp.py:275-421` | `RCDelegationClient.wait_idle_two_phase()` |
| Wait completed | All 4 standalone utilities | `RCDelegationClient.wait_completed()` |
| Signal handler / abort | `ModelGenerator.py:248-293`, `temp.py:197-272` | `RCDelegationClient` or mixin class |
| Export validation | `ModelGenerator.py:506-545`, `MakeModels.py:236-258` | `modules/utils/export_validation.py` |
| Component name scanning | `ModelGenerator.py:566-570`, `temp.py:423-427`, `MakeModels.py:333-337` | `RCDelegationClient` or utility |

---

## Updated Module Registration in `main.py`

After full integration, `main.py` would register:

```python
available_modules = {
    'Extract Images': ExtractImages(logger),
    'Enhance Images': ImageEnhancement(logger),        # NEW (Phase 2)
    'Georeference Images': GeoreferenceImages(logger),
    'Batch Directory': BatchDirectory(logger),
    'Camera Setup': CameraSetup(logger),               # NEW (Phase 3)
    'RealityCapture Alignment': RealityCaptureAlignment(logger),  # ENHANCED (Phase 1+5)
    'Component Export': ComponentExport(logger),        # NEW (Phase 6)
    'Model Generation': ModelGeneration(logger),        # NEW (Phase 4)
}
```

**Full pipeline order**:
1. Extract Images (from video)
2. Enhance Images (CLAHE + texture boost)
3. Georeference Images (apply flight log GPS)
4. Batch Directory (cluster into zones)
5. Camera Setup (apply calibration priors per camera group)
6. RealityCapture Alignment (align + export components)
7. Component Export (optional: re-export from open project)
8. Model Generation (mesh + texture + simplify + export)

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Breaking existing direct-CLI alignment | Keep as default mode; delegation is opt-in via `r_use_delegation` flag |
| Windows-only delegation commands | Already the case — document clearly; no Linux path regressions |
| Long integration time for ModelGeneration | Deliver in increments: Phase 1 first, then Phase 4 can function standalone |
| Parameter explosion (too many CLI args) | Use config file for camera groups and model pipeline settings |
| Testing difficulty (requires running RC) | Add dry-run modes that log commands without executing; use `rc_dry_run` pattern already in codebase |

---

## Files to Create

| File | Description |
|------|-------------|
| `modules/realitycapture_interface/rc_delegation.py` | Shared delegation client |
| `modules/realitycapture_interface/rc_status.py` | Status parser utility |
| `modules/image_enhancement/__init__.py` | Package init |
| `modules/image_enhancement/image_enhancement.py` | CLAHE enhancement module |
| `modules/camera_setup/__init__.py` | Package init |
| `modules/camera_setup/camera_setup.py` | Camera parameter setup module |
| `modules/model_generation/__init__.py` | Package init |
| `modules/model_generation/model_generation.py` | Model pipeline module |
| `modules/component_export/__init__.py` | Package init |
| `modules/component_export/component_export.py` | Component export module |
| `config/camera_groups.json` | Externalized camera group configuration |

## Files to Modify

| File | Changes |
|------|---------|
| `main.py` | Register new modules in `initialize_modules()` |
| `modules/realitycapture_interface/realitycapture_interface.py` | Add delegation mode, batch sequential mode |
