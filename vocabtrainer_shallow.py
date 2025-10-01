"""
COLMAP Vocabulary Tree Training Script - PARALLEL VERSION
Handles multiple camera models - RESUMABLE
"""

import subprocess
import os
from pathlib import Path
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
import sqlite3

COLMAP_PATH = r"C:\COLMAP\bin\colmap.exe"


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

    def stage_images(self, source_dir: Path, camera_model: str):
        """
        Copy images to staging directory organized by camera model.
        """
        target_dir = self.staging_dir / camera_model

        if target_dir.exists():
            existing_images = list(target_dir.glob('*'))
            if len(existing_images) > 0:
                print(f"\n[SKIP] {camera_model} already staged with {len(existing_images)} images")
                return len(existing_images)

        print(f"\nStaging images from {source_dir}")
        print(f"  Camera model: {camera_model}")

        target_dir.mkdir(exist_ok=True)

        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
        image_files = [
            f for f in source_dir.iterdir()
            if f.suffix.lower() in image_extensions
        ]

        total_images = len(image_files)
        copied_count = 0

        for img_file in image_files:
            target_file = target_dir / f"{camera_model}_{img_file.name}"
            shutil.copy2(img_file, target_file)
            copied_count += 1

            if copied_count % 100 == 0:
                print(f"  Copied {copied_count}/{total_images} images...", end='\r')

        print(f"  Staged {copied_count} images")
        return copied_count

    def prepare_dataset(self, dataset_config: dict, skip_if_exists: bool = True):
        """
        Stage all images from configured directories.
        """
        print("=" * 60)
        print("PREPARING DATASET")
        print("=" * 60)

        if skip_if_exists:
            print("(Skipping camera models with existing staged images)")

        total_staged = 0

        for source_path, camera_model in dataset_config.items():
            source = Path(source_path)
            if not source.exists():
                print(f"WARNING: {source} does not exist, skipping")
                continue

            count = self.stage_images(source, camera_model)
            total_staged += count

        print(f"\nTotal staged images: {total_staged}")
        return total_staged

    def extract_features_parallel(self, max_workers: int = 3):
        """
        Extract features in parallel using separate databases per camera model.
        Skips extraction if temporary databases already exist.
        """
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
        extraction_needed = []

        # Check which databases already exist
        for camera_subdir in camera_subdirs:
            temp_db = self.temp_db_dir / f"{camera_subdir.name}.db"
            temp_db_paths[camera_subdir.name] = temp_db

            if temp_db.exists():
                print(f"[SKIP] {camera_subdir.name} - database already exists")
            else:
                extraction_needed.append(camera_subdir)

        if extraction_needed:
            print(f"\nExtracting features for {len(extraction_needed)} camera models...")
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_to_camera = {}
                for camera_subdir in extraction_needed:
                    temp_db = temp_db_paths[camera_subdir.name]
                    future = executor.submit(
                        extract_features_worker,
                        camera_subdir,
                        temp_db
                    )
                    future_to_camera[future] = camera_subdir.name

                for future in as_completed(future_to_camera):
                    camera_model = future_to_camera[future]
                    try:
                        future.result()
                        print(f"✓ Completed {camera_model}")
                    except Exception as e:
                        print(f"✗ Failed {camera_model}: {e}")
                        raise
        else:
            print("All feature extraction already complete!")

        return temp_db_paths

    def merge_databases(self, temp_db_paths: dict):
        """
        Merge multiple temporary databases into the main training database.
        Skips merge if main database already exists.
        """
        print("\n" + "=" * 60)
        print("MERGING DATABASES")
        print("=" * 60)

        if self.db_path.exists():
            print(f"[SKIP] Training database already exists: {self.db_path}")
            return

        print(f"Merging {len(temp_db_paths)} databases into {self.db_path}")

        main_conn = sqlite3.connect(str(self.db_path))
        main_cursor = main_conn.cursor()

        camera_id_offset = 0
        image_id_offset = 0

        for idx, (camera_model, temp_db) in enumerate(temp_db_paths.items(), 1):
            print(f"  [{idx}/{len(temp_db_paths)}] Merging {camera_model}...")

            main_cursor.execute(f"ATTACH DATABASE '{temp_db}' AS temp_db")

            if idx == 1:
                main_cursor.execute("SELECT sql FROM temp_db.sqlite_master WHERE type='table' AND name='cameras'")
                cameras_schema = main_cursor.fetchone()[0]
                main_cursor.execute(cameras_schema)

                main_cursor.execute("SELECT sql FROM temp_db.sqlite_master WHERE type='table' AND name='images'")
                images_schema = main_cursor.fetchone()[0]
                main_cursor.execute(images_schema)

                main_cursor.execute("SELECT sql FROM temp_db.sqlite_master WHERE type='table' AND name='keypoints'")
                keypoints_schema = main_cursor.fetchone()[0]
                main_cursor.execute(keypoints_schema)

                main_cursor.execute("SELECT sql FROM temp_db.sqlite_master WHERE type='table' AND name='descriptors'")
                descriptors_schema = main_cursor.fetchone()[0]
                main_cursor.execute(descriptors_schema)

            main_cursor.execute("SELECT MAX(camera_id) FROM cameras")
            result = main_cursor.fetchone()
            camera_id_offset = (result[0] if result[0] is not None else 0)

            main_cursor.execute("SELECT MAX(image_id) FROM images")
            result = main_cursor.fetchone()
            image_id_offset = (result[0] if result[0] is not None else 0)

            main_cursor.execute("PRAGMA temp_db.table_info(cameras)")
            camera_columns = [col[1] for col in main_cursor.fetchall()]
            camera_cols_offset = ", ".join(
                f"{col} + {camera_id_offset}" if col == "camera_id" else col
                for col in camera_columns
            )

            main_cursor.execute("PRAGMA temp_db.table_info(images)")
            image_columns = [col[1] for col in main_cursor.fetchall()]
            image_cols_offset = ", ".join(
                f"{col} + {image_id_offset}" if col == "image_id" else
                f"{col} + {camera_id_offset}" if col == "camera_id" else col
                for col in image_columns
            )

            main_cursor.execute(f"""
                INSERT INTO cameras 
                SELECT {camera_cols_offset}
                FROM temp_db.cameras
            """)

            main_cursor.execute(f"""
                INSERT INTO images 
                SELECT {image_cols_offset}
                FROM temp_db.images
            """)

            main_cursor.execute(f"""
                INSERT INTO keypoints 
                SELECT image_id + {image_id_offset}, rows, cols, data 
                FROM temp_db.keypoints
            """)

            main_cursor.execute(f"""
                INSERT INTO descriptors 
                SELECT image_id + {image_id_offset}, rows, cols, data 
                FROM temp_db.descriptors
            """)

            main_conn.commit()
            main_cursor.execute("DETACH DATABASE temp_db")

        main_cursor.close()
        main_conn.close()
        print("✓ Database merge complete")

    def train_vocabulary_tree(self, num_visual_words: int = 1000000,
                              branching_factor: int = 32, num_iterations: int = 12):
        """
        Train the vocabulary tree using the merged database.
        Skips training if vocab tree already exists.
        """
        print("\n" + "=" * 60)
        print("TRAINING VOCABULARY TREE")
        print("=" * 60)

        if self.vocab_tree_path.exists():
            size_mb = self.vocab_tree_path.stat().st_size / (1024 * 1024)
            print(f"[SKIP] Vocabulary tree already exists: {self.vocab_tree_path}")
            print(f"  File size: {size_mb:.1f} MB")
            return

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
            print(result.stderr)
            raise RuntimeError("Vocabulary tree training failed")

        print("\n✓ Vocabulary tree training complete!")
        print(f"  Saved to: {self.vocab_tree_path}")

        if self.vocab_tree_path.exists():
            size_mb = self.vocab_tree_path.stat().st_size / (1024 * 1024)
            print(f"  File size: {size_mb:.1f} MB")

    def cleanup_staging(self, keep_temp_dbs: bool = True):
        """
        Remove staging directory after completion.
        Optionally keep temporary databases for debugging.
        """
        print("\n" + "=" * 60)
        print("CLEANUP")
        print("=" * 60)

        if self.staging_dir.exists():
            print(f"Removing staging directory: {self.staging_dir}")
            shutil.rmtree(self.staging_dir)
            print("✓ Staging cleanup complete")

        if not keep_temp_dbs and self.temp_db_dir.exists():
            print(f"Removing temporary databases: {self.temp_db_dir}")
            shutil.rmtree(self.temp_db_dir)
            print("✓ Temp database cleanup complete")
        elif keep_temp_dbs:
            print(f"Keeping temporary databases in: {self.temp_db_dir}")


