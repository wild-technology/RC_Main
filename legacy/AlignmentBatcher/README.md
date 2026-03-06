# RealityCapture Interface - Enhanced Version

## Files Included

### 1. realitycapture_interface_enhanced.py (38KB)
**Complete rewritten module with all enhancements applied.**

This is your production-ready, batch-processing RealityCapture alignment module with:
- Proper delegation-based synchronization
- Complete component merging workflow
- No user prompts (fully automated)
- Context managers for resource cleanup
- Real-time progress monitoring
- 100-camera minimum component filtering
- Comprehensive error handling and logging

**Replace your existing `realitycapture_interface.py` with this file.**

---

### 2. ENHANCEMENT_SUMMARY.md (11KB)
**Comprehensive documentation of all improvements.**

Contains:
- Overview of all 10 major enhancements
- Architecture changes (before/after workflow diagrams)
- Configuration parameters
- Output structure
- Error recovery strategies
- Performance optimizations
- Logging details
- Migration guide
- Common issues and solutions
- Testing recommendations
- Future enhancement ideas

**Read this first to understand what changed and why.**

---

### 3. BEFORE_AFTER_COMPARISON.md (13KB)
**Side-by-side code comparison of critical changes.**

Shows exact code differences for:
- Component export synchronization fix (race condition eliminated)
- Missing merging functionality (now implemented)
- Resource management (context managers)
- Error handling (comprehensive logging)
- Progress monitoring (real-time feedback)
- Component filtering (quality control)
- File operations (retry logic)

**Review this to understand the technical improvements.**

---

### 4. example_usage.py (4.7KB)
**Working example of how to use the enhanced module.**

Demonstrates:
- Logger configuration
- Parameter setup
- Module initialization
- Workflow execution
- Result processing

**Use this as a template for your own scripts.**

---

## Quick Start

### Step 1: Replace Your Module
```bash
# Backup original
cp realitycapture_interface.py realitycapture_interface_original.py

# Install enhanced version
cp realitycapture_interface_enhanced.py realitycapture_interface.py
```

### Step 2: Update Your Code
Remove any interactive prompts and set required parameters:

```python
from realitycapture_interface import RealityCaptureAlignment

# Initialize
rc_module = RealityCaptureAlignment(logger)

# Configure (no prompts needed)
rc_module.params = {
    'rc_expedition_name': Parameter(..., default_value='EX2501'),
    'rc_dive_name': Parameter(..., default_value='H001'),
    'output_dir': Parameter(..., default_value='/path/to/output'),
    'rc_input_image_dir': Parameter(..., default_value='/path/to/zones'),
    'rc_merge_components': Parameter(..., default_value=True),
    'rc_save_checkpoints': Parameter(..., default_value=True)
}

# Run workflow
result = rc_module.run()

# Check results
if result['Success']:
    print(f"Components exported: {result['Total Components']}")
    print(f"Merged output: {result['Merge']['Merged Output']}")
```

### Step 3: Test
Run a test with a single zone folder to verify functionality:

```bash
python3 your_workflow_script.py \
    --r_expedition EX2501 \
    --r_dive H001 \
    --r_input /path/to/test_zone \
    --output /path/to/test_output
```

---

## Key Configuration Points

### Minimum Component Size
Hardcoded at 100 cameras. To change:
```python
# In __init__ method (line 22)
self.MIN_COMPONENT_SIZE = 100  # Change this value
```

### Timeout Duration
Default 1 hour. To change:
```python
# In __run_realityscan_command (line 243)
timeout=3600  # Seconds - change this value
```

### Retry Attempts
Default 3 attempts for file locks. To change:
```python
# In __safe_rename (line 309)
def __safe_rename(self, src, dst, max_retries=3):  # Change this
```

---

## Expected Directory Structure

### Input
```
batched_images_by_zone/
├── zone_001/
│   ├── camlower/
│   │   ├── img001.jpg
│   │   └── ...
│   ├── camupper/
│   │   ├── img001.jpg
│   │   └── ...
│   └── flight_log_17S_UTM.txt
├── zone_002/
│   └── ...
└── zone_003/
    └── ...
```

### Output
```
aligned_components/
├── EX2501_H001_zone_001_20250129_1430_1.rsalign
├── EX2501_H001_zone_001_20250129_1430_2.rsalign
├── EX2501_H001_zone_002_20250129_1430_1.rsalign
├── checkpoint_zone_001_20250129_1430.rsproj
├── checkpoint_zone_002_20250129_1430.rsproj
├── EX2501_H001_merged_20250129_1430.rsproj
└── merged/
    └── EX2501_H001_FINAL_MERGED_20250129_1430.rsalign
```

---

## What's Different from Original

