import os
import subprocess
import json
import psutil
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import time
import threading
from datetime import datetime

# Configuration
BASE_DIR = Path("E:/RUMI/NA173_H2102")
BATCHED_IMAGES = BASE_DIR / "batched_images_by_zone"
COLMAP_BIN = Path("C:/colmap/bin/colmap.exe")
OUTPUT_DIR = BASE_DIR / "colmap_hierarchical"
VOCAB_TREE_PATH = BASE_DIR / "vocab_tree_faiss_256K.bin"
NUM_PARALLEL_ZONES = 1  # Reduced from 5 to avoid GPU contention

# Camera model detection patterns
CAMERA_PATTERNS = {
	"camlower": {"pattern": "camlower_", "model": "OPENCV"},
	"cammid": {"pattern": "cammid_", "model": "OPENCV_FISHEYE"},
	"zeuss": {"pattern": "_HERC_", "model": "OPENCV"}
}

ZONES = ["zone_1", "zone_2", "zone_3", "zone_4", "zone_5"]

# Resource monitoring
monitoring_active = False

def timestamp():
	"""Return current timestamp string."""
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_print(message):
	"""Print message with timestamp."""
	print(f"[{timestamp()}] {message}")

def monitor_resources():
	"""Monitor system resources during processing."""
	global monitoring_active
	log_file = OUTPUT_DIR / "resource_usage.log"

	with open(log_file, 'w') as f:
		f.write("Timestamp,CPU%,RAM_GB,RAM%\n")

		while monitoring_active:
			cpu_percent = psutil.cpu_percent(interval=1)
			ram = psutil.virtual_memory()
			ram_gb = ram.used / (1024 ** 3)
			ram_percent = ram.percent

			ts = timestamp()
			f.write(f"{ts},{cpu_percent:.1f},{ram_gb:.1f},{ram_percent:.1f}\n")
			f.flush()

			if ram_percent > 90:
				log_print(f"⚠ WARNING: RAM usage at {ram_percent:.1f}% ({ram_gb:.1f}GB)")

			time.sleep(10)

def start_resource_monitoring():
	"""Start background resource monitoring thread."""
	global monitoring_active
	monitoring_active = True
	monitor_thread = threading.Thread(target=monitor_resources, daemon=True)
	monitor_thread.start()
	log_print("Resource monitoring started - logging to resource_usage.log")

def stop_resource_monitoring():
	"""Stop resource monitoring."""
	global monitoring_active
	monitoring_active = False
	time.sleep(1)
	log_print("Resource monitoring stopped")

def run_colmap_command(cmd, description, zone=None):
	"""Execute COLMAP command and handle errors with detailed progress."""
	log_print(f"{'=' * 60}")
	log_print(f"Starting: {description}")
	log_print(f"{'=' * 60}")

	if zone:
		log_print(f"Zone: {zone}")
	log_print(f"Command: {' '.join([str(c) for c in cmd[:5]])}...")

	start_time = time.time()

	# Run process with real-time output capture
	process = subprocess.Popen(
		cmd,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
		bufsize=1,
		universal_newlines=True
	)

	# Monitor output in real-time
	last_progress_time = time.time()
	output_lines = []

	for line in process.stdout:
		output_lines.append(line)
		line = line.strip()

		# Print progress lines
		if any(keyword in line.lower() for keyword in
			   ['processing', 'extracting', 'matching', 'registering', 'elapsed']):
			log_print(f"  [{zone or 'COLMAP'}] {line}")
			last_progress_time = time.time()

		# Check for stalls (no output for 5 minutes)
		if time.time() - last_progress_time > 300:
			log_print(f"⚠ WARNING: No progress output for 5 minutes in {description}")
			last_progress_time = time.time()

	process.wait()
	elapsed = time.time() - start_time

	if process.returncode != 0:
		log_print(f"ERROR in {description}:")
		log_print("\n".join(output_lines[-50:]))  # Print last 50 lines of output
		raise RuntimeError(f"COLMAP command failed: {description}")

	log_print(f"✓ Completed {description} in {elapsed / 60:.1f} minutes ({elapsed:.1f}s)")

	return process.returncode, output_lines

def setup_zone_directories(zone):
	"""Create output directories for a zone."""
	zone_dir = OUTPUT_DIR / zone
	db_path = zone_dir / f"{zone}.db"
	sparse_path = zone_dir / "sparse"

	zone_dir.mkdir(parents=True, exist_ok=True)
	sparse_path.mkdir(exist_ok=True)

	return zone_dir, db_path, sparse_path

