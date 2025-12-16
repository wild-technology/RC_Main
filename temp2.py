#!/usr/bin/env python3
"""
Batch process underwater images for photogrammetry.
Enhanced version with highlight protection and anti-banding measures.
Maintains folder structure and copies text files.
Includes preview mode with 10 random sample images.
"""

import cv2
import numpy as np
from pathlib import Path
from tqdm import tqdm
import shutil
import random
import subprocess
import platform


def enhance_for_photogrammetry(image):
    """
    Adaptive enhancement with highlight protection and anti-banding.

    Args:
        image: Input BGR image as numpy array (uint8)

    Returns:
        Enhanced BGR image as numpy array (uint8)
    """
    # Convert to LAB (must be 8-bit for OpenCV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    # Create highlight protection mask
    highlight_mask = (l_channel > 220).astype(np.float32)
    highlight_mask = cv2.GaussianBlur(highlight_mask, (15, 15), 0)

    # Create adaptive processing mask based on local variance
    local_variance = compute_local_variance(l_channel)
    variance_norm = cv2.normalize(local_variance, None, 0, 1, cv2.NORM_MINMAX, dtype=cv2.CV_32F)

    # Process high-detail regions - minimal processing
    clahe_conservative = cv2.createCLAHE(clipLimit=1.0, tileGridSize=(64, 64))
    l_high_detail = clahe_conservative.apply(l_channel)

    # Process low-detail regions - emphasize subtle texture
    clahe_detail = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    l_low_detail = clahe_detail.apply(l_channel)

    # Blend based on local variance using float32 for precision
    l_high_float = l_high_detail.astype(np.float32)
    l_low_float = l_low_detail.astype(np.float32)
    l_enhanced_float = l_high_float * variance_norm + l_low_float * (1 - variance_norm)

    # Blend with original to prevent harsh transitions
    l_original_float = l_channel.astype(np.float32)
    l_blended_float = l_enhanced_float * 0.85 + l_original_float * 0.15

    # Protect highlights - revert to original in bright areas
    l_final_float = l_blended_float * (1 - highlight_mask) + l_original_float * highlight_mask

    # Add subtle dithering to prevent banding in smooth gradients
    # This is critical for preventing posterization
    dither_strength = 0.8
    dither = np.random.normal(0, dither_strength, l_final_float.shape)
    l_final_float = l_final_float + dither

    # Clip and convert back to uint8
    l_final = np.clip(l_final_float, 0, 255).astype(np.uint8)

    # Merge back to LAB
    lab_enhanced = cv2.merge([l_final, a_channel, b_channel])

    # Convert back to BGR
    enhanced = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    # Very gentle global contrast with highlight rolloff
    enhanced = apply_highlight_safe_contrast(enhanced)

    # Subtle white balance
    enhanced = apply_white_balance(enhanced)

    # Enhance micro-texture in sediment areas
    enhanced = enhance_sediment_texture(enhanced, variance_norm)

    return enhanced


def apply_highlight_safe_contrast(image):
    """
    Apply contrast adjustment with soft clipping to preserve highlights.
    Uses S-curve to avoid hard clipping.

    Args:
        image: Input BGR image

    Returns:
        Contrast-adjusted image
    """
    # Convert to float for precise computation
    img_float = image.astype(np.float32) / 255.0

    # Gentle power curve with highlight rolloff
    gamma = 0.95
    img_adjusted = np.power(img_float, gamma)

    # Apply highlight compression (soft knee)
    highlight_threshold = 0.8
    highlight_mask = img_adjusted > highlight_threshold
    compressed = highlight_threshold + (img_adjusted - highlight_threshold) * 0.7
    img_adjusted = np.where(highlight_mask, compressed, img_adjusted)

    # Convert back to uint8
    result = np.clip(img_adjusted * 255, 0, 255).astype(np.uint8)

    return result


