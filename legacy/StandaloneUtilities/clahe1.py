#!/usr/bin/env python3
"""
Batch process underwater images optimized for photogrammetry.
Enhances feature detectability while preserving geometric fidelity.
Maintains folder structure and copies text files.
"""

import cv2
import numpy as np
from pathlib import Path
from tqdm import tqdm
import shutil


def enhance_for_photogrammetry(image):
    """
    Optimize underwater images for photogrammetry feature matching.
    Focuses on texture enhancement and local contrast without geometric distortion.

    Args:
        image: Input BGR image as numpy array

    Returns:
        Enhanced BGR image as numpy array
    """
    # Convert to LAB for luminance processing
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    # Multi-scale CLAHE approach for better feature detection
    # Process at two scales and blend

    # Fine scale - small tiles for local detail (good for texture matching)
    clahe_fine = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(16, 16))
    l_fine = clahe_fine.apply(l_channel)

    # Coarse scale - large tiles for global structure (prevents posterization)
    clahe_coarse = cv2.createCLAHE(clipLimit=1.2, tileGridSize=(64, 64))
    l_coarse = clahe_coarse.apply(l_channel)

    # Blend scales: 40% fine detail, 40% coarse structure, 20% original
    l_enhanced = cv2.addWeighted(l_fine, 0.4, l_coarse, 0.4, 0)
    l_enhanced = cv2.addWeighted(l_enhanced, 0.8, l_channel, 0.2, 0)

    # Merge back
    lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
    enhanced = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    # Gentle contrast for global separation
    enhanced = cv2.convertScaleAbs(enhanced, alpha=1.15, beta=0)

    # Minimal white balance to reduce color cast without distorting hues
    enhanced = apply_white_balance(enhanced)

    # High-pass filter for texture enhancement (critical for photogrammetry)
    # Emphasizes fine detail without affecting overall brightness/geometry
    enhanced = apply_texture_enhancement(enhanced)

    # Very light detail-preserving sharpening
    enhanced = apply_detail_sharpening(enhanced)

    return enhanced


def apply_white_balance(image):
    """
    Minimal white balance to improve color consistency across image set.
    Important for photogrammetry feature matching.

    Args:
        image: Input BGR image as numpy array

    Returns:
        White-balanced BGR image as numpy array
    """
    result = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    avg_a = np.average(result[:, :, 1])
    avg_b = np.average(result[:, :, 2])

    # Very subtle correction for color consistency
    result[:, :, 1] = result[:, :, 1] - ((avg_a - 128) * (result[:, :, 0] / 255.0) * 0.35)
    result[:, :, 2] = result[:, :, 2] - ((avg_b - 128) * (result[:, :, 0] / 255.0) * 0.35)
    result = cv2.cvtColor(result, cv2.COLOR_LAB2BGR)
    return result


def apply_texture_enhancement(image):
    """
    High-pass filter to enhance texture without affecting overall structure.
    Crucial for improving feature point detection in low-texture areas.

    Args:
        image: Input BGR image as numpy array

    Returns:
        Texture-enhanced BGR image as numpy array
    """
    # Create low-frequency component with large Gaussian blur
    low_freq = cv2.GaussianBlur(image, (21, 21), 0)

    # High-frequency detail is the difference
    # Convert to float for safe subtraction
    image_float = image.astype(np.float32)
    low_freq_float = low_freq.astype(np.float32)
    high_freq = image_float - low_freq_float

    # Amplify high-frequency detail by small amount (texture boost)
    # Then add back to original
    texture_boost = 0.3
    enhanced = image_float + (high_freq * texture_boost)

    # Clip and convert back
    enhanced = np.clip(enhanced, 0, 255).astype(np.uint8)

    return enhanced


