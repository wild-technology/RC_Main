#!/usr/bin/env python3
"""
CPU-Optimized Underwater Image Enhancement Script
Multithreaded for AMD Ryzen processors with real-time progress indication.
Improves contrast, exposure, sharpness, and reduces noise for underwater images.
Reduces vibrance by 20% and saves corrected images to a 'corrected' subfolder.
"""

import cv2
import numpy as np
from PIL import Image, ImageEnhance
import os
import sys
from pathlib import Path
from typing import List, Tuple, Optional
import time
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
import threading


class UnderwaterImageProcessor:
    """CPU-optimized underwater JPG image processor with multithreading."""

    def __init__(self):
        self.supported_formats = {'.jpg', '.jpeg'}
        self.cpu_count = mp.cpu_count()
        self.progress_lock = threading.Lock()
        self.completed_count = 0

        print("CPU Detection:")
        print("-" * 40)
        print(f"✓ Detected {self.cpu_count} CPU cores")
        print("✓ Multithreading enabled for optimal Ryzen performance")

    def get_image_paths(self, folder_path: str) -> List[Path]:
        """Get all JPG image file paths from the top-level folder only."""
        folder = Path(folder_path)
        if not folder.exists():
            raise FileNotFoundError(f"Folder not found: {folder_path}")

        image_paths = []
        for file_path in folder.iterdir():
            if (file_path.is_file() and
                    file_path.suffix.lower() in self.supported_formats and
                    file_path.parent.name != 'corrected'):
                image_paths.append(file_path)

        return sorted(image_paths)

    def create_output_folder(self, input_folder: str) -> Path:
        """Create 'corrected' subfolder in the input directory."""
        output_folder = Path(input_folder) / 'corrected'
        output_folder.mkdir(exist_ok=True)
        return output_folder

    def enhance_underwater_image(self, image: np.ndarray) -> np.ndarray:
        """
        Underwater image enhancement without color cast correction.
        Focuses on contrast, exposure, sharpness, and noise reduction only.
        """
        # Step 1: Convert to LAB for contrast enhancement
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        # Step 2: Contrast enhancement with CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(l_channel)

        # Merge LAB channels (no color adjustment)
        enhanced_lab = cv2.merge([l_enhanced, a_channel, b_channel])
        enhanced_bgr = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

        # Step 3: Exposure correction with gamma adjustment
        gamma = 1.15  # Optimized for underwater exposure
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]).astype("uint8")
        enhanced_bgr = cv2.LUT(enhanced_bgr, table)

        # Step 4: Noise reduction
        # Use bilateral filter first for edge-preserving smoothing
        bilateral = cv2.bilateralFilter(enhanced_bgr, 9, 75, 75)

        # Then apply Non-Local Means for fine noise reduction
        denoised = cv2.fastNlMeansDenoisingColored(bilateral, None, 8, 8, 7, 21)

        return denoised

    def enhance_sharpness_and_reduce_vibrance(self, cv_image: np.ndarray) -> np.ndarray:
        """Apply sharpness enhancement and reduce saturation/vibrance by exactly 20%."""
        # Convert OpenCV BGR to PIL RGB
        rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb_image)

        # Enhance sharpness - optimized for underwater softness
        sharpness_enhancer = ImageEnhance.Sharpness(pil_image)
        sharpened = sharpness_enhancer.enhance(1.4)  # 40% sharpness increase

        # Reduce saturation/vibrance by exactly 20%
        color_enhancer = ImageEnhance.Color(sharpened)
        reduced_saturation = color_enhancer.enhance(0.8)  # 20% saturation reduction

        # Convert back to OpenCV BGR format
        result_rgb = np.array(reduced_saturation)
        result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)

        return result_bgr

    def copy_exif_data(self, original_path: Path, output_path: Path) -> None:
        """Copy EXIF data from original to processed JPG image."""
        try:
            with Image.open(original_path) as original_img:
                exif_data = original_img.info.get('exif')

                if exif_data:
                    with Image.open(output_path) as processed_img:
                        processed_img.save(
                            output_path,
                            'JPEG',
                            quality=95,
                            optimize=True,
                            exif=exif_data
                        )
        except Exception as e:
            pass  # Silent fail for EXIF preservation

    def process_single_image_worker(self, args: Tuple) -> Tuple[bool, str, str]:
        """Worker function for processing a single image in a thread."""
        input_path, output_path, total_count = args

        try:
            # Load image
            image = cv2.imread(str(input_path))
            if image is None:
                return False, f"Could not load: {input_path.name}", input_path.name

            # Memory check - skip very large images that might cause issues
            height, width = image.shape[:2]
            if height * width > 50_000_000:  # ~50MP limit
                return False, f"Image too large ({width}x{height}): {input_path.name}", input_path.name

            # Apply underwater enhancements
            enhanced_image = self.enhance_underwater_image(image)

            # Apply sharpness and vibrance adjustments
            final_image = self.enhance_sharpness_and_reduce_vibrance(enhanced_image)

            # Save with high quality
            save_params = [cv2.IMWRITE_JPEG_QUALITY, 95, cv2.IMWRITE_JPEG_OPTIMIZE, 1]
            success = cv2.imwrite(str(output_path), final_image, save_params)

            if success:
                # Copy EXIF data
                self.copy_exif_data(input_path, output_path)
                return True, f"Saved: {output_path.name}", input_path.name
            else:
                return False, f"Failed to save: {output_path.name}", input_path.name

        except Exception as e:
            return False, f"Error: {e}", input_path.name

    def update_progress(self, completed: int, total: int, image_name: str, success: bool):
        """Thread-safe progress update with real-time display."""
        with self.progress_lock:
            progress = (completed / total) * 100
            status = "✓" if success else "✗"
            print(f"[{completed:3d}/{total}] ({progress:5.1f}%) {status} {image_name}")

    def process_folder(self, input_folder: str) -> None:
        """Process all JPG images using multithreading with real-time progress."""
        try:
            # Get all JPG image paths
            image_paths = self.get_image_paths(input_folder)

            if not image_paths:
                print("No JPG images found in the specified folder.")
                return

            # Create output folder
            output_folder = self.create_output_folder(input_folder)

            print(f"\nFound {len(image_paths)} JPG images to process")
            print(f"Output folder: {output_folder}")
            print(f"✓ Using CPU multithreading with {self.cpu_count} cores")
            print("✓ Enhanced contrast, exposure, sharpness, and noise reduction")
            print("✓ 20% saturation reduction (no color cast correction)")
            print("=" * 70)

            # Filter out already processed images
            worker_args = []
            for image_path in image_paths:
                output_path = output_folder / image_path.name
                if not output_path.exists():
                    worker_args.append((image_path, output_path, len(image_paths)))

            if not worker_args:
                print("All images already processed!")
                return

            # Track timing
            start_time = time.time()
            successful = 0
            self.completed_count = 0

            # Optimal thread count for large batches (conservative approach)
            # Use fewer threads for very large datasets to avoid memory issues
            if len(worker_args) > 1000:
                max_workers = min(8, self.cpu_count)  # Conservative for large batches
                print(f"Large dataset detected ({len(worker_args)} images)")
                print(f"Using conservative threading: {max_workers} threads")
            else:
                max_workers = min(self.cpu_count, len(worker_args))

            print(f"Processing {len(worker_args)} images with {max_workers} threads...")
            print(f"Estimated completion time: ~{(len(worker_args) * 3) / max_workers / 60:.1f} minutes")
            print("-" * 70)

            # Process images with ThreadPoolExecutor for real-time progress
            completed_images = []
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    # Submit all jobs
                    future_to_args = {executor.submit(self.process_single_image_worker, args): args
                                      for args in worker_args}

                    # Process completed tasks as they finish
                    from concurrent.futures import as_completed
                    for future in as_completed(future_to_args):
                        try:
                            success, message, image_name = future.result(timeout=30)  # 30 second timeout per image
                            self.completed_count += 1

                            if success:
                                successful += 1

                            # Update progress in real-time
                            self.update_progress(self.completed_count, len(worker_args), image_name, success)

                            # Show error details if failed
                            if not success and "Error:" in message:
                                print(f"    Details: {message}")

                            # Periodic status update for large batches
                            if self.completed_count % 100 == 0:
                                elapsed = time.time() - start_time
                                rate = self.completed_count / elapsed
                                remaining = (len(worker_args) - self.completed_count) / rate
                                print(f"    Status: {rate:.1f} images/sec, ~{remaining / 60:.1f} min remaining")

                        except Exception as e:
                            print(f"    Thread error: {e}")
                            continue

            except KeyboardInterrupt:
                print("\nProcessing interrupted by user.")
                print(f"Completed: {successful}/{self.completed_count} images")
                return

            # Final summary
            elapsed_time = time.time() - start_time
            print("=" * 70)
            print(f"Processing complete!")
            print(f"Successfully processed: {successful}/{len(worker_args)} images")
            print(f"Total time: {elapsed_time:.1f} seconds")
            if len(worker_args) > 0:
                print(f"Average time per image: {elapsed_time / len(worker_args):.1f} seconds")
            print(f"Performance: CPU multithreaded with {max_workers} cores")
            print(f"Enhanced images saved in: {output_folder}")

        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)


def main():
    """Main function to run the CPU-optimized underwater image processor."""
    print("CPU-Optimized Underwater JPG Image Enhancement Tool")
    print("Contrast, exposure, sharpness, noise reduction, and 20% saturation reduction")
    print("=" * 70)

    # Get input folder from user
    while True:
        folder_path = input("Enter the path to the folder containing underwater JPG images: ").strip()
        folder_path = folder_path.strip('"\'')

        if not folder_path:
            print("Please enter a valid folder path.")
            continue

        if not os.path.exists(folder_path):
            print(f"Folder not found: {folder_path}")
            continue

        if not os.path.isdir(folder_path):
            print(f"Path is not a directory: {folder_path}")
            continue

        break

    print(f"\nSelected folder: {folder_path}")

    # Confirm before processing
    confirm = input("Proceed with multithreaded processing? (y/N): ").strip().lower()
    if confirm not in ['y', 'yes']:
        print("Processing cancelled.")
        return

    # Initialize processor and run
    processor = UnderwaterImageProcessor()
    processor.process_folder(folder_path)


if __name__ == "__main__":
    main()