def organize_images_by_camera(zone, image_path, zone_dir):
	"""Organize mixed images into camera-specific subfolders."""
	log_print(f"{zone} - Starting image organization by camera...")
	start_time = time.time()

	camera_dirs = {}

	for camera_name, info in CAMERA_PATTERNS.items():
		camera_dir = zone_dir / camera_name
		camera_dir.mkdir(exist_ok=True)
		camera_dirs[camera_name] = camera_dir

	image_files = list(image_path.glob("*.jpeg")) + list(image_path.glob("*.jpg"))

	if not image_files:
		log_print(f"WARNING: No images found in {image_path}")
		return camera_dirs

	log_print(f"{zone} - Found {len(image_files)} total images to organize")

	organized_count = {name: 0 for name in CAMERA_PATTERNS.keys()}

	for idx, img_file in enumerate(image_files):
		if idx % 1000 == 0 and idx > 0:
			log_print(f"{zone} - Organized {idx}/{len(image_files)} images...")

		for camera_name, info in CAMERA_PATTERNS.items():
			if info["pattern"] in img_file.name:
				dest = camera_dirs[camera_name] / img_file.name
				if not dest.exists():
					shutil.copy2(img_file, dest)
				organized_count[camera_name] += 1
				break

	elapsed = time.time() - start_time
	log_print(f"{zone} - Image organization complete in {elapsed:.1f}s:")
	for camera_name, count in organized_count.items():
		log_print(f"  {camera_name}: {count} images")

	return camera_dirs

def extract_features_for_camera(zone, camera_name, camera_model, db_path, camera_path):
	"""Extract features for a specific camera with appropriate model."""
	image_count = len(list(camera_path.glob("*.jpeg")) + list(camera_path.glob("*.jpg")))

	if image_count == 0:
		log_print(f"{zone} - Skipping {camera_name} - no images found")
		return

	log_print(f"{zone} - Starting feature extraction for {camera_name} ({camera_model}, {image_count} images)")

	# Check database size before
	db_size_before = db_path.stat().st_size if db_path.exists() else 0

	cmd = [
		str(COLMAP_BIN), "feature_extractor",
		"--database_path", str(db_path),
		"--image_path", str(camera_path),
		"--ImageReader.single_camera", "1",
		"--ImageReader.camera_model", camera_model,
		"--SiftExtraction.use_gpu", "1",
		"--SiftExtraction.max_num_features", "8192",
		"--SiftExtraction.max_image_size", "3200",
		"--SiftExtraction.first_octave", "-1",
		"--SiftExtraction.num_octaves", "4",
		"--SiftExtraction.octave_resolution", "3",
		"--SiftExtraction.peak_threshold", "0.00667",
		"--SiftExtraction.edge_threshold", "10",
		"--SiftExtraction.gpu_index", "-1"
	]

	run_colmap_command(cmd, f"{zone} - Feature extraction ({camera_name})", zone)

	# Check database size after
	db_size_after = db_path.stat().st_size if db_path.exists() else 0
	db_growth_mb = (db_size_after - db_size_before) / (1024 ** 2)
	log_print(f"{zone} - Database grew by {db_growth_mb:.1f} MB (now {db_size_after / (1024 ** 2):.1f} MB total)")

