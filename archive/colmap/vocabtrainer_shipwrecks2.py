"""
COLMAP Vocabulary Tree Training Script - PARALLEL VERSION
Handles multiple camera models and decimates by 50% using timestamp-based sequential sampling
"""

import subprocess
import os
from pathlib import Path
import shutil
import re
from datetime import datetime
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import sqlite3
import time
import random

COLMAP_PATH = r"C:\COLMAP\bin\colmap.exe"

# Pipeline control flags - set to True to skip steps
SKIP_DECIMATION = False  # Run decimation to create smaller staging folder
SKIP_FEATURE_EXTRACTION = False
SKIP_TRAINING = False


class COLMAPVocabTrainer:
    def __init__(self, output_base_path: str):
        self.output_base = Path(output_base_path)
        self.output_base.mkdir(parents=True, exist_ok=True)

        self.db_path = self.output_base / "training.db"
        self.vocab_tree_path = self.output_base / "vocab_tree.bin"

        self.staging_dir = self.output_base / "staging_images"
        self.staging_dir.mkdir(exist_ok=True)

        self.temp_db_dir = self.output_base / "temp_databases"
        self.temp_db_dir.mkdir(exist_ok=True)

    def decimate_staging_folder(self, target_images_per_camera: int = 3000):
        """Create decimated staging folder from existing staging images"""
        decimated_dir = self.output_base / "staging_images_decimated"
        decimated_dir.mkdir(exist_ok=True)

        print(f"\nDecimating staging folder to ~{target_images_per_camera} images per camera...")

        total_kept = 0
        for camera_dir in self.staging_dir.iterdir():
            if not camera_dir.is_dir():
                continue

            camera_name = camera_dir.name
            target_dir = decimated_dir / camera_name

            if target_dir.exists() and len(list(target_dir.glob('*'))) > 0:
                kept = len(list(target_dir.glob('*')))
                print(f"  [SKIP] {camera_name}: already has {kept} images")
                total_kept += kept
                continue

            target_dir.mkdir(exist_ok=True)
            all_images = list(camera_dir.glob('*'))

            selected = random.sample(all_images, min(target_images_per_camera, len(all_images)))

            for img in selected:
                shutil.copy2(img, target_dir / img.name)

            print(f"  {camera_name}: kept {len(selected)}/{len(all_images)} images")
            total_kept += len(selected)

        print(f"\nTotal decimated images: {total_kept}")
        return decimated_dir

    def extract_features_parallel(self, max_workers: int = 3):
        if SKIP_FEATURE_EXTRACTION:
            print("\n" + "=" * 60)
            print("SKIPPING FEATURE EXTRACTION (SKIP_FEATURE_EXTRACTION = True)")
            print("=" * 60)
            if self.db_path.exists():
                db_size_mb = self.db_path.stat().st_size / (1024 * 1024)
                print(f"Using existing database: {self.db_path} ({db_size_mb:.1f} MB)")
            else:
                print("WARNING: No database found!")
            return

        print("\n" + "=" * 60)
        print("EXTRACTING FEATURES (PARALLEL)")
        print("=" * 60)
        print(f"Using {max_workers} parallel workers")

        camera_subdirs = [d for d in self.staging_dir.iterdir() if d.is_dir()]

        if not camera_subdirs:
            print("No camera subdirectories found!")
            return

        temp_db_paths = {}

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            future_to_camera = {}
            for camera_subdir in camera_subdirs:
                temp_db = self.temp_db_dir / f"{camera_subdir.name}.db"
                temp_db_paths[camera_subdir.name] = temp_db

                future = executor.submit(
                    extract_features_worker,
                    camera_subdir,
                    temp_db
                )
                future_to_camera[future] = camera_subdir.name

            completed = []
            for future in as_completed(future_to_camera):
                camera_model = future_to_camera[future]
                try:
                    future.result()
                    print(f"✓ Completed {camera_model}")
                    completed.append(camera_model)
                except Exception as e:
                    print(f"✗ Failed {camera_model}: {e}")
                    raise

        print(f"\n✓ All {len(completed)} camera models completed feature extraction")

        print("\n" + "=" * 60)
        print("MERGING DATABASES")
        print("=" * 60)
        self.merge_databases(temp_db_paths)

        print("\nCleaning up temporary databases...")
        for temp_db in temp_db_paths.values():
            if temp_db.exists():
                wal_file = Path(str(temp_db) + "-wal")
                shm_file = Path(str(temp_db) + "-shm")
                temp_db.unlink()
                if wal_file.exists():
                    wal_file.unlink()
                if shm_file.exists():
                    shm_file.unlink()

        if self.temp_db_dir.exists() and not list(self.temp_db_dir.iterdir()):
            self.temp_db_dir.rmdir()
        print("✓ Cleanup complete")

    def merge_databases(self, temp_db_paths: dict):
        """Merge multiple temporary databases with NULL descriptor validation"""
        print(f"Merging {len(temp_db_paths)} databases into {self.db_path}")

        if self.db_path.exists():
            print(f"Removing existing database: {self.db_path}")
            self.db_path.unlink()

        main_conn = sqlite3.connect(str(self.db_path))
        main_cursor = main_conn.cursor()

        total_null_cleaned = 0

        for idx, (camera_model, temp_db) in enumerate(temp_db_paths.items(), 1):
            print(f"\n[{idx}/{len(temp_db_paths)}] Merging {camera_model}...")

            if not temp_db.exists():
                print(f"  WARNING: Database not found: {temp_db}")
                continue

            db_size_mb = temp_db.stat().st_size / (1024 * 1024)
            print(f"  Database size: {db_size_mb:.1f} MB")

            temp_conn = sqlite3.connect(str(temp_db))
            temp_cursor = temp_conn.cursor()

            temp_cursor.execute("SELECT COUNT(*) FROM descriptors WHERE data IS NULL")
            null_count = temp_cursor.fetchone()[0]

            if null_count > 0:
                print(f"  WARNING: Found {null_count} NULL descriptors, cleaning...")
                temp_cursor.execute("SELECT image_id FROM descriptors WHERE data IS NULL")
                null_ids = [row[0] for row in temp_cursor.fetchall()]
                temp_cursor.execute("DELETE FROM descriptors WHERE data IS NULL")
                for img_id in null_ids:
                    temp_cursor.execute("DELETE FROM keypoints WHERE image_id = ?", (img_id,))
                    temp_cursor.execute("DELETE FROM images WHERE image_id = ?", (img_id,))
                temp_conn.commit()
                total_null_cleaned += null_count

            temp_conn.close()

            main_cursor.execute(f"ATTACH DATABASE '{temp_db}' AS temp_db")

            main_cursor.execute("PRAGMA temp_db.table_info(images)")
            image_columns = [row[1] for row in main_cursor.fetchall()]

            main_cursor.execute("PRAGMA temp_db.table_info(cameras)")
            camera_columns = [row[1] for row in main_cursor.fetchall()]

            main_cursor.execute("SELECT COUNT(*) FROM temp_db.images")
            num_images = main_cursor.fetchone()[0]
            print(f"  Images: {num_images}")

            if idx == 1:
                print("  Creating tables...")
                main_cursor.execute("""
                    CREATE TABLE cameras (
                        camera_id INTEGER PRIMARY KEY NOT NULL,
                        model INTEGER NOT NULL,
                        width INTEGER NOT NULL,
                        height INTEGER NOT NULL,
                        params BLOB,
                        prior_focal_length INTEGER NOT NULL
                    )
                """)
                main_cursor.execute("""
                    CREATE TABLE images (
                        image_id INTEGER PRIMARY KEY NOT NULL,
                        name TEXT NOT NULL,
                        camera_id INTEGER NOT NULL,
                        FOREIGN KEY(camera_id) REFERENCES cameras(camera_id) ON DELETE CASCADE
                    )
                """)
                main_cursor.execute("""
                    CREATE TABLE keypoints (
                        image_id INTEGER PRIMARY KEY NOT NULL,
                        rows INTEGER NOT NULL,
                        cols INTEGER NOT NULL,
                        data BLOB,
                        FOREIGN KEY(image_id) REFERENCES images(image_id) ON DELETE CASCADE
                    )
                """)
                main_cursor.execute("""
                    CREATE TABLE descriptors (
                        image_id INTEGER PRIMARY KEY NOT NULL,
                        rows INTEGER NOT NULL,
                        cols INTEGER NOT NULL,
                        data BLOB,
                        FOREIGN KEY(image_id) REFERENCES images(image_id) ON DELETE CASCADE
                    )
                """)
                camera_id_offset = 0
                image_id_offset = 0
            else:
                main_cursor.execute("SELECT MAX(camera_id) FROM cameras")
                result = main_cursor.fetchone()
                camera_id_offset = (result[0] if result[0] is not None else 0)

                main_cursor.execute("SELECT MAX(image_id) FROM images")
                result = main_cursor.fetchone()
                image_id_offset = (result[0] if result[0] is not None else 0)

            camera_cols_adjusted = ", ".join([
                f"camera_id + {camera_id_offset}" if col == "camera_id" else col
                for col in camera_columns
            ])
            main_cursor.execute(f"INSERT INTO cameras SELECT {camera_cols_adjusted} FROM temp_db.cameras")

            image_cols_adjusted = ", ".join([
                f"image_id + {image_id_offset}" if col == "image_id" else
                f"camera_id + {camera_id_offset}" if col == "camera_id" else
                col
                for col in image_columns
            ])
            main_cursor.execute(f"INSERT INTO images SELECT {image_cols_adjusted} FROM temp_db.images")

            main_cursor.execute(f"INSERT INTO keypoints SELECT image_id + {image_id_offset}, rows, cols, data FROM temp_db.keypoints")

            main_cursor.execute(f"""
                INSERT INTO descriptors 
                SELECT image_id + {image_id_offset}, rows, cols, data 
                FROM temp_db.descriptors WHERE data IS NOT NULL
            """)

            main_conn.commit()
            main_cursor.execute("DETACH DATABASE temp_db")
            print(f"  ✓ Merged {camera_model}")

        main_cursor.execute("CREATE INDEX IF NOT EXISTS index_images_camera_id ON images(camera_id)")
        main_conn.commit()
        main_conn.close()

        final_size_mb = self.db_path.stat().st_size / (1024 * 1024)
        print(f"\n✓ Database merge complete: {final_size_mb:.1f} MB")
        if total_null_cleaned > 0:
            print(f"  Total corrupted images cleaned: {total_null_cleaned}")

    def train_vocabulary_tree(self, num_visual_words: int = 256000, num_iterations: int = 12):
        if SKIP_TRAINING:
            print("\n" + "=" * 60)
            print("SKIPPING TRAINING (SKIP_TRAINING = True)")
            print("=" * 60)
            return

        print("\n" + "=" * 60)
        print("TRAINING VOCABULARY TREE")
        print("=" * 60)
        print(f"  Visual words: {num_visual_words:,}")
        print(f"  Iterations: {num_iterations}")

        cmd = [
            COLMAP_PATH,
            "vocab_tree_builder",
            "--database_path", str(self.db_path),
            "--vocab_tree_path", str(self.vocab_tree_path),
            "--num_visual_words", str(num_visual_words),
            "--num_iterations", str(num_iterations),
        ]

        print(f"\nStarting training (this will take several hours)...")
        print("COLMAP output:")
        print("-" * 60)

        # Run without capturing output so progress appears in real-time
        result = subprocess.run(cmd)

        if result.returncode != 0:
            raise RuntimeError("Vocabulary tree training failed")

        print("-" * 60)
        print("\n✓ Vocabulary tree training complete!")

        if self.vocab_tree_path.exists():
            size_mb = self.vocab_tree_path.stat().st_size / (1024 * 1024)
            print(f"  File size: {size_mb:.1f} MB")