def compute_local_variance(gray_channel, kernel_size=15):
    """
    Compute local variance to identify high-detail vs low-detail regions.

    Args:
        gray_channel: Grayscale image channel
        kernel_size: Size of local neighborhood

    Returns:
        Local variance map
    """
    img_float = gray_channel.astype(np.float32)
    mean = cv2.blur(img_float, (kernel_size, kernel_size))
    mean_of_squares = cv2.blur(img_float ** 2, (kernel_size, kernel_size))
    variance = mean_of_squares - (mean ** 2)
    variance = np.maximum(variance, 0)
    return variance


def enhance_sediment_texture(image, variance_map):
    """
    Enhance micro-texture in low-contrast sediment areas.

    Args:
        image: Input BGR image
        variance_map: Map of local variance (0-1, normalized)

    Returns:
        Texture-enhanced image
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # Compute gradients
    grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)

    # Create sediment mask (inverse of variance)
    sediment_mask = 1.0 - variance_map
    sediment_mask = cv2.GaussianBlur(sediment_mask, (15, 15), 0)

    # Reduced boost to prevent artifacts
    boost = 1.0 + (sediment_mask * 0.20)

    # Apply boost
    image_float = image.astype(np.float32)
    image_boosted = image_float * boost[:, :, np.newaxis]
    image_boosted = np.clip(image_boosted, 0, 255).astype(np.uint8)

    return image_boosted


def apply_white_balance(image):
    """
    Gray world white balance for color consistency.

    Args:
        image: Input BGR image as numpy array

    Returns:
        White-balanced BGR image as numpy array
    """
    result = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    avg_a = np.average(result[:, :, 1])
    avg_b = np.average(result[:, :, 2])

    result[:, :, 1] = result[:, :, 1] - ((avg_a - 128) * (result[:, :, 0] / 255.0) * 0.3)
    result[:, :, 2] = result[:, :, 2] - ((avg_b - 128) * (result[:, :, 0] / 255.0) * 0.3)
    result = cv2.cvtColor(result, cv2.COLOR_LAB2BGR)
    return result


def open_folder(folder_path):
    """
    Open folder in system file explorer.

    Args:
        folder_path: Path to folder to open
    """
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["explorer", str(folder_path)])
        elif system == "Darwin":
            subprocess.run(["open", str(folder_path)])
        else:
            subprocess.run(["xdg-open", str(folder_path)])
    except Exception as e:
        print(f"Could not open folder automatically: {e}")
        print(f"Please manually open: {folder_path}")


def process_preview_samples(input_dir, preview_dir, num_samples=10):
    """
    Process random sample images for preview comparison.

    Args:
        input_dir: Path to input directory
        preview_dir: Path to preview directory
        num_samples: Number of random samples to process

    Returns:
        List of processed sample file paths
    """
    input_path = Path(input_dir)
    preview_path = Path(preview_dir)

    # Find all JPEG files recursively
    image_files = list(input_path.rglob('*.jpg')) + list(input_path.rglob('*.jpeg')) + \
                  list(input_path.rglob('*.JPG')) + list(input_path.rglob('*.JPEG'))

    if not image_files:
        print(f"No JPEG images found in {input_dir}")
        return []

    # Select random samples
    num_samples = min(num_samples, len(image_files))
    sample_files = random.sample(image_files, num_samples)

    print(f"\nProcessing {num_samples} random samples for preview...")

    # Create preview directory structure
    original_dir = preview_path / "1_original"
    processed_dir = preview_path / "2_processed"
    original_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    processed_samples = []

    for img_file in tqdm(sample_files, desc="Creating preview samples"):
        # Read image
        image = cv2.imread(str(img_file))

        if image is None:
            print(f"\nWarning: Could not read {img_file.name}, skipping")
            continue

        # Copy original
        original_output = original_dir / img_file.name
        shutil.copy2(img_file, original_output)

        # Process and save
        enhanced = enhance_for_photogrammetry(image)
        processed_output = processed_dir / img_file.name
        cv2.imwrite(str(processed_output), enhanced, [cv2.IMWRITE_JPEG_QUALITY, 90])

        processed_samples.append(img_file)

    return processed_samples


def process_images(input_dir, output_dir, skip_preview=False):
    """
    Process all JPEG images in input directory and save to output directory.
    Maintains folder structure and copies text files.

    Args:
        input_dir: Path to directory containing input images
        output_dir: Path to directory for saving processed images
        skip_preview: If True, skip preview mode and process all images
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # Preview mode
    if not skip_preview:
        preview_dir = output_path.parent / f"{output_path.name}_PREVIEW"

        if preview_dir.exists():
            shutil.rmtree(preview_dir)

        processed_samples = process_preview_samples(input_dir, preview_dir, num_samples=10)

        if not processed_samples:
            print("No samples could be processed for preview.")
            return

        print(f"\nPreview samples saved to: {preview_dir}")
        print("  - 1_original: Original images")
        print("  - 2_processed: Enhanced images")
        print("\nOpening preview folder...")

        open_folder(preview_dir)

        print("\nPlease review the preview samples.")
        proceed = input("Proceed with full batch processing? (y/n): ").strip().lower()

        if proceed != 'y':
            print("Processing cancelled.")
            return

    # Create output directory
    output_path.mkdir(parents=True, exist_ok=True)

    # Find all files
    image_files = list(input_path.rglob('*.jpg')) + list(input_path.rglob('*.jpeg')) + \
                  list(input_path.rglob('*.JPG')) + list(input_path.rglob('*.JPEG'))

    text_extensions = ['*.txt', '*.md', '*.log', '*.csv', '*.json', '*.xml', '*.yaml', '*.yml']
    text_files = []
    for ext in text_extensions:
        text_files.extend(input_path.rglob(ext))

    if not image_files and not text_files:
        print(f"No JPEG images or text files found in {input_dir}")
        return

    print(f"\nProcessing {len(image_files)} images and {len(text_files)} text files...")

    # Process each image
    for img_file in tqdm(image_files, desc="Processing images"):
        rel_path = img_file.relative_to(input_path)
        output_file = output_path / rel_path
        output_file.parent.mkdir(parents=True, exist_ok=True)

        image = cv2.imread(str(img_file))

        if image is None:
            print(f"\nWarning: Could not read {rel_path}, skipping")
            continue

        enhanced = enhance_for_photogrammetry(image)
        cv2.imwrite(str(output_file), enhanced, [cv2.IMWRITE_JPEG_QUALITY, 90])

    # Copy text files
    if text_files:
        print("\nCopying text files...")
        for text_file in tqdm(text_files, desc="Copying text files"):
            rel_path = text_file.relative_to(input_path)
            output_file = output_path / rel_path
            output_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(text_file, output_file)

    print(f"\nProcessing complete. Files saved to {output_dir}")
    print(f"  - {len(image_files)} images processed")
    print(f"  - {len(text_files)} text files copied")