def process_zone(zone):
	"""Complete processing pipeline for a single zone."""
	log_print(f"\n{'#' * 60}")
	log_print(f"Processing Zone: {zone}")
	log_print(f"{'#' * 60}\n")

	zone_dir, db_path, sparse_path = setup_zone_directories(zone)
	image_path = BATCHED_IMAGES / zone

	if not image_path.exists():
		raise FileNotFoundError(f"Zone directory not found: {image_path}")

	# Step 1: Organize images by camera into subfolders
	camera_dirs = organize_images_by_camera(zone, image_path, zone_dir)

	# Step 2: Feature extraction for each camera separately
	log_print(f"{zone} - Starting feature extraction phase (3 cameras)")
	for idx, (camera_name, info) in enumerate(CAMERA_PATTERNS.items(), 1):
		log_print(f"{zone} - Feature extraction progress: {idx}/3 cameras")
		camera_path = camera_dirs[camera_name]
		extract_features_for_camera(zone, camera_name, info["model"], db_path, camera_path)

	log_print(f"{zone} - Feature extraction complete for all cameras")

	# Step 3: Sequential matching with vocab tree
	log_print(f"{zone} - Starting sequential matching with vocab tree loop detection")
	cmd = [
		str(COLMAP_BIN), "sequential_matcher",
		"--database_path", str(db_path),
		"--SequentialMatching.overlap", "10",
		"--SequentialMatching.quadratic_overlap", "1",
		"--SequentialMatching.loop_detection", "1",
		"--SequentialMatching.loop_detection_period", "10",
		"--SequentialMatching.loop_detection_num_images", "50",
		"--SequentialMatching.loop_detection_num_nearest_neighbors", "1",
		"--SequentialMatching.vocab_tree_path", str(VOCAB_TREE_PATH),
		"--SiftMatching.use_gpu", "1",
		"--SiftMatching.max_num_matches", "32768",
		"--SiftMatching.max_ratio", "0.8",
		"--SiftMatching.max_distance", "0.7",
		"--SiftMatching.cross_check", "1",
		"--SiftMatching.gpu_index", "-1"
	]
	run_colmap_command(cmd, f"{zone} - Sequential matching", zone)

	# Step 4: Reconstruction with GPU bundle adjustment
	log_print(f"{zone} - Starting incremental reconstruction with GPU bundle adjustment")
	organized_image_path = zone_dir

	cmd = [
		str(COLMAP_BIN), "mapper",
		"--database_path", str(db_path),
		"--image_path", str(organized_image_path),
		"--output_path", str(sparse_path),
		"--Mapper.multiple_models", "0",
		"--Mapper.num_threads", "20",
		"--Mapper.ba_use_gpu", "1",
		"--Mapper.ba_gpu_index", "-1",
		"--Mapper.ba_refine_focal_length", "1",
		"--Mapper.ba_refine_principal_point", "0",
		"--Mapper.ba_refine_extra_params", "1",
		"--Mapper.ba_local_num_images", "6",
		"--Mapper.ba_local_max_num_iterations", "15",
		"--Mapper.ba_local_max_refinements", "2",
		"--Mapper.ba_global_max_num_iterations", "30",
		"--Mapper.ba_global_max_refinements", "3",
		"--Mapper.ba_global_frames_freq", "300",
		"--Mapper.ba_global_points_freq", "150000",
		"--Mapper.ba_min_num_residuals_for_cpu_multi_threading", "50000",
		"--Mapper.init_min_num_inliers", "80",
		"--Mapper.init_max_error", "4",
		"--Mapper.init_min_tri_angle", "12",
		"--Mapper.abs_pose_min_num_inliers", "25",
		"--Mapper.abs_pose_min_inlier_ratio", "0.20",
		"--Mapper.abs_pose_max_error", "12",
		"--Mapper.filter_max_reproj_error", "4.0",
		"--Mapper.filter_min_tri_angle", "1.5",
		"--Mapper.tri_min_angle", "1.5",
		"--Mapper.min_num_matches", "15",
		"--Mapper.max_reg_trials", "3"
	]
	run_colmap_command(cmd, f"{zone} - Reconstruction", zone)

	log_print(f"{zone} - ZONE COMPLETE")

	return zone, sparse_path / "0"

def align_zone_to_reference(zone_path, reference_path, output_path):
	"""Align a zone reconstruction to reference coordinate system."""
	log_print(f"Aligning {zone_path.parent.parent.name} to reference coordinate system")

	cmd = [
		str(COLMAP_BIN), "model_aligner",
		"--input_path", str(zone_path),
		"--output_path", str(output_path),
		"--ref_images_path", str(reference_path),
		"--alignment_type", "custom",
		"--robust_alignment", "1",
		"--robust_alignment_max_error", "0.1"
	]
	run_colmap_command(cmd, f"Aligning {zone_path.parent.parent.name}")

def merge_models(input_path1, input_path2, output_path):
	"""Merge two aligned models."""
	log_print(f"Merging models: {input_path1.parent.name} + {input_path2.parent.name}")

	cmd = [
		str(COLMAP_BIN), "model_merger",
		"--input_path1", str(input_path1),
		"--input_path2", str(input_path2),
		"--output_path", str(output_path),
		"--max_reproj_error", "4.0"
	]
	run_colmap_command(cmd, f"Merging models")