def extract_features_worker(camera_subdir: Path, db_path: Path):
    camera_model = camera_subdir.name

    if "fisheye" in camera_model.lower():
        colmap_camera = "OPENCV_FISHEYE"
    else:
        colmap_camera = "OPENCV"

    cmd = [
        COLMAP_PATH,
        "feature_extractor",
        "--database_path", str(db_path),
        "--image_path", str(camera_subdir),
        "--ImageReader.camera_model", colmap_camera,
        "--ImageReader.single_camera", "1",
        "--SiftExtraction.use_gpu", "1",
        "--SiftExtraction.gpu_index", "0",
        "--SiftExtraction.max_image_size", "3200",
        "--SiftExtraction.max_num_features", "8192",
        "--SiftExtraction.num_threads", "32",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Feature extraction failed for {camera_model}: {result.stderr}")

    return camera_model


def main():
    print("=" * 60)
    print("COLMAP VOCABULARY TREE TRAINING PIPELINE")
    print("=" * 60)
    print()

    output_path = r"Z:\colmap vocab training"
    trainer = COLMAPVocabTrainer(output_path)

    try:
        if not SKIP_DECIMATION:
            decimated_staging = trainer.decimate_staging_folder(target_images_per_camera=4500)
            trainer.staging_dir = decimated_staging

        trainer.extract_features_parallel(max_workers=3)
        trainer.train_vocabulary_tree(num_visual_words=175000, num_iterations=12)

        print("\n" + "=" * 60)
        print("VOCABULARY TREE TRAINING COMPLETE")
        print("=" * 60)
        print(f"Vocabulary tree: {trainer.vocab_tree_path}")
        print(f"Database: {trainer.db_path}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()