def main():
    """Main function to handle user input and process images."""
    print("Adaptive Underwater Image Enhancement for Photogrammetry")
    print("=" * 60)

    input_dir = input("Enter the path to the directory with original images: ").strip()
    if not Path(input_dir).exists():
        print(f"Error: Input directory '{input_dir}' does not exist")
        return

    output_dir = input("Enter the path to save processed images: ").strip()

    print(f"\nInput: {input_dir}")
    print(f"Output: {output_dir}")
    print("\nEnhanced processing with anti-banding:")
    print("  - Float32 intermediate processing to prevent banding")
    print("  - Highlight protection mask (preserves values >220)")
    print("  - Soft-knee contrast curve (no hard clipping)")
    print("  - Subtle dithering on smooth gradients")
    print("  - Reduced CLAHE on low-detail areas (1.8 vs 2.0)")
    print("  - Reduced sediment texture boost (20% vs 25%)")
    print("  - Adaptive processing based on local variance")
    print("  - 90% JPEG quality")

    print("\nPreview mode: 10 random samples will be processed first")

    confirm = input("\nProceed with preview? (y/n): ").strip().lower()

    if confirm == 'y':
        process_images(input_dir, output_dir, skip_preview=False)
    else:
        print("Processing cancelled.")


if __name__ == "__main__":
    main()