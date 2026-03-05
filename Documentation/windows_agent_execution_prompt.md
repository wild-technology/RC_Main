# Execution Prompt: Windows Console Claude Code Agent Workflow

## Copy this prompt to start a local Claude Code session on your Windows machine

---

You are a Claude Code Agent running locally on a Windows machine with RealityScan 2.1 installed. Your task is to perform integration testing of the RC_Main photogrammetry pipeline with real sample data and a live RealityScan instance.

## Setup Verification

Before running any tests, verify the environment:

1. **Check Python**: Run `python --version` (need 3.11+)
2. **Check dependencies**: Run `pip install -r requirements.txt`
3. **Check RealityScan**: Verify RealityScan.exe exists at one of:
   - `C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe`
   - Or the path in `%RC_EXECUTABLE%` environment variable
4. **Check test data**: Verify `tests/sample_data/` contains test images and ROV data
5. **Run unit tests first**: `python -m pytest tests/ -v -m "not windows_only"` — all should pass

## Testing Phases

### Phase 1: Unit Test Validation
Run the full unit test suite (no RC needed):
```
python -m pytest tests/ -v --tb=short
```
All tests must pass. If any fail, investigate and fix before proceeding.

### Phase 2: Module Smoke Tests
Test each module individually in non-interactive mode:

```powershell
# Set common env
set RC_NO_INTERACTIVE=1

# Test Extract Images (if applicable)
set RC_MODULES=Extract Images
python main.py --expedition_name TEST --dive_name D001 --output_dir D:\output\smoke_test

# Test Georeference
set RC_MODULES=Georeference Images
python main.py --expedition_name TEST --dive_name D001 --output_dir D:\output\smoke_test --geo_input_dir D:\test_images --geo_rov_csv D:\test_data\rov.csv

# Test Batch Directory
set RC_MODULES=Batch Directory
python main.py --expedition_name TEST --dive_name D001 --output_dir D:\output\smoke_test --batch_input_image_dir D:\test_images

# Test Image Enhancement
set RC_MODULES=Enhance Images
python main.py --expedition_name TEST --dive_name D001 --output_dir D:\output\smoke_test --enhance_enabled true --enhance_input_dir D:\test_images
```

### Phase 3: RC Delegation Tests (requires running RealityScan)
Start RealityScan 2.1 and wait for it to be idle, then:

```python
# Quick delegation connectivity check
from modules.rc_common.rc_delegation import RCDelegationClient
rc_exe = r"C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe"
client = RCDelegationClient(rc_exe)
assert client.verify_connection(), "Cannot connect to RealityScan!"
status = client.get_status()
print(f"Connected. Status: {status}")
assert status.get("is_idle"), "RealityScan is not idle — clear queue first"
```

### Phase 4: Alignment Integration Test
Run alignment with delegation on a small dataset (1-2 zones):
```powershell
set RC_NO_INTERACTIVE=1
set RC_MODULES=Camera Setup,RealityCapture Alignment
python main.py ^
  --expedition_name TEST --dive_name D001 ^
  --output_dir D:\output\alignment_test ^
  --r_use_delegation true ^
  --r_input D:\test_images\batched ^
  --rc_executable_path "C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe"
```

Monitor: Watch the console for two-phase detection logs. Verify:
- Phase 1 pickup detection fires within 30s
- Phase 2 progress reporting shows % updates
- Triple-verify idle confirmation at completion
- Checkpoint file created in output directory

### Phase 5: Model Generation Integration Test
After alignment produces .rsalign files:
```powershell
set RC_NO_INTERACTIVE=1
set RC_MODULES=Model Generation
python main.py ^
  --expedition_name TEST --dive_name D001 ^
  --output_dir D:\output\model_test ^
  --model_enabled true ^
  --model_alignment_dir D:\output\alignment_test\components ^
  --model_test_mode true ^
  --rc_executable_path "C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe"
```

Verify:
- Model generation pipeline completes for first component
- FBX and/or Cesium exports created
- Signal handling works (try Ctrl+C — should save and exit gracefully)

### Phase 6: Full Pipeline Test
Run the complete pipeline end-to-end with a small dataset:
```powershell
set RC_NO_INTERACTIVE=1
python main.py ^
  --expedition_name NA173 --dive_name H2102 ^
  --output_dir D:\output\full_pipeline ^
  --continue_automatically true ^
  --r_use_delegation true ^
  --enhance_enabled true ^
  --enhance_input_dir D:\images\NA173 ^
  --rc_executable_path "C:\Program Files\Capturing Reality\RealityScan 2.1\RealityScan.exe"
```

### Phase 7: Session Resume Test
1. Start a full pipeline run
2. Interrupt after 2-3 modules complete (Ctrl+C)
3. Verify session JSON saved in output directory
4. Restart with same parameters — verify completed steps are skipped

## Bug Report Format

When reporting bugs, include:
- **Module**: Which pipeline module
- **Severity**: Critical / High / Medium / Low
- **Steps to reproduce**: Exact commands run
- **Expected behavior**: What should have happened
- **Actual behavior**: What actually happened (include full error traceback)
- **RC Status**: Output of `client.get_status()` if delegation-related
- **Environment**: Python version, RC version, Windows version

## Success Criteria

- All unit tests pass (343+)
- Each module runs independently without errors
- RC delegation connects and reports status correctly
- Two-phase detection works for alignment and model generation
- Checkpoint save/resume works across interruptions
- Session state correctly tracks completed steps
- All output files follow the `{expedition}_{dive}_{utm}` naming convention
- No encoding errors, path separator issues, or resource leaks