### Removed (No Longer Needed)
- All user prompts and interactive confirmations
- Dry-run mode (batch only now)
- Filesystem polling for component files
- Environment variables (RC_NO_PROMPT, RC_OVERWRITE, RC_ALIGN_WAIT_TIMEOUT)
- Manual selection interfaces

### Added (New Functionality)
- Complete component merging workflow
- Delegation-based command routing
- Explicit wait for completion
- Context managers for resource cleanup
- Real-time progress monitoring
- Component quality filtering (100-camera minimum)
- Retry logic for file operations
- Checkpoint saves after each zone
- Comprehensive error logging

### Changed (Improved Implementation)
- Synchronization method (polling → delegation)
- Error handling (basic → comprehensive)
- Resource management (manual → automatic)
- Path handling (strings → Path objects with resolution)
- Command execution (single → multi-step with validation)

---

## Critical Notes

### Component Size Threshold
**Components with <100 cameras are NOT processed.**

This is intentional to:
- Eliminate low-quality fragments
- Reduce processing time
- Improve final merged quality

If you need smaller components, modify `MIN_COMPONENT_SIZE`.

### Non-Interactive Operation
**The script NEVER prompts for user input.**

This means:
- Existing output directories are automatically overwritten
- All discovered zones are automatically processed
- Merge always executes if zones succeed
- Errors are logged but don't stop workflow

### Windows Dependency
**This script requires Windows to run** because:
- RealityScan.exe is Windows-only
- Path handling uses Windows conventions
- File locking retry logic is Windows-specific

For Linux/ROS2 usage, the script itself can run on Linux but must connect to a Windows machine running RealityScan.

---

## Troubleshooting

### "No components with >=100 images"
**Solution:** Your zone has too few aligned cameras. Options:
1. Lower `MIN_COMPONENT_SIZE` in code
2. Improve image quality/overlap
3. Check flight log alignment

### "RealityScan command timed out"
**Solution:** Operation exceeded 1-hour timeout. Options:
1. Increase timeout in `__run_realityscan_command`
2. Process smaller zones
3. Check system resources

### "Merge failed: No components to merge"
**Solution:** All zones failed alignment. Options:
1. Check individual zone logs for errors
2. Verify image quality
3. Validate flight logs
4. Test single zone in RealityScan GUI

### "Failed to rename after 3 attempts"
**Solution:** File locks preventing rename. Options:
1. Increase retry count in `__safe_rename`
2. Disable antivirus temporarily
3. Close file explorers viewing output directory

---

## Performance Tips

### For Large Datasets (>10K images per zone)
1. Increase timeout to 2-3 hours
2. Enable checkpoints to allow recovery
3. Process zones individually first to verify settings
4. Monitor system resources (disk I/O, memory)

### For Many Zones (>20 zones)
1. Consider batching zones into groups
2. Enable checkpoint saves
3. Monitor total disk space for checkpoints
4. Test merge with subset first

### For Network Storage
1. Copy zones to local disk before processing
2. Process locally, then copy results to network
3. Disable checkpoint saves (performance hit)
4. Increase timeout significantly

---

## Support

### Log Files
Check these locations for debugging:
1. Console output (INFO level workflow progress)
2. `realitycapture_batch.log` (if using example_usage.py)
3. RealityScan cache directory for dumps
4. Checkpoint .rsproj files for inspection

### Debug Mode
Enable debug logging:
```python
logging.basicConfig(level=logging.DEBUG)
```

### Common Log Patterns
- `"Alignment completed"` = Success
- `"Export operations completed"` = Success
- `"Merge completed"` = Success
- `"command failed with code"` = Check error details
- `"File locked, retrying"` = Normal, wait for completion

---

## Next Steps

1. **Read ENHANCEMENT_SUMMARY.md** - Understand what changed
2. **Review BEFORE_AFTER_COMPARISON.md** - See code differences
3. **Test with example_usage.py** - Verify functionality
4. **Replace your module** - Deploy to production
5. **Run test workflow** - Single zone first
6. **Monitor logs** - Watch for issues
7. **Tune parameters** - Adjust timeouts/thresholds as needed

---

## Version Info

- **Enhanced Version:** 2.0
- **Python:** 3.13+
- **RealityScan:** 2.0
- **ROS2:** Kilted Kaiju
- **OS:** Ubuntu 24.04 / Jetpack 7.0 (Jetson Orion Nano)
- **Last Updated:** October 27, 2025

---

## Contact

For questions about this enhancement, refer to:
- ENHANCEMENT_SUMMARY.md (comprehensive documentation)
- BEFORE_AFTER_COMPARISON.md (code-level changes)
- RealityCapture CLI documentation in project files