def apply_detail_sharpening(image):
    """
    Gentle edge-aware sharpening focused on real edges, not noise.

    Args:
        image: Input BGR image as numpy array

    Returns:
        Sharpened BGR image as numpy array
    """
    # Detect actual edges
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 100)

    # Create edge mask
    kernel = np.ones((3, 3), np.uint8)
    edge_mask = cv2.dilate(edges, kernel, iterations=1)
    edge_mask = cv2.GaussianBlur(edge_mask, (5, 5), 0)
    edge_mask = edge_mask.astype(np.float32) / 255.0

    # Very gentle unsharp mask
    blurred = cv2.GaussianBlur(image, (0, 0), 1.2)
    sharpened = cv2.addWeighted(image, 1.08, blurred, -0.08, 0)

    # Apply only to edges
    edge_mask_3ch = cv2.merge([edge_mask, edge_mask, edge_mask])
    result = (sharpened * edge_mask_3ch + image * (1 - edge_mask_3ch)).astype(np.uint8)

    return result


def process_images(input_dir, output_dir):
    """
    Process all JPEG images in input directory and save to output directory.
    Maintains folder structure and copies text files.

    Args:
        input_dir: Path to directory containing input images
        output_dir: Path to directory for saving processed images
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)

    # Find all JPEG files recursively
    image_files = list(input_path.rglob('*.jpg')) + list(input_path.rglob('*.jpeg')) + \
                  list(input_path.rglob('*.JPG')) + list(input_path.rglob('*.JPEG'))

    # Find all text files recursively
    text_extensions = ['*.txt', '*.md', '*.log', '*.csv', '*.json', '*.xml', '*.yaml', '*.yml']
    text_files = []
    for ext in text_extensions:
        text_files.extend(input_path.rglob(ext))

    if not image_files and not text_files:
        print(f"No JPEG images or text files found in {input_dir}")
        return

    print(f"Found {len(image_files)} images and {len(text_files)} text files to process")
    print("Processing optimized for photogrammetry:")
    print("  - Multi-scale CLAHE for feature detectability")
    print("  - Texture enhancement for low-contrast areas")
    print("  - Edge-preserving sharpening")
    print("  - Minimal geometric distortion")

    # Process each image
    for img_file in tqdm(image_files, desc="Processing images"):
        # Calculate relative path from input directory
        rel_path = img_file.relative_to(input_path)
        output_file = output_path / rel_path

        # Create subdirectories if needed
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Read image
        image = cv2.imread(str(img_file))

        if image is None:
            print(f"\nWarning: Could not read {rel_path}, skipping")
            continue

        # Enhance image
        enhanced = enhance_for_photogrammetry(image)

        # Save with 90% quality
        cv2.imwrite(str(output_file), enhanced, [cv2.IMWRITE_JPEG_QUALITY, 90])

    # Copy text files maintaining structure
    if text_files:
        print("\nCopying text files...")
        for text_file in tqdm(text_files, desc="Copying text files"):
            # Calculate relative path from input directory
            rel_path = text_file.relative_to(input_path)
            output_file = output_path / rel_path

            # Create subdirectories if needed
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(text_file, output_file)

    print(f"\nProcessing complete. Files saved to {output_dir}")
    print(f"  - {len(image_files)} images processed")
    print(f"  - {len(text_files)} text files copied")


def main():
    """Main function to handle user input and process images."""
    print("Underwater Image Enhancement for Photogrammetry")
    print("=" * 50)

    # Get input directory
    input_dir = input("Enter the path to the directory with original images: ").strip()
    if not Path(input_dir).exists():
        print(f"Error: Input directory '{input_dir}' does not exist")
        return

    # Get output directory
    output_dir = input("Enter the path to save processed images: ").strip()

    # Confirm before processing
    print(f"\nInput: {input_dir}")
    print(f"Output: {output_dir}")
    print("Photogrammetry-optimized processing:")
    print("  - Multi-scale CLAHE (16x16 + 64x64 tiles)")
    print("  - Blended approach: 40% fine + 40% coarse + 20% original")
    print("  - Texture enhancement via high-pass filtering")
    print("  - Edge-selective detail sharpening")
    print("  - Minimal white balance for color consistency")
    print("  - Reduced contrast (+15%) to avoid clipping")
    print("  - 90% JPEG quality")
    print("  - Maintains folder structure")
    print("  - Copies text files")
    confirm = input("\nProceed? (y/n): ").strip().lower()

    if confirm == 'y':
        process_images(input_dir, output_dir)
    else:
        print("Processing cancelled")


if __name__ == "__main__":
    main()