def final_bundle_adjustment(input_path, output_path):
	"""Run final global bundle adjustment on merged model."""
	log_print("Starting final global bundle adjustment on complete merged model")

	cmd = [
		str(COLMAP_BIN), "bundle_adjuster",
		"--input_path", str(input_path),
		"--output_path", str(output_path),
		"--BundleAdjustment.refine_focal_length", "1",
		"--BundleAdjustment.refine_principal_point", "0",
		"--BundleAdjustment.refine_extra_params", "1",
		"--BundleAdjustment.max_num_iterations", "100"
	]
	run_colmap_command(cmd, "Final bundle adjustment")

def main():
	"""Main hierarchical reconstruction pipeline."""
	log_print(f"\n{'#' * 60}")
	log_print("COLMAP Hierarchical Reconstruction Pipeline")
	log_print(f"{'#' * 60}\n")

	# Verify vocab tree exists
	if not VOCAB_TREE_PATH.exists():
		raise FileNotFoundError(f"Vocabulary tree not found at {VOCAB_TREE_PATH}")

	# System info
	ram = psutil.virtual_memory()
	log_print(f"System Resources:")
	log_print(f"  CPU: Ryzen 9 7900X (12C/24T)")
	log_print(f"  GPU: RTX 4090")
	log_print(f"  Total RAM: {ram.total / (1024 ** 3):.1f} GB")
	log_print(f"  Available RAM: {ram.available / (1024 ** 3):.1f} GB")
	log_print(f"  Parallel Zones: {NUM_PARALLEL_ZONES}")
	log_print(f"  GPU BA: ENABLED\n")

	# Verify COLMAP exists
	if not COLMAP_BIN.exists():
		raise FileNotFoundError(f"COLMAP not found at {COLMAP_BIN}")

	# Create output directory
	OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

	# Start resource monitoring
	start_resource_monitoring()

	try:
		# Step 1: Process zones in parallel (limited parallelism)
		log_print("\n" + "=" * 60)
		log_print(f"PHASE 1: Processing Zones ({NUM_PARALLEL_ZONES} at a time)")
		log_print("=" * 60)

		zone_results = {}
		with ProcessPoolExecutor(max_workers=NUM_PARALLEL_ZONES) as executor:
			futures = {executor.submit(process_zone, zone): zone for zone in ZONES}

			for future in as_completed(futures):
				zone = futures[future]
				try:
					zone_name, sparse_path = future.result()
					zone_results[zone_name] = sparse_path
					log_print(f"\n{'=' * 60}")
					log_print(f"✓ {zone_name} COMPLETED SUCCESSFULLY")
					log_print(f"{'=' * 60}\n")
				except Exception as e:
					log_print(f"\n{'=' * 60}")
					log_print(f"✗ {zone} FAILED: {str(e)}")
					log_print(f"{'=' * 60}\n")
					raise

		# Step 2: Align and merge zones sequentially
		log_print("\n" + "=" * 60)
		log_print("PHASE 2: Aligning and Merging Zones")
		log_print("=" * 60)

		merge_dir = OUTPUT_DIR / "merged"
		merge_dir.mkdir(exist_ok=True)

		# Use zone_1 as reference
		reference_model = zone_results["zone_1"]
		current_merged = reference_model

		log_print(f"Using {reference_model.parent.parent.name} as reference coordinate system")

		# Align and merge remaining zones
		for i, zone in enumerate(ZONES[1:], start=2):
			zone_model = zone_results[zone]
			aligned_path = merge_dir / f"{zone}_aligned"

			# Align to current merged model
			align_zone_to_reference(zone_model, current_merged, aligned_path)

			# Merge with current result
			merged_output = merge_dir / f"merged_{i - 1}_{i}"
			merge_models(current_merged, aligned_path, merged_output)

			current_merged = merged_output
			log_print(f"✓ Merged {zone} into combined model ({i - 1}/{len(ZONES) - 1} merges complete)")

		# Step 3: Final bundle adjustment
		log_print("\n" + "=" * 60)
		log_print("PHASE 3: Final Global Bundle Adjustment")
		log_print("=" * 60)

		final_output = OUTPUT_DIR / "final"
		final_bundle_adjustment(current_merged, final_output)

		log_print("\n" + "=" * 60)
		log_print("RECONSTRUCTION COMPLETE!")
		log_print("=" * 60)
		log_print(f"Final model location: {final_output}")
		log_print(f"To visualize:")
		log_print(f'  {COLMAP_BIN} gui --import_path "{final_output}"')

	finally:
		stop_resource_monitoring()

if __name__ == "__main__":
	main()