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

COLMAP_PATH = r"C:\COLMAP\bin\colmap.exe"

# Pipeline control flags - set to True to skip steps
SKIP_DECIMATION = True
SKIP_FEATURE_EXTRACTION = True
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

    def extract_timestamp_zeuss(self, filename: str) -> datetime:
        match = re.match(r'(\d{8}T\d{6}Z)', filename)
        if match:
            timestamp_str = match.group(1)
            return datetime.strptime(timestamp_str, '%Y%m%dT%H%M%SZ')
        return None

    def extract_timestamp_cam(self, filename: str) -> datetime:
        match = re.search(r'(\d{8}T\d{6}Z)', filename)
        if match:
            timestamp_str = match.group(1)
            return datetime.strptime(timestamp_str, '%Y%m%dT%H%M%SZ')
        return None

    def decimate_images_by_timestamp(self, source_dir: Path, camera_model: str,
                                     is_zeuss: bool = False):
        target_dir = self.staging_dir / camera_model

        if target_dir.exists():
            existing_images = list(target_dir.glob('*'))
            if len(existing_images) > 0:
                print(f"\n[SKIP] {camera_model} already staged with {len(existing_images)} images")
                return len(existing_images)

        print(f"\nDecimating images from {source_dir}")
        print(f"  Camera model: {camera_model}, keeping 50% (every other image)")

        target_dir.mkdir(exist_ok=True)

        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
        image_files = [
            f for f in source_dir.iterdir()
            if f.suffix.lower() in image_extensions
        ]

        timestamp_extractor = self.extract_timestamp_zeuss if is_zeuss else self.extract_timestamp_cam

        files_with_timestamps = []
        skipped = 0

        for img_file in image_files:
            timestamp = timestamp_extractor(img_file.name)
            if timestamp:
                files_with_timestamps.append((timestamp, img_file))
            else:
                skipped += 1

        total_files = len(image_files)
        skip_percentage = (skipped / total_files * 100) if total_files > 0 else 0

        if skipped > 0 and skip_percentage < 45:
            print(f"  WARNING: Could not parse timestamp for {skipped} files ({skip_percentage:.1f}%)")
        elif skipped > 0:
            print(f"  Note: Skipped {skipped} files (likely duplicate frames with same timestamp)")

        files_with_timestamps.sort(key=lambda x: x[0])

        total_images = len(files_with_timestamps)
        kept_count = 0

        for idx, (timestamp, img_file) in enumerate(files_with_timestamps):
            if idx % 2 == 0:
                target_file = target_dir / f"{camera_model}_{img_file.name}"
                shutil.copy2(img_file, target_file)
                kept_count += 1

                if kept_count % 100 == 0:
                    print(f"  Copied {kept_count} images...", end='\r')

        print(f"  Decimated {total_images} -> {kept_count} images ({kept_count / total_images * 100:.1f}%)")
        return kept_count

    def prepare_dataset(self, dataset_config: dict, skip_if_exists: bool = True):
        if SKIP_DECIMATION:
            print("\n" + "=" * 60)
            print("SKIPPING DECIMATION (SKIP_DECIMATION = True)")
            print("=" * 60)
            total_staged = sum(len(list(d.glob('*'))) for d in self.staging_dir.iterdir() if d.is_dir())
            print(f"Total staged images: {total_staged}")
            return total_staged

        print("=" * 60)
        print("PREPARING DATASET")
        print("=" * 60)

        if skip_if_exists:
            print("(Skipping camera models with existing staged images)")

        total_staged = 0

        for source_path, (camera_model, is_zeuss) in dataset_config.items():
            source = Path(source_path)
            if not source.exists():
                print(f"WARNING: {source} does not exist, skipping")
                continue

            count = self.decimate_images_by_timestamp(source, camera_model, is_zeuss)
            total_staged += count

        print(f"\nTotal staged images: {total_staged}")
        return total_staged

    def extract_features_parallel(self, max_workers: int = 3):
        if SKIP_FEATURE_EXTRACTION:
            print("\n" + "=" * 60)
            print("SKIPPING FEATURE EXTRACTION (SKIP_FEATURE_EXTRACTION = True)")
            print("=" * 60)
            if self.db_path.exists():
                db_size_mb = self.db_path.stat().st_size / (1024 * 1024)
                print(f"Using existing database: {self.db_path} ({db_size_mb:.1f} MB)")
            else:
                print("WARNING: No database found! Running merge from temp databases...")
                # Check if temp databases exist and merge them
                camera_subdirs = [d for d in self.staging_dir.iterdir() if d.is_dir()]
                temp_db_paths = {d.name: self.temp_db_dir / f"{d.name}.db" for d in camera_subdirs}

                if any(db.exists() for db in temp_db_paths.values()):
                    print("\n" + "=" * 60)
                    print("MERGING DATABASES")
                    print("=" * 60)
                    self.merge_databases(temp_db_paths)
                else:
                    print("ERROR: No temp databases found either!")
            return

        print("\n" + "=" * 60)
        print("EXTRACTING FEATURES (PARALLEL)")
        print("=" * 60)
        print(f"Using {max_workers} parallel workers")
        print("Each camera model uses its own temporary database")

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
        """
        Merge multiple temporary databases into the main training database with progress reporting.
        Includes validation and cleanup of NULL descriptors.
        """
        print(f"Merging {len(temp_db_paths)} databases into {self.db_path}")

        if self.db_path.exists():
            print(f"Removing existing database: {self.db_path}")
            self.db_path.unlink()

            wal_file = Path(str(self.db_path) + "-wal")
            shm_file = Path(str(self.db_path) + "-shm")
            if wal_file.exists():
                wal_file.unlink()
            if shm_file.exists():
                shm_file.unlink()

        main_conn = sqlite3.connect(str(self.db_path))
        main_cursor = main_conn.cursor()

        camera_id_offset = 0
        image_id_offset = 0
        total_null_cleaned = 0

        for idx, (camera_model, temp_db) in enumerate(temp_db_paths.items(), 1):
            print(f"\n[{idx}/{len(temp_db_paths)}] Merging {camera_model}...")

            if not temp_db.exists():
                print(f"  WARNING: Database not found: {temp_db}")
                continue

            db_size_mb = temp_db.stat().st_size / (1024 * 1024)
            print(f"  Database size: {db_size_mb:.1f} MB")

            # Validate temp database before merging
            temp_conn = sqlite3.connect(str(temp_db))
            temp_cursor = temp_conn.cursor()

            print("  Validating database integrity...")
            temp_cursor.execute("SELECT COUNT(*) FROM descriptors WHERE data IS NULL")
            null_count = temp_cursor.fetchone()[0]

            if null_count > 0:
                print(f"  WARNING: Found {null_count} NULL descriptors, cleaning...")

                # Get image IDs with NULL descriptors
                temp_cursor.execute("SELECT image_id FROM descriptors WHERE data IS NULL")
                null_ids = [row[0] for row in temp_cursor.fetchall()]

                # Delete NULL descriptors and corresponding data
                temp_cursor.execute("DELETE FROM descriptors WHERE data IS NULL")
                for img_id in null_ids:
                    temp_cursor.execute("DELETE FROM keypoints WHERE image_id = ?", (img_id,))
                    temp_cursor.execute("DELETE FROM images WHERE image_id = ?", (img_id,))

                temp_conn.commit()
                total_null_cleaned += null_count
                print(f"  Cleaned {null_count} corrupted images")

            temp_conn.close()

            main_cursor.execute(f"ATTACH DATABASE '{temp_db}' AS temp_db")

            # Get actual column names from the source database
            main_cursor.execute("PRAGMA temp_db.table_info(images)")
            image_columns = [row[1] for row in main_cursor.fetchall()]

            main_cursor.execute("PRAGMA temp_db.table_info(cameras)")
            camera_columns = [row[1] for row in main_cursor.fetchall()]

            main_cursor.execute("SELECT COUNT(*) FROM temp_db.images")
            num_images = main_cursor.fetchone()[0]
            print(f"  Images: {num_images}")

            main_cursor.execute("SELECT COUNT(*) FROM temp_db.keypoints")
            num_keypoints = main_cursor.fetchone()[0]
            print(f"  Keypoints: {num_keypoints}")

            if idx == 1:
                print("  Creating tables...")
                main_cursor.execute("CREATE TABLE cameras AS SELECT * FROM temp_db.cameras WHERE 1=0")
                main_cursor.execute("CREATE TABLE images AS SELECT * FROM temp_db.images WHERE 1=0")
                main_cursor.execute("CREATE TABLE keypoints AS SELECT * FROM temp_db.keypoints WHERE 1=0")
                main_cursor.execute("CREATE TABLE descriptors AS SELECT * FROM temp_db.descriptors WHERE 1=0")

            main_cursor.execute("SELECT MAX(camera_id) FROM cameras")
            result = main_cursor.fetchone()
            camera_id_offset = (result[0] if result[0] is not None else 0)

            main_cursor.execute("SELECT MAX(image_id) FROM images")
            result = main_cursor.fetchone()
            image_id_offset = (result[0] if result[0] is not None else 0)

            print("  Copying cameras...")
            camera_cols_adjusted = ", ".join([
                f"camera_id + {camera_id_offset}" if col == "camera_id" else col
                for col in camera_columns
            ])
            main_cursor.execute(f"""
                INSERT INTO cameras 
                SELECT {camera_cols_adjusted}
                FROM temp_db.cameras
            """)

            print("  Copying images...")
            image_cols_adjusted = ", ".join([
                f"image_id + {image_id_offset}" if col == "image_id" else
                f"camera_id + {camera_id_offset}" if col == "camera_id" else
                col
                for col in image_columns
            ])
            main_cursor.execute(f"""
                INSERT INTO images 
                SELECT {image_cols_adjusted}
                FROM temp_db.images
            """)

            print("  Copying keypoints...")
            main_cursor.execute(f"""
                INSERT INTO keypoints 
                SELECT image_id + {image_id_offset}, rows, cols, data 
                FROM temp_db.keypoints
            """)

            print("  Copying descriptors (this may take a while)...")
            start_time = time.time()
            main_cursor.execute(f"""
                INSERT INTO descriptors 
                SELECT image_id + {image_id_offset}, rows, cols, data 
                FROM temp_db.descriptors
                WHERE data IS NOT NULL
            """)
            elapsed = time.time() - start_time
            print(f"  Descriptors copied in {elapsed:.1f} seconds")

            print("  Committing...")
            main_conn.commit()

            print("  Checkpointing WAL...")
            main_cursor.execute("PRAGMA temp_db.wal_checkpoint(TRUNCATE)")

            print("  Detaching database...")
            main_cursor.execute("DETACH DATABASE temp_db")

            print(f"  ✓ Merged {camera_model}")

        print("\nCreating indices for faster access...")
        main_cursor.execute("CREATE INDEX IF NOT EXISTS index_images_camera_id ON images(camera_id)")
        main_conn.commit()

        # Final validation
        print("\nValidating merged database...")
        main_cursor.execute("SELECT COUNT(*) FROM descriptors WHERE data IS NULL")
        final_null = main_cursor.fetchone()[0]

        if final_null > 0:
            print(f"  WARNING: {final_null} NULL descriptors remain, cleaning...")
            main_cursor.execute("DELETE FROM descriptors WHERE data IS NULL")
            main_conn.commit()

        main_conn.close()

        final_size_mb = self.db_path.stat().st_size / (1024 * 1024)
        print(f"\n✓ Database merge complete")
        print(f"  Final database size: {final_size_mb:.1f} MB")
        if total_null_cleaned > 0:
            print(f"  Total corrupted images cleaned: {total_null_cleaned}")

    def train_vocabulary_tree(self, num_visual_words: int = 1000000,
                              branching_factor: int = 32, num_iterations: int = 12):
        if SKIP_TRAINING:
            print("\n" + "=" * 60)
            print("SKIPPING TRAINING (SKIP_TRAINING = True)")
            print("=" * 60)
            if self.vocab_tree_path.exists():
                size_mb = self.vocab_tree_path.stat().st_size / (1024 * 1024)
                print(f"Using existing vocab tree: {self.vocab_tree_path} ({size_mb:.1f} MB)")
            else:
                print("WARNING: No vocab tree found! You may need to run training.")
            return

        print("\n" + "=" * 60)
        print("TRAINING VOCABULARY TREE")
        print("=" * 60)
        print(f"  Visual words: {num_visual_words:,}")
        print(f"  Iterations: {num_iterations}")
        print(f"  Output: {self.vocab_tree_path}")
        print("\nThis will take several hours. Progress may not be visible.")
        print("Monitor RAM usage via Task Manager.")

        cmd = [
            COLMAP_PATH,
            "vocab_tree_builder",
            "--database_path", str(self.db_path),
            "--vocab_tree_path", str(self.vocab_tree_path),
            "--num_visual_words", str(num_visual_words),
            "--num_iterations", str(num_iterations),
        ]

        print(f"\nCommand: {' '.join(cmd)}")
        print("\nStarting training...")

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("ERROR in vocabulary tree training:")
            print("\nSTDOUT:")
            print(result.stdout)
            print("\nSTDERR:")
            print(result.stderr)
            raise RuntimeError("Vocabulary tree training failed")

        print("\n✓ Vocabulary tree training complete!")
        print(f"  Saved to: {self.vocab_tree_path}")

        if self.vocab_tree_path.exists():
            size_mb = self.vocab_tree_path.stat().st_size / (1024 * 1024)
            print(f"  File size: {size_mb:.1f} MB")

