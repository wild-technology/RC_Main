"""Image enhancement module for underwater photogrammetry.

Wraps the multi-scale CLAHE enhancement pipeline from clahe1.py as an
RCModule for integration into the main pipeline. Runs after image
extraction and before georeferencing.

Enhancement pipeline (optimised for feature detectability):
1. Multi-scale CLAHE on L channel (fine 16×16 + coarse 64×64)
2. Gentle contrast adjustment
3. Minimal white balance for colour consistency
4. High-pass texture enhancement
5. Edge-aware detail sharpening
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.rc_common.naming import generate_filename

_log = logging.getLogger(__name__)

# Supported image extensions (case-insensitive matching)
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}

# Text-like extensions to copy unchanged
_TEXT_EXTENSIONS = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".yaml", ".yml"}


class ImageEnhancement(RCModule):
    """Multi-scale CLAHE image enhancement for photogrammetry."""

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__("Image Enhancement", logger)

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def get_parameters(self) -> dict[str, Parameter]:
        return {
            "enhance_enabled": Parameter(
                name="Enable Image Enhancement",
                cli_short="enh",
                cli_long="enhance_enabled",
                type=bool,
                default_value=False,
                description="Enable CLAHE image enhancement before georeferencing",
                parameter_group="Enhancement",
            ),
            "enhance_input_dir": Parameter(
                name="Enhancement Input Directory",
                cli_short="enh_in",
                cli_long="enhance_input_dir",
                type=str,
                default_value=None,
                description="Directory containing images to enhance",
                parameter_group="Enhancement",
                file_filter="directory",
            ),
            "enhance_clip_limit_fine": Parameter(
                name="Fine CLAHE Clip Limit",
                cli_short="enh_clf",
                cli_long="enhance_clip_limit_fine",
                type=float,
                default_value=1.5,
                description="Clip limit for fine-scale CLAHE (16×16 tiles)",
                parameter_group="Enhancement",
                min_value=0.5,
                max_value=10.0,
            ),
            "enhance_clip_limit_coarse": Parameter(
                name="Coarse CLAHE Clip Limit",
                cli_short="enh_clc",
                cli_long="enhance_clip_limit_coarse",
                type=float,
                default_value=1.2,
                description="Clip limit for coarse-scale CLAHE (64×64 tiles)",
                parameter_group="Enhancement",
                min_value=0.5,
                max_value=10.0,
            ),
            "enhance_contrast_alpha": Parameter(
                name="Contrast Alpha",
                cli_short="enh_ca",
                cli_long="enhance_contrast_alpha",
                type=float,
                default_value=1.15,
                description="Global contrast scaling factor (1.0 = no change)",
                parameter_group="Enhancement",
                min_value=0.5,
                max_value=3.0,
            ),
            "enhance_jpeg_quality": Parameter(
                name="JPEG Output Quality",
                cli_short="enh_q",
                cli_long="enhance_jpeg_quality",
                type=int,
                default_value=90,
                description="JPEG output quality (1-100)",
                parameter_group="Enhancement",
                min_value=1,
                max_value=100,
            ),
        }

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_parameters(self) -> tuple[bool, str | None]:
        base_ok, base_msg = super().validate_parameters()
        if not base_ok:
            return False, base_msg

        if not self.params.get("enhance_enabled") or not self.params["enhance_enabled"].get_value():
            return True, None  # disabled — nothing to validate

        input_dir = self.params.get("enhance_input_dir")
        if not input_dir or not input_dir.get_value():
            return False, "Enhancement input directory is required when enhancement is enabled"

        p = Path(input_dir.get_value())
        if not p.exists():
            return False, f"Enhancement input directory does not exist: {p}"
        if not p.is_dir():
            return False, f"Enhancement input path is not a directory: {p}"

        return True, None

    # ------------------------------------------------------------------ #
    # Main processing
    # ------------------------------------------------------------------ #

    def run(self) -> dict[str, object] | None:
        if not self.params.get("enhance_enabled") or not self.params["enhance_enabled"].get_value():
            self.logger.info("[Image Enhancement] Skipped (disabled)")
            return {"Success": True, "Skipped": True}

        input_dir = Path(self.params["enhance_input_dir"].get_value())
        clip_fine = self.params["enhance_clip_limit_fine"].get_value()
        clip_coarse = self.params["enhance_clip_limit_coarse"].get_value()
        contrast_alpha = self.params["enhance_contrast_alpha"].get_value()
        jpeg_quality = self.params["enhance_jpeg_quality"].get_value()

        # Output alongside input as *_enhanced/
        output_dir = input_dir.parent / f"{input_dir.name}_enhanced"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Discover files
        image_files = [
            f for f in input_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTENSIONS
        ]
        text_files = [
            f for f in input_dir.rglob("*")
            if f.is_file() and f.suffix.lower() in _TEXT_EXTENSIONS
        ]

        total = len(image_files) + len(text_files)
        if total == 0:
            self.logger.warning("[Image Enhancement] No files found in %s", input_dir)
            return {"Success": True, "ImagesProcessed": 0, "TextFilesCopied": 0}

        self.logger.info(
            "[Image Enhancement] Found %d images and %d text files in %s",
            len(image_files), len(text_files), input_dir,
        )

        bar = self._initialize_loading_bar(total, "Enhancing images")
        start_time = time.time()
        processed = 0
        skipped = 0

        for idx, img_file in enumerate(image_files, 1):
            rel_path = img_file.relative_to(input_dir)
            out_file = output_dir / rel_path
            out_file.parent.mkdir(parents=True, exist_ok=True)

            self._log_file_processing(
                "Enhancement", str(img_file), idx, len(image_files),
            )

            image = cv2.imread(str(img_file))
            if image is None:
                self.logger.warning(
                    "[Image Enhancement] Could not read %s, skipping", img_file
                )
                skipped += 1
                self._update_loading_bar(bar)
                continue

            enhanced = self._enhance_image(
                image, clip_fine, clip_coarse, contrast_alpha
            )

            if img_file.suffix.lower() in (".jpg", ".jpeg"):
                cv2.imwrite(
                    str(out_file), enhanced,
                    [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
                )
            else:
                cv2.imwrite(str(out_file), enhanced)

            processed += 1
            self._update_loading_bar(bar)

        # Copy text files
        for idx, text_file in enumerate(text_files, 1):
            rel_path = text_file.relative_to(input_dir)
            out_file = output_dir / rel_path
            out_file.parent.mkdir(parents=True, exist_ok=True)

            self._log_file_processing(
                "Copy text", str(text_file), idx, len(text_files),
            )
            shutil.copy2(text_file, out_file)
            self._update_loading_bar(bar)

        elapsed = time.time() - start_time
        self._finish_loading_bar(bar)

        self.logger.info(
            "[Image Enhancement] Complete: %d images enhanced, %d skipped, "
            "%d text files copied in %.1fs. Output: %s",
            processed, skipped, len(text_files), elapsed, output_dir,
        )

        return {
            "Success": True,
            "ImagesProcessed": processed,
            "ImagesSkipped": skipped,
            "TextFilesCopied": len(text_files),
            "OutputDirectory": str(output_dir),
            "Duration": elapsed,
        }

    # ------------------------------------------------------------------ #
    # Enhancement pipeline
    # ------------------------------------------------------------------ #

    @staticmethod
    def _enhance_image(
        image: np.ndarray,
        clip_fine: float,
        clip_coarse: float,
        contrast_alpha: float,
    ) -> np.ndarray:
        """Run the full enhancement pipeline on a single BGR image."""
        # Convert to LAB for luminance processing
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)

        # Multi-scale CLAHE
        clahe_fine = cv2.createCLAHE(clipLimit=clip_fine, tileGridSize=(16, 16))
        l_fine = clahe_fine.apply(l_channel)

        clahe_coarse = cv2.createCLAHE(clipLimit=clip_coarse, tileGridSize=(64, 64))
        l_coarse = clahe_coarse.apply(l_channel)

        # Blend: 40% fine + 40% coarse + 20% original
        l_enhanced = cv2.addWeighted(l_fine, 0.4, l_coarse, 0.4, 0)
        l_enhanced = cv2.addWeighted(l_enhanced, 0.8, l_channel, 0.2, 0)

        lab_enhanced = cv2.merge([l_enhanced, a_channel, b_channel])
        enhanced = cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

        # Gentle contrast
        enhanced = cv2.convertScaleAbs(enhanced, alpha=contrast_alpha, beta=0)

        # White balance
        enhanced = ImageEnhancement._apply_white_balance(enhanced)

        # Texture enhancement
        enhanced = ImageEnhancement._apply_texture_enhancement(enhanced)

        # Detail sharpening
        enhanced = ImageEnhancement._apply_detail_sharpening(enhanced)

        return enhanced

    @staticmethod
    def _apply_white_balance(image: np.ndarray) -> np.ndarray:
        """Minimal white balance for colour consistency."""
        result = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        avg_a = np.average(result[:, :, 1])
        avg_b = np.average(result[:, :, 2])
        result[:, :, 1] = result[:, :, 1] - (
            (avg_a - 128) * (result[:, :, 0] / 255.0) * 0.35
        )
        result[:, :, 2] = result[:, :, 2] - (
            (avg_b - 128) * (result[:, :, 0] / 255.0) * 0.35
        )
        return cv2.cvtColor(result, cv2.COLOR_LAB2BGR)

    @staticmethod
    def _apply_texture_enhancement(image: np.ndarray) -> np.ndarray:
        """High-pass filter for texture detail."""
        low_freq = cv2.GaussianBlur(image, (21, 21), 0)
        image_float = image.astype(np.float32)
        high_freq = image_float - low_freq.astype(np.float32)
        enhanced = image_float + (high_freq * 0.3)
        return np.clip(enhanced, 0, 255).astype(np.uint8)

    @staticmethod
    def _apply_detail_sharpening(image: np.ndarray) -> np.ndarray:
        """Edge-aware sharpening."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 100)
        kernel = np.ones((3, 3), np.uint8)
        edge_mask = cv2.dilate(edges, kernel, iterations=1)
        edge_mask = cv2.GaussianBlur(edge_mask, (5, 5), 0)
        edge_mask = edge_mask.astype(np.float32) / 255.0

        blurred = cv2.GaussianBlur(image, (0, 0), 1.2)
        sharpened = cv2.addWeighted(image, 1.08, blurred, -0.08, 0)

        edge_mask_3ch = cv2.merge([edge_mask, edge_mask, edge_mask])
        result = (
            sharpened * edge_mask_3ch + image * (1 - edge_mask_3ch)
        ).astype(np.uint8)
        return result
