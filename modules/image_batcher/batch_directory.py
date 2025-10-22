from __future__ import annotations
from module_base.rc_module import RCModule
from module_base.parameter import Parameter

import os
import shutil
import numpy as np
import pandas as pd
import geopandas as gpd
from sklearn.cluster import KMeans
from sklearn.neighbors import KernelDensity
from scipy.spatial import cKDTree, ConvexHull
from shapely.geometry import Point
from sklearn.preprocessing import StandardScaler
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
import glob
from typing import Optional, List
import logging
from pathlib import Path


class BatchDirectory(RCModule):
    ACCEPTED_EXTENSIONS = [".png", ".jpg", ".jpeg"]

    def __init__(self, logger):
        super().__init__("Batch Directory", logger)
        self.logger.info(f"Matplotlib {matplotlib.__version__}, Seaborn {sns.__version__}")
        self.utm_zone_suffix = None
        self._file_index = None

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['batch_target_images_per_zone'] = Parameter(
            name='Target Images Per Zone',
            cli_short='b_t',
            cli_long='b_target_images',
            type=int,
            default_value=3000,
            description='Target number of images per zone (min/max auto-calculated as +/-15%)',
            prompt_user=True
        )

        additional_params['batch_initial_overlap_percent'] = Parameter(
            name='Initial Overlap Percent',
            cli_short='b_p',
            cli_long='b_overlap_percent',
            type=float,
            default_value=20.0,
            description='The initial percent of overlap between batches.',
            prompt_user=True
        )

        additional_params['batch_density_weight'] = Parameter(
            name='Density Weight (0..1)',
            cli_short='b_dw',
            cli_long='b_density_weight',
            type=float,
            default_value=0.3,
            description='Weight of density in clustering/overlap scoring (higher favors low-density boundaries).',
            prompt_user=False
        )

        additional_params['batch_kde_bandwidth'] = Parameter(
            name='KDE Bandwidth (meters, 0=auto)',
            cli_short='b_bw',
            cli_long='b_kde_bandwidth',
            type=float,
            default_value=0.0,
            description='Kernel density bandwidth. 0 uses Scotts rule.',
            prompt_user=False
        )

        additional_params['batch_input_image_dir'] = Parameter(
            name='Input Image Folder',
            cli_short='b_i',
            cli_long='b_input',
            type=str,
            default_value=None,
            description='Directory containing the images to batch',
            prompt_user=True,
            disable_when_module_active='Extract Images'
        )

        additional_params['batch_flight_log_path'] = Parameter(
            name='Flight Log Path',
            cli_short='b_f',
            cli_long='b_flight_log_path',
            type=str,
            default_value=None,
            description='Path to the flight log file (required for geographic batching)',
            prompt_user=True,
            disable_when_module_active='Georeference Images'
        )

        return {**super().get_parameters(), **additional_params}

    def __get_input_dir(self):
        # Priority 1: Explicit batch_input_image_dir parameter
        if 'batch_input_image_dir' in self.params:
            input_dir = self.params['batch_input_image_dir'].get_value()
            if input_dir:
                input_dir = input_dir.strip().strip('"').strip("'")
                return input_dir

        # Priority 2: Check if geo_input_image_dir exists (georeferenced images location)
        if 'geo_input_image_dir' in self.params:
            geo_dir = self.params['geo_input_image_dir'].get_value()
            if geo_dir:
                geo_dir = geo_dir.strip().strip('"').strip("'")
                return geo_dir

        # Priority 3: Fallback to output_dir/raw_images or output_dir
        output_dir = self.params['output_dir'].get_value()
        if output_dir:
            output_dir = output_dir.strip().strip('"').strip("'")
            raw_images_path = os.path.join(output_dir, "raw_images")

            # If raw_images exists, use it; otherwise use output_dir directly
            if os.path.isdir(raw_images_path):
                return raw_images_path
            return output_dir

        return None

    def __get_flight_log_path(self):
        if 'batch_flight_log_path' in self.params:
            flight_log_path = self.params['batch_flight_log_path'].get_value()
            if flight_log_path:
                flight_log_path = flight_log_path.strip().strip('"').strip("'")
            return flight_log_path
        else:
            if 'geo_input_image_dir' in self.params:
                search_dir = self.params['geo_input_image_dir'].get_value()
            else:
                search_dir = self.params['output_dir'].get_value()

            if search_dir:
                search_dir = search_dir.strip().strip('"').strip("'")

            pattern = os.path.join(search_dir, "flight_log_*_UTM.txt")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

            fallback = os.path.join(search_dir, "flight_log.txt")
            if os.path.isfile(fallback):
                return fallback

            return None

    def __read_flight_log_gdf(self, flight_log_path):
        if flight_log_path is None:
            return None

        flight_log_path = flight_log_path.strip().strip('"').strip("'")

        filename = os.path.basename(flight_log_path)
        if "_UTM.txt" in filename:
            zone_part = filename.replace("flight_log_", "").replace("_UTM.txt", "")
            self.utm_zone_suffix = f"_{zone_part}"
        else:
            self.utm_zone_suffix = ""

        try:
            df = pd.read_csv(flight_log_path, delimiter=';')

            if 'Name' in df.columns:
                df = df.rename(columns={'Name': 'filename'})

            if 'X (East)' in df.columns and 'Y (North)' in df.columns:
                df = df.rename(columns={'X (East)': 'x', 'Y (North)': 'y'})
            elif 'x' not in df.columns or 'y' not in df.columns:
                self.logger.error("Flight log missing X (East) and Y (North) columns")
                return None

            df = df.dropna(subset=['x', 'y'])
            geometry = [Point(float(x), float(y)) for x, y in zip(df.x, df.y)]
            gdf = gpd.GeoDataFrame(df, geometry=geometry)

            return gdf
        except Exception as e:
            self.logger.error(f"Error reading or processing flight log: {e}")
            return None

    @staticmethod
    def __scott_bandwidth(xy: np.ndarray) -> float:
        n, d = xy.shape
        if n < 2:
            return 1.0
        std = np.std(xy, axis=0, ddof=1)
        s = float(np.mean(std))
        if s <= 0:
            s = 1.0
        factor = n ** (-1.0 / (d + 4.0))
        return max(s * factor, 1e-6)

    def __compute_density(self, coords: np.ndarray, bandwidth: float) -> np.ndarray:
        kde = KernelDensity(kernel='gaussian', bandwidth=bandwidth).fit(coords)
        log_d = kde.score_samples(coords)
        d = np.exp(log_d)
        d = np.maximum(d, np.finfo(np.float64).tiny)
        return d

    def __density_aware_kmeans(self, coords: np.ndarray, density: np.ndarray, k: int,
                               density_weight: float) -> np.ndarray:
        logd = np.log(density)
        features = np.column_stack([coords[:, 0], coords[:, 1], logd])
        scaler = StandardScaler()
        X = scaler.fit_transform(features)
        X[:, 2] *= float(density_weight)
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X)
        return labels

    def __split_zone(self, zone_gdf, density_weight):
        """Split a zone into 2 sub-zones using density-aware k-means."""
        if len(zone_gdf) < 2:
            return [zone_gdf]

        coords = np.column_stack([zone_gdf.geometry.x.to_numpy(np.float64),
                                  zone_gdf.geometry.y.to_numpy(np.float64)])
        density = zone_gdf['density'].to_numpy()

        labels = self.__density_aware_kmeans(coords, density, 2, density_weight)

        return [zone_gdf[labels == 0].copy(), zone_gdf[labels == 1].copy()]

    def __find_nearest_zone(self, zone_gdf, other_zones):
        """Find the nearest zone based on centroid distance."""
        zone_centroid = np.array([zone_gdf.geometry.x.mean(), zone_gdf.geometry.y.mean()])

        min_dist = float('inf')
        nearest_zone = None
        nearest_idx = None

        for idx, other_zone in enumerate(other_zones):
            if other_zone is zone_gdf:
                continue
            other_centroid = np.array([other_zone.geometry.x.mean(), other_zone.geometry.y.mean()])
            dist = np.linalg.norm(zone_centroid - other_centroid)

            if dist < min_dist:
                min_dist = dist
                nearest_zone = other_zone
                nearest_idx = idx

        return nearest_zone, nearest_idx

    def __adaptive_zone_creation(self, gdf, target_size, min_size, max_size, density_weight):
        """Create zones targeting specific image count with split/merge post-processing."""

        initial_k = max(2, int(np.ceil(len(gdf) / target_size)))
        self.logger.info(f"Starting with {initial_k} initial zones for {len(gdf)} images")

        coords = np.column_stack([gdf.geometry.x.to_numpy(np.float64),
                                  gdf.geometry.y.to_numpy(np.float64)])
        density = gdf['density'].to_numpy()

        labels = self.__density_aware_kmeans(coords, density, initial_k, density_weight)
        gdf['cluster'] = labels

        zones = [gdf[gdf['cluster'] == i].copy() for i in range(initial_k)]

        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            modified = False
            new_zones = []
            zones_to_merge = []

            for zone in zones:
                zone_size = len(zone)

                if zone_size > max_size:
                    self.logger.info(f"Splitting zone with {zone_size} images")
                    split_zones = self.__split_zone(zone, density_weight)
                    new_zones.extend(split_zones)
                    modified = True

                elif zone_size < min_size:
                    zones_to_merge.append(zone)

                else:
                    new_zones.append(zone)

            def remove_zone_from_list(zone_list, target_zone):
                return [z for z in zone_list if z is not target_zone]

            while zones_to_merge:
                small_zone = zones_to_merge.pop(0)

                search_zones = new_zones + zones_to_merge
                nearest_zone, nearest_idx = self.__find_nearest_zone(small_zone, search_zones)

                if nearest_zone is not None:
                    combined_size = len(small_zone) + len(nearest_zone)

                    if combined_size <= max_size:
                        self.logger.info(f"Merging zones: {len(small_zone)} + {len(nearest_zone)} = {combined_size}")
                        merged = pd.concat([small_zone, nearest_zone])

                        new_zones = remove_zone_from_list(new_zones, nearest_zone)
                        zones_to_merge = remove_zone_from_list(zones_to_merge, nearest_zone)

                        new_zones.append(merged)
                        modified = True
                    else:
                        new_zones.append(small_zone)
                else:
                    new_zones.append(small_zone)

            zones = new_zones

            if not modified:
                self.logger.info(f"Converged after {iteration} iterations")
                break

        for i, zone in enumerate(zones):
            zone['cluster'] = i

        final_gdf = pd.concat(zones, ignore_index=True)

        return final_gdf, len(zones)

    def __create_geographic_zones(self, gdf, target_size, min_size, max_size,
                                  overlap_percent, density_weight, kde_bw):
        if gdf is None or gdf.empty:
            return [], {}, None

        coords = np.column_stack([gdf.geometry.x.to_numpy(np.float64),
                                  gdf.geometry.y.to_numpy(np.float64)])

        bw = float(kde_bw)
        if bw <= 0.0:
            bw = self.__scott_bandwidth(coords)
        self.logger.info(f"KDE bandwidth used: {bw:.6g}")

        density = self.__compute_density(coords, bw)
        gdf['density'] = density

        gdf_processed, num_zones = self.__adaptive_zone_creation(
            gdf, target_size, min_size, max_size, density_weight
        )

        base_zones_gdf = [gdf_processed[gdf_processed['cluster'] == i] for i in range(num_zones)]
        base_zones_files = {i: zone['filename'].tolist() for i, zone in enumerate(base_zones_gdf)}

        final_zones = []
        if overlap_percent > 0:
            for i in range(num_zones):
                zone_i = base_zones_gdf[i]
                other = gdf_processed[gdf_processed['cluster'] != i]

                final_zone_files = list(base_zones_files[i])

                if other.empty or zone_i.empty:
                    final_zones.append(final_zone_files)
                    continue

                overlap_size = int(len(zone_i) * (overlap_percent / 100.0))
                if overlap_size <= 0:
                    final_zones.append(final_zone_files)
                    continue

                tree = cKDTree(np.column_stack([zone_i.geometry.x.to_numpy(np.float64),
                                                zone_i.geometry.y.to_numpy(np.float64)]))
                other_xy = np.column_stack([other.geometry.x.to_numpy(np.float64),
                                            other.geometry.y.to_numpy(np.float64)])
                dists, _ = tree.query(other_xy, k=1)

                other_density = other['density'].to_numpy()
                invdens = 1.0 / other_density

                d_ptp = np.ptp(dists)
                d_norm = (dists - dists.min()) / (d_ptp if d_ptp > 0 else 1.0)

                invdens_ptp = np.ptp(invdens)
                invdens_norm = (invdens - invdens.min()) / (invdens_ptp if invdens_ptp > 0 else 1.0)

                w_d = 0.7
                w_den = 0.3 if density_weight <= 0 else min(max(density_weight, 0.0), 1.0)
                score = w_d * d_norm + w_den * invdens_norm

                idx = np.argsort(score)[:overlap_size]
                files_to_add = other.iloc[idx]['filename'].tolist()

                final_zone_files.extend(files_to_add)
                final_zones.append(final_zone_files)
        else:
            final_zones = [files for _, files in base_zones_files.items()]

        return final_zones, base_zones_files, gdf_processed

    def __plot_results(self, gdf, zones, output_dir):
        os.makedirs(output_dir, exist_ok=True)

        x = gdf.geometry.x.to_numpy(dtype=np.float64, copy=False)
        y = gdf.geometry.y.to_numpy(dtype=np.float64, copy=False)

        fig1, ax1 = plt.subplots(figsize=(12, 10))
        try:
            sns.kdeplot(x=x, y=y, ax=ax1, cmap="viridis", fill=True, levels=25, bw_adjust=1.0, thresh=None)
            sc = ax1.scatter(x, y, c=gdf['density'].to_numpy(), cmap='viridis', s=10)
            cbar = fig1.colorbar(sc, ax=ax1)
            cbar.set_label('Density')
        except Exception as e:
            self.logger.warning(f"seaborn.kdeplot failed ({type(e).__name__}: {e}). Falling back to manual grid.")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)
                nx = ny = 200
                xmin, xmax = float(np.nanmin(x)), float(np.nanmax(x))
                ymin, ymax = float(np.nanmin(y)), float(np.nanmax(y))
                if xmax == xmin:
                    xmax = xmin + 1.0
                if ymax == ymin:
                    ymax = ymin + 1.0
                xi = np.linspace(xmin, xmax, nx)
                yi = np.linspace(ymin, ymax, ny)
                Xi, Yi = np.meshgrid(xi, yi)
                H, _, _ = np.histogram2d(x, y, bins=[nx, ny], density=True)
                Z = H.T
                zmin, zmax = float(np.nanmin(Z)), float(np.nanmax(Z))
                if not np.isfinite(zmin) or not np.isfinite(zmax) or zmax == zmin:
                    zmin, zmax = 0.0, 1.0
                levels = np.linspace(zmin, zmax, 25)
                levels = np.unique(levels)
                if levels.size < 2:
                    levels = np.array([zmin, zmax], dtype=float)
                cf = ax1.contourf(Xi, Yi, Z, levels=levels, cmap="viridis", antialiased=True)
                cbar = fig1.colorbar(cf, ax=ax1)
                cbar.set_label('Density (proxy)')
                sc = ax1.scatter(x, y, c=gdf['density'].to_numpy(), cmap='viridis', s=10)

        ax1.set_title('Kernel Density Estimation of Image Locations')
        ax1.set_xlabel('X (Easting)')
        ax1.set_ylabel('Y (Northing)')
        kernel_plot_path = os.path.join(output_dir, 'kernel_density.png')
        fig1.savefig(kernel_plot_path, bbox_inches='tight')
        try:
            plt.show()
        except Exception:
            pass
        plt.close(fig1)
        self.logger.info(f"Kernel density plot saved to: {kernel_plot_path}")

        fig2, ax2 = plt.subplots(figsize=(12, 10))
        palette = sns.color_palette("husl", len(zones))
        ax2.scatter(x, y, color='gray', s=10, alpha=0.2, label='All Points')

        for i, zone_files in enumerate(zones):
            zone_gdf = gdf[gdf['filename'].isin(zone_files)]
            color = palette[i]
            zx = zone_gdf.geometry.x.to_numpy(dtype=np.float64, copy=False)
            zy = zone_gdf.geometry.y.to_numpy(dtype=np.float64, copy=False)
            ax2.scatter(zx, zy, color=color, label=f'Zone {i + 1}', s=25, alpha=0.8)

            if len(zone_gdf) >= 3:
                try:
                    points = np.column_stack([zx, zy])
                    hull = ConvexHull(points)
                    for simplex in hull.simplices:
                        ax2.plot(points[simplex, 0], points[simplex, 1], color=color, linewidth=2.0)
                except Exception as e:
                    self.logger.warning(f"Could not generate convex hull for Zone {i + 1}: {e}")

        ax2.set_title('Image Batches by Geographic Zone')
        ax2.set_xlabel('X (Easting)')
        ax2.set_ylabel('Y (Northing)')
        ax2.legend()
        zones_plot_path = os.path.join(output_dir, 'batch_zones.png')
        fig2.savefig(zones_plot_path, bbox_inches='tight')
        try:
            plt.show()
        except Exception:
            pass
        plt.close(fig2)
        self.logger.info(f"Batch zones plot saved to: {zones_plot_path}")

    def __determine_camera_subfolder(self, filename):
        """
        Determine camera subfolder based on filename.
        Uses same logic as GeoreferenceImages module for consistency.
        """
        if not filename:
            self.logger.warning("Empty filename provided to __determine_camera_subfolder")
            return "other"

        filename_lower = filename.lower()

        # Check specific camera types (most specific to least specific)
        if filename_lower.startswith('camupper'):
            return 'camupper'
        elif filename_lower.startswith('cammid'):
            return 'cammid'
        elif filename_lower.startswith('camlower'):
            return 'camlower'
        elif '_herc_' in filename_lower:
            return 'zeuss'

        # Additional prefix-based detection
        # U prefix = upper camera, C prefix = lower camera
        elif filename.startswith('U'):
            return 'camupper'
        elif filename.startswith('C'):
            return 'camlower'

        # Fallback: check for generic zeuss indicators
        elif 'zeuss' in filename_lower or 'herc' in filename_lower:
            return 'zeuss'

        else:
            return 'other'

    def __build_file_index(self, input_dir: str) -> dict[str, str]:
        """Build filename → full_path index once. O(n) instead of O(n²)."""
        if not input_dir or not os.path.isdir(input_dir):
            raise ValueError(f"Invalid input directory: {input_dir}")

        self.logger.info(f"Building file index for {input_dir}...")

        file_index = {}
        duplicate_count = 0

        total_files = sum(1 for _, _, files in os.walk(input_dir)
                          for f in files if os.path.splitext(f)[1].lower() in self.ACCEPTED_EXTENSIONS)

        if total_files == 0:
            raise ValueError(f"No valid image files found in {input_dir}")

        if total_files > 1000:
            bar = self._initialize_loading_bar(total_files, 'Indexing Files')
            files_processed = 0
        else:
            bar = None

        for root, _, filenames in os.walk(input_dir):
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in self.ACCEPTED_EXTENSIONS:
                    full_path = os.path.join(root, filename)

                    if filename in file_index:
                        duplicate_count += 1
                        self.logger.warning(
                            f"Duplicate filename found: {filename} (keeping first occurrence)"
                        )
                    else:
                        file_index[filename] = full_path

                    if bar:
                        files_processed += 1
                        if files_processed % 100 == 0:
                            self._update_loading_bar(bar, 100)

        if bar:
            self._finish_loading_bar(bar)

        self.logger.info(f"Indexed {len(file_index)} files ({duplicate_count} duplicates skipped)")

        return file_index

    def __copy_files(
            self,
            input_dir: str,
            batch_folder_dir: str,
            files: List[str]
    ) -> dict:
        """
        Copy files to camera-specific subfolders.

        Args:
            input_dir: Source directory containing images
            batch_folder_dir: Destination zone directory
            files: List of filenames to copy

        Returns:
            dict: Statistics about the copy operation

        Raises:
            RuntimeError: If critical files cannot be copied
        """
        if self._file_index is None:
            self._file_index = self.__build_file_index(input_dir)

        stats = {
            'copied': 0,
            'skipped_existing': 0,
            'skipped_notfound': 0,
            'errors': []
        }

        bar = self._initialize_loading_bar(len(files), 'Copying Files')

        for file in files:
            try:
                camera_subfolder = self.__determine_camera_subfolder(file)
                camera_dir = os.path.join(batch_folder_dir, camera_subfolder)
                os.makedirs(camera_dir, exist_ok=True)

                file_path = self._file_index.get(file)

                if file_path is None:
                    self.logger.warning(f"File not found in index: {file}")
                    stats['skipped_notfound'] += 1
                    self._update_loading_bar(bar, 1)
                    continue

                output_path = os.path.join(camera_dir, file)

                if os.path.exists(output_path):
                    stats['skipped_existing'] += 1
                else:
                    shutil.copy2(file_path, output_path)
                    stats['copied'] += 1

                self._update_loading_bar(bar, 1)

            except Exception as e:
                self.logger.error(f"Failed to process {file}: {e}")
                stats['errors'].append(f"{file}: {str(e)}")
                self._update_loading_bar(bar, 1)

                error_rate = len(stats['errors']) / len(files)
                if error_rate > 0.05 and len(stats['errors']) % 100 == 0:
                    self.logger.error(
                        f"High failure rate: {error_rate * 100:.1f}% "
                        f"({len(stats['errors'])}/{len(files)} files). "
                        f"Recent errors: {stats['errors'][-5:]}"
                    )

        self._finish_loading_bar(bar)

        self.logger.info(f"File copy summary:")
        self.logger.info(f"  Copied: {stats['copied']}")
        self.logger.info(f"  Skipped (existing): {stats['skipped_existing']}")
        self.logger.info(f"  Skipped (not found): {stats['skipped_notfound']}")

        if stats['errors']:
            self.logger.warning(f"Total errors: {len(stats['errors'])}")
            self.logger.warning(f"First 5 errors: {stats['errors'][:5]}")

        if stats['copied'] == 0 and stats['skipped_existing'] == 0:
            raise RuntimeError("No files were copied - check input directory and file list")

        return stats

    def __create_batch_folders(self, output_dir, zones, input_dir, flight_log_path=None):
        """
        Create per-zone folders and write zone-specific flight logs including all original columns.

        Returns:
            dict: Aggregate statistics across all zones
        """
        if not zones:
            raise ValueError('No geographic zones were created.')

        if self._file_index is None:
            self._file_index = self.__build_file_index(input_dir)

        flight_log_df = None
        if flight_log_path and os.path.isfile(flight_log_path):
            try:
                flight_log_df = pd.read_csv(flight_log_path, delimiter=';', dtype=str, keep_default_na=False)
                if 'Name' in flight_log_df.columns:
                    flight_log_df = flight_log_df.rename(columns={'Name': 'filename'})
                flight_log_df.set_index('filename', inplace=True)
            except Exception as e:
                self.logger.error(f"Error reading flight log: {e}")
                raise

        # Aggregate stats across all zones
        aggregate_stats = {
            'copied': 0,
            'skipped_existing': 0,
            'skipped_notfound': 0,
            'total_errors': 0
        }

        bar = self._initialize_loading_bar(len(zones), 'Creating Batch Folders')

        for i, zone_files in enumerate(zones):
            batch_folder_name = f"zone_{i + 1}"
            batch_folder_dir = os.path.join(output_dir, batch_folder_name)
            os.makedirs(batch_folder_dir, exist_ok=True)

            unique_zone_files = list(dict.fromkeys(zone_files))
            zone_stats = self.__copy_files(input_dir, batch_folder_dir, unique_zone_files)

            # Aggregate statistics
            aggregate_stats['copied'] += zone_stats['copied']
            aggregate_stats['skipped_existing'] += zone_stats['skipped_existing']
            aggregate_stats['skipped_notfound'] += zone_stats['skipped_notfound']
            aggregate_stats['total_errors'] += len(zone_stats['errors'])

            if flight_log_df is not None:
                try:
                    zone_flight_log_df = flight_log_df.loc[
                        flight_log_df.index.isin(unique_zone_files)
                    ].copy()

                    missing = [col for col in flight_log_df.columns if col not in zone_flight_log_df.columns]
                    for col in missing:
                        zone_flight_log_df[col] = ""

                    if self.utm_zone_suffix:
                        batch_flight_log_name = f'flight_log{self.utm_zone_suffix}_UTM.txt'
                    else:
                        batch_flight_log_name = 'flight_log.txt'

                    batch_flight_log_path = os.path.join(batch_folder_dir, batch_flight_log_name)

                    zone_flight_log_df.to_csv(
						batch_flight_log_path,
                        sep=';',
                        index=True,
                        index_label='filename',
                        columns=flight_log_df.columns
                    )

                except Exception as e:
                    self.logger.error(f"Error writing flight log for zone {i + 1}: {e}")

            self._update_loading_bar(bar, 1)

        self._finish_loading_bar(bar)

        # Log aggregate summary
        self.logger.info(f"\nAggregate statistics across all {len(zones)} zones:")
        self.logger.info(f"  Total files copied: {aggregate_stats['copied']}")
        self.logger.info(f"  Total files skipped (existing): {aggregate_stats['skipped_existing']}")
        self.logger.info(f"  Total files skipped (not found): {aggregate_stats['skipped_notfound']}")
        self.logger.info(f"  Total errors: {aggregate_stats['total_errors']}")

        return aggregate_stats

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        try:
            output_dir = self.params['output_dir'].get_value()
            if output_dir:
                output_dir = output_dir.strip().strip('"').strip("'")

            output_dir = os.path.join(output_dir, 'batched_images_by_zone')
            input_dir = self.__get_input_dir()
            flight_log_path = self.__get_flight_log_path()

            gdf = self.__read_flight_log_gdf(flight_log_path)
            if gdf is None or gdf.empty:
                self.logger.error("Could not process flight log for geographic batching.")
                return {'Success': False}

            self.logger.info(f"Total number of georeferenced points: {len(gdf)}")

            target_size = int(self.params['batch_target_images_per_zone'].get_value())

            min_size = int(target_size * 0.85)
            max_size = int(target_size * 1.15)

            overlap_percent = float(self.params['batch_initial_overlap_percent'].get_value())
            density_weight = float(self.params['batch_density_weight'].get_value())
            kde_bw = float(self.params['batch_kde_bandwidth'].get_value())

            self.logger.info(f"Target zone size: {target_size} images (auto-calculated range: {min_size}-{max_size})")
            if self.utm_zone_suffix:
                self.logger.info(f"UTM zone suffix detected: {self.utm_zone_suffix}")

            no_prompt = os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')

            while True:
                final_zones, base_zones, gdf_processed = self.__create_geographic_zones(
                    gdf, target_size, min_size, max_size, overlap_percent, density_weight, kde_bw
                )

                print("\n--- Batch Summary ---")
                print(f"Total unique images: {len(gdf)}")
                print(f"Number of zones created: {len(final_zones)}")
                print(f"Target: {target_size} images/zone (range: {min_size}-{max_size}, ±15%)")
                print("\nPer-zone breakdown:")

                total_in_batches = 0
                for i in range(len(final_zones)):
                    final_files_in_zone = list(dict.fromkeys(final_zones[i]))
                    total_count = len(final_files_in_zone)
                    base_count = len(base_zones[i])
                    overlap_count = total_count - base_count
                    total_in_batches += total_count

                    status = "OK"
                    if total_count > max_size:
                        status = "OVERSIZED"
                    elif total_count < min_size:
                        status = "UNDERSIZED"

                    print(
                        f"  Zone {i + 1}: {total_count:4d} images ({base_count:4d} base + {overlap_count:3d} overlap) [{status}]")

                print(f"\nTotal images across all batches: {total_in_batches}")
                print(f"Average zone size: {total_in_batches / len(final_zones):.0f} images")
                print("---------------------\n")

                self.__plot_results(gdf_processed, final_zones, output_dir)

                if no_prompt:
                    user_input = 'a'
                else:
                    user_input = input(
                        "Accept these batches? [a/yes/y] accept, [r/no/n] reject and adjust: ").lower().strip()

                # Accept: a, yes, y, or just pressing enter (default to accept)
                if user_input in ('a', 'accept', 'yes', 'y', ''):
                    self.logger.info("Batches accepted. Proceeding to copy files.")
                    break
                # Reject: r, reject, no, n
                elif user_input in ('r', 'reject', 'no', 'n'):
                    while True:
                        try:
                            new_target = input(f"Enter new target images per zone (current: {target_size}): ").strip()
                            if not new_target:
                                print("Keeping current target.")
                                break
                            new_target = int(new_target)
                            if new_target >= 100:
                                target_size = new_target
                                min_size = int(target_size * 0.85)
                                max_size = int(target_size * 1.15)
                                break
                            else:
                                print("Please enter a value >= 100.")
                        except ValueError:
                            print("Invalid input. Please enter an integer.")

                    while True:
                        try:
                            new_overlap = input(f"Enter new overlap percentage (current: {overlap_percent}): ").strip()
                            if not new_overlap:
                                print("Keeping current overlap.")
                                break
                            new_overlap = float(new_overlap)
                            if 0.0 <= new_overlap <= 100.0:
                                overlap_percent = new_overlap
                                break
                            else:
                                print("Please enter a value between 0 and 100.")
                        except ValueError:
                            print("Invalid input. Please enter a number.")

                    if os.path.isdir(output_dir):
                        shutil.rmtree(output_dir)
                    os.makedirs(output_dir)
                    continue
                else:
                    print("Invalid input. Please enter 'a/yes/y' to accept or 'r/no/n' to reject.")

            batch_stats = self.__create_batch_folders(output_dir, final_zones, input_dir, flight_log_path)

            avg_zone_size = (total_in_batches / len(final_zones)) if final_zones and len(final_zones) > 0 else 0

            return {
                'Success': True,
                'Number of Zones': len(final_zones),
                'Target Zone Size': target_size,
                'Min Zone Size': min_size,
                'Max Zone Size': max_size,
                'Average Zone Size': int(avg_zone_size),
                'Final Overlap': f"{overlap_percent}%",
                'Total Unique Images': len(gdf),
                'Total Images in Batches': total_in_batches,
                'Files Copied': batch_stats['copied'],
                'Files Skipped (Existing)': batch_stats['skipped_existing'],
                'Files Skipped (Not Found)': batch_stats['skipped_notfound'],
                'Total Errors': batch_stats['total_errors'],
                'Output Directory': output_dir,
                'UTM Zone': self.utm_zone_suffix or 'N/A'
            }

        except RuntimeError as e:
            self.logger.error(f"Batch folder creation failed: {e}")
            return {'Success': False, 'Error': str(e)}
        except ValueError as e:
            self.logger.error(f"Invalid parameter or data: {e}")
            return {'Success': False, 'Error': str(e)}
        except Exception as e:
            self.logger.error(f"Unexpected error during batching: {e}")
            return {'Success': False, 'Error': str(e)}

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if 'batch_target_images_per_zone' not in self.params:
            return False, 'Target images per zone parameter not found'

        target = self.params['batch_target_images_per_zone'].get_value()
        if not isinstance(target, int) or target < 100:
            return False, 'Target images per zone must be an integer >= 100'

        if 'batch_initial_overlap_percent' not in self.params:
            return False, 'Initial overlap percent parameter not found'

        overlap = self.params['batch_initial_overlap_percent'].get_value()
        if not isinstance(overlap, (int, float)) or not (0 <= overlap <= 100):
            return False, 'Overlap percent must be a number between 0 and 100'

        # Validate input directory
        input_dir = self.__get_input_dir()
        if input_dir is None:
            return False, 'Input directory could not be determined. Ensure output_dir or batch_input_image_dir is set.'

        if not os.path.isdir(input_dir):
            return False, f'Input directory does not exist: {input_dir}'

        # Check if input directory has any valid image files
        has_images = False
        for root, _, files in os.walk(input_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in self.ACCEPTED_EXTENSIONS:
                    has_images = True
                    break
            if has_images:
                break

        if not has_images:
            return False, f'Input directory contains no valid image files: {input_dir}'

        # Validate flight log
        flight_log_path = self.__get_flight_log_path()
        if not flight_log_path:
            return False, 'Flight log path could not be determined. Run Georeference Images first or specify batch_flight_log_path.'

        if not os.path.isfile(flight_log_path):
            return False, f'Flight log file does not exist: {flight_log_path}'

        # Check flight log is readable
        try:
            with open(flight_log_path, 'r') as f:
                f.read(1)
        except Exception as e:
            return False, f'Cannot read flight log file: {e}'

        # Validate and prepare output directory
        output_dir = self.params['output_dir'].get_value()
        if output_dir is None:
            return False, 'Output directory parameter is not set'

        output_dir = output_dir.strip().strip('"').strip("'")
        output_dir = os.path.join(output_dir, 'batched_images_by_zone')

        # Check for existing output and handle cleanup
        if os.path.isdir(output_dir):
            existing_files = os.listdir(output_dir)

            if existing_files:
                no_prompt = os.environ.get('RC_NO_PROMPT', '').strip().lower() in ('1', 'true', 'yes', 'y')
                auto_overwrite = os.environ.get('RC_OVERWRITE', '').strip().lower() in ('1', 'true', 'yes', 'y')

                if auto_overwrite or no_prompt:
                    self.logger.warning(
                        f'Batched images folder exists with {len(existing_files)} items. '
                        f'Auto-overwriting due to RC_OVERWRITE/RC_NO_PROMPT.'
                    )
                    try:
                        shutil.rmtree(output_dir)
                        self.logger.info(f'Deleted existing directory: {output_dir}')
                    except Exception as e:
                        return False, f'Failed to delete existing directory: {e}'
                else:
                    self.logger.warning('=' * 70)
                    self.logger.warning(f'OUTPUT DIRECTORY ALREADY EXISTS: {output_dir}')
                    self.logger.warning(f'Contains {len(existing_files)} items')
                    self.logger.warning('This may include:')
                    self.logger.warning('  - Previous zone folders with images')
                    self.logger.warning('  - Previous flight log files')
                    self.logger.warning('  - Previous visualization plots')
                    self.logger.warning('=' * 70)

                    print('\nExisting batched images directory detected!')
                    print(f'Location: {output_dir}')
                    print(f'Contains: {len(existing_files)} items')
                    print('\nOptions:')
                    print('  (d) Delete and recreate - Remove all existing data')
                    print('  (c) Cancel - Stop execution, keep existing data')
                    print('  (i) Inspect - Show first 10 items in directory')

                    while True:
                        user_choice = input('\nYour choice [d/c/i]: ').lower().strip()

                        if user_choice == 'd':
                            try:
                                self.logger.info(f'User chose to delete existing directory: {output_dir}')
                                shutil.rmtree(output_dir)
                                self.logger.info('Existing directory deleted successfully')
                                break
                            except Exception as e:
                                self.logger.error(f'Failed to delete directory: {e}')
                                return False, f'Failed to delete existing directory: {e}'

                        elif user_choice == 'c':
                            self.logger.info('User cancelled execution to preserve existing data')
                            return False, 'User cancelled to preserve existing batched images'

                        elif user_choice == 'i':
                            print('\nFirst 10 items in directory:')
                            for idx, item in enumerate(existing_files[:10], 1):
                                item_path = os.path.join(output_dir, item)
                                if os.path.isdir(item_path):
                                    item_type = 'DIR'
                                    try:
                                        item_count = len(os.listdir(item_path))
                                        item_info = f'({item_count} items)'
                                    except:
                                        item_info = '(access denied)'
                                else:
                                    item_type = 'FILE'
                                    try:
                                        size_kb = os.path.getsize(item_path) / 1024
                                        item_info = f'({size_kb:.1f} KB)'
                                    except:
                                        item_info = '(size unknown)'
                                print(f'  {idx:2d}. [{item_type:4s}] {item} {item_info}')

                            if len(existing_files) > 10:
                                print(f'  ... and {len(existing_files) - 10} more items')
                            print()

                        else:
                            print('Invalid choice. Please enter d, c, or i.')

        # Create output directory if it doesn't exist
        if not os.path.isdir(output_dir):
            try:
                os.makedirs(output_dir)
                self.logger.info(f'Created output directory: {output_dir}')
            except Exception as e:
                return False, f'Failed to create output directory: {e}'

        return True, None