def extract_features_worker(camera_subdir: Path, db_path: Path):
    """
    Worker function to extract features for a single camera model.
    Uses its own database to avoid locking issues.
    """
    camera_model = camera_subdir.name
    print(f"\n[{camera_model}] Starting feature extraction...")

    cmd = [
        COLMAP_PATH,
        "feature_extractor",
        "--database_path", str(db_path),
        "--image_path", str(camera_subdir),
        "--ImageReader.camera_model", "OPENCV",
        "--ImageReader.single_camera", "1",
        "--SiftExtraction.use_gpu", "1",
        "--SiftExtraction.gpu_index", "0",
        "--SiftExtraction.max_image_size", "3200",
        "--SiftExtraction.max_num_features", "16384",
        "--SiftExtraction.num_threads", "32",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Feature extraction failed for {camera_model}: {result.stderr}")

    return camera_model


def validate_colmap_installation():
    """
    Verify COLMAP executable exists and responds correctly.
    """
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
    """
    Check if CUDA GPU is available via nvidia-smi.
    """
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

    print("Validating installation...")
    if not validate_colmap_installation():
        print("\nAborting: COLMAP not found or not working")
        sys.exit(1)

    if not validate_cuda_availability():
        print("WARNING: Continuing without CUDA GPU acceleration")

    print()

    output_path = r"D:\colmap_vocab_training"

    dataset_config = {
        r"D:\NA173 Shallow": "na173_shallow_opencv",
    }

    trainer = COLMAPVocabTrainer(output_path)

    try:
        # Check if vocab tree already exists
        if trainer.vocab_tree_path.exists():
            size_mb = trainer.vocab_tree_path.stat().st_size / (1024 * 1024)
            print("=" * 60)
            print("VOCABULARY TREE ALREADY EXISTS")
            print("=" * 60)
            print(f"Location: {trainer.vocab_tree_path}")
            print(f"Size: {size_mb:.1f} MB")
            print("\nTo retrain, delete the existing vocab_tree.bin file.")
            sys.exit(0)

        total_images = trainer.prepare_dataset(dataset_config, skip_if_exists=True)

        if total_images == 0:
            print("ERROR: No images were staged. Check paths.")
            sys.exit(1)

        temp_db_paths = trainer.extract_features_parallel(max_workers=3)

        trainer.merge_databases(temp_db_paths)

        trainer.train_vocabulary_tree(
            num_visual_words=750000,
            branching_factor=32,
            num_iterations=12
        )

        trainer.cleanup_staging(keep_temp_dbs=True)

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