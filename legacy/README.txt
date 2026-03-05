Legacy / Standalone Scripts
===========================

These files are predecessors to the main RC_Main pipeline modules.
They have been moved here during cleanup to reduce confusion, but are
preserved for reference.

StandaloneUtilities/
  - BatchSequentialAlignment.py  — Early batch alignment via delegation
  - ModelGenerator.py            — Standalone model generation script
  - SetCameraParams.py           — Camera parameter setter via delegation
  - clahe1.py                    — CLAHE enhancement (now: modules/image_enhancement)
  - temp.py                      — Component simplification script
  - text_rsalign_exporter.py     — rsalign text export helper

AlignmentBatcher/
  - MakeModels.py                — Model generation from components
  - rc_module_batcher_old.py     — First version of batch alignment
  - rc_module_batcher3.py        — Third iteration
  - rc_module_batcher4.py        — Fourth iteration (now: modules/realitycapture_interface)

geo_backup.py                    — Backup copy of georeference_images.py
batch_directory_exif.py          — EXIF-based variant of batch_directory.py (unused)

All functionality from these scripts has been integrated into the main
pipeline under modules/.
