# CLAUDE.md

## Project Overview

RC_Main is a modular Python pipeline for processing underwater ROV (Remotely Operated Vehicle) imagery. It extracts video frames, georeferences them with navigation/telemetry data, clusters images into geographic zones, and prepares them for 3D reconstruction via RealityCapture.

## Project Structure

```
RC_Main/
├── main.py                          # Entry point, module orchestration & interactive CLI
├── module_base/                     # Base classes for the module system
│   ├── rc_module.py                 # Abstract base class (RCModule) for all modules
│   └── parameter.py                 # Parameter configuration class
├── modules/                         # Pluggable processing modules
│   ├── file_metadata_parser.py      # Timestamp/frame extraction from filenames
│   ├── extract_images/              # Video frame extraction from MP4s
│   ├── georeference/                # Match images to flight log GPS/orientation data
│   ├── image_batcher/               # Density-aware geographic clustering into zones
│   └── realitycapture_interface/    # RealityCapture CLI integration & batch scripts
├── colmap_processor.py              # COLMAP hierarchical feature matching
├── geoall.py                        # Standalone georeference script
├── decimator.py                     # Image subset decimation
├── masking.py                       # Image renaming and validation
├── vocabtrainer_*.py                # Vocabulary tree training for COLMAP
├── flightlogs.xml                   # RealityCapture flight log format definitions
├── sensorsdb.xml                    # Camera sensor/lens database
└── test.py                          # Image organization utility
```

## Tech Stack

- **Language:** Python 3 (uses `from __future__ import annotations`)
- **Core:** opencv-python, numpy, pandas
- **Geospatial:** geopandas, shapely, utm
- **ML/Clustering:** scikit-learn, scipy
- **Visualization:** matplotlib, seaborn
- **Image:** Pillow
- **CLI:** inquirer (interactive prompts)
- **External tools:** RealityCapture, COLMAP

## How to Run

```bash
# Install dependencies
pip install opencv-python numpy pandas geopandas shapely utm scikit-learn scipy matplotlib seaborn pillow inquirer tqdm psutil

# Run the interactive pipeline
python main.py

# Or with CLI arguments
python main.py -o /path/to/output -c true -i_input /path/to/video.mp4
```

## Module Architecture

All processing modules inherit from `RCModule` (in `module_base/rc_module.py`) and implement:
- `get_parameters()` — define configurable parameters
- `run()` — main processing logic
- `validate_parameters()` — input validation (returns `(bool, str)`)
- `finish()` — cleanup

Modules share state via a `params` dict passed through the pipeline.

## Coding Conventions

- **Classes:** PascalCase (`RCModule`, `ExtractImages`, `BatchDirectory`)
- **Functions/variables:** snake_case
- **Private methods:** prefixed with `__` or `_`
- **Logging:** Python `logging` module (INFO/WARNING/ERROR)
- **Type hints:** used throughout with `from __future__ import annotations`
- **Docstrings:** include Args/Returns sections where present
- **Validation:** explicit `validate_parameters()` methods returning `(success, message)` tuples

## Key Data Formats

- **Input flight logs:** CSV with columns like `Timestamp`, `kalman_lat`, `kalman_long`, `kalman_depth`, `kalman_yaw_deg`, `kalman_pitch_deg`, `kalman_roll_deg`
- **Output flight logs (for RealityCapture):** semicolon-delimited with `filename;X;Y;Alt;XAcc;YAcc;AltAcc;Yaw;Pitch;Roll;YawAcc;PitchAcc;RollAcc`
- **Camera configs:** XML sensor database and XMP sidecar files for calibration metadata

## Supported Camera Systems

- ZCAM F6 8-15mm Fisheye (Upper/Mid) — division distortion model
- ZCAM F7 16-35mm (Lower) — division distortion model
- Zeus Plus HD — rectilinear, zoom lens
- Camera offsets and pitch accuracies are hardcoded per camera type in the georeference module