def extract_features_worker(camera_subdir: Path, db_path: Path):
    """
    Worker function to extract features for a single camera model.
    Uses its own database to avoid locking issues.
    """
    camera_model = camera_subdir.name
    print(f"\n[{camera_model}] Starting feature extraction...")

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
        "--SiftExtraction.max_num_features", "8384",
        "--SiftExtraction.num_threads", "32",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Feature extraction failed for {camera_model}: {result.stderr}")

    return camera_model


def validate_colmap_installation():
    try:
        result = subprocess.run(
            [COLMAP_PATH],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stdout + result.stderr
        if "COLMAP" in output or "feature_extractor" in output:
            print(f"COLMAP found at: {COLMAP_PATH}")
            return True
        else:
            print("ERROR: COLMAP executable found but doesn't respond correctly")
            return False
    except FileNotFoundError:
        print(f"ERROR: COLMAP not found at: {COLMAP_PATH}")
        return False
    except subprocess.TimeoutExpired:
        print("ERROR: COLMAP command timed out")
        return False


def validate_cuda_availability():
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print("CUDA GPU detected via nvidia-smi")
            return True
        else:
            print("WARNING: nvidia-smi found but returned error")
            return False
    except FileNotFoundError:
        print("WARNING: nvidia-smi not found - CUDA may not be available")
        return False
    except subprocess.TimeoutExpired:
        print("WARNING: nvidia-smi command timed out")
        return False


def main():
    print("=" * 60)
    print("COLMAP VOCABULARY TREE TRAINING PIPELINE")
    print("=" * 60)
    print()

    print("Pipeline Configuration:")
    print(f"  SKIP_DECIMATION = {SKIP_DECIMATION}")
    print(f"  SKIP_FEATURE_EXTRACTION = {SKIP_FEATURE_EXTRACTION}")
    print(f"  SKIP_TRAINING = {SKIP_TRAINING}")
    print()

#    print("Validating installation...")
#    if not validate_colmap_installation():
#        print("\nAborting: COLMAP not found or not working")
#        sys.exit(1)

 #   if not validate_cuda_availability():
 #       print("WARNING: Continuing without CUDA GPU acceleration")

    print()

    output_path = r"Z:\colmap vocab training"

    dataset_config = {
        r"Z:\ToSort\NA173\lower": ("lower_opencv", False),
        r"Z:\ToSort\NA173\mid": ("mid_fisheye", False),
        r"Z:\ToSort\NA173\upper": ("upper_fisheye", False),
        r"Z:\ToSort\NA173\Zeuss\H2102\raw_images": ("zeuss_h2102_opencv", True),
        r"Z:\ToSort\NA173\Zeuss\H2103\raw_images": ("zeuss_h2103_opencv", True),
        r"Z:\ToSort\NA173\Zeuss\H2104\raw_images": ("zeuss_h2104_opencv", True),
        r"Z:\ToSort\NA173\Zeuss\H2105\raw_images": ("zeuss_h2105_opencv", True),
    }

    trainer = COLMAPVocabTrainer(output_path)

    try:
        total_images = trainer.prepare_dataset(dataset_config, skip_if_exists=True)

        if total_images == 0:
            print("ERROR: No images were staged. Check paths.")
            sys.exit(1)

        trainer.extract_features_parallel(max_workers=3)

        trainer.train_vocabulary_tree(
            num_visual_words=10000,
            num_iterations=5
        )

        # Uncomment to clean up staging after completion
        # trainer.cleanup_staging()

        print("\n" + "=" * 60)
        print("VOCABULARY TREE TRAINING COMPLETE")
        print("=" * 60)
        print(f"Vocabulary tree: {trainer.vocab_tree_path}")
        print(f"Database: {trainer.db_path}")
        print("\nUsage in COLMAP GUI:")
        print("  Processing -> Vocabulary tree matching")
        print("  Select vocab_tree.bin")

    except KeyboardInterrupt:
        print("\n\nTraining interrupted by user")
        print(f"Partial results in: {output_path}")
        sys.exit(1)

    except Exception as e:
        print(f"\nERROR: {e}")
        print(f"\nPartial results may be in: {output_path}")
        raise


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()