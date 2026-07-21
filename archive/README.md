# Archive

Scripts kept for reference but no longer part of the active RealityScan pipeline.

## colmap/

COLMAP-based reconstruction and vocabulary-tree training scripts. The active
pipeline uses RealityScan 2.2 exclusively; these are retained only in case a
COLMAP workflow is ever revisited.

| Script | Purpose |
|---|---|
| `colmap_processor.py` | Hierarchical COLMAP reconstruction (per-zone SfM → align → merge → global bundle adjustment). Hardcoded to `E:/RUMI/NA173_H2102`. |
| `vocabtrainer_shipwrecks.py` | COLMAP vocab-tree trainer for the NA173 + Zeuss dive datasets (256k visual words). Most complete variant. |
| `vocabtrainer_shipwrecks2.py` | Slimmed variant of the above with per-camera decimation (175k visual words). |
| `vocabtrainer_shallow.py` | Resumable variant retargeted at the `NA173 Shallow` dataset (50k visual words). |

The three `vocabtrainer_*.py` scripts are near-duplicates of one trainer with
different datasets/decimation settings; if the tool is ever needed again,
consolidate them into a single parameterized script rather than resurrecting
all three.

No Gaussian-splatting scripts existed in the repo at the time of archiving.
