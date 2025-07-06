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
import matplotlib.pyplot as plt
import seaborn as sns


class BatchDirectory(RCModule):
    ACCEPTED_EXTENSIONS = [".png", ".jpg", ".jpeg"]

    def __init__(self, logger):
        super().__init__("Batch Directory", logger)

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['batch_num_zones'] = Parameter(
            name='Number of Zones',
            cli_short='b_z',
            cli_long='b_num_zones',
            type=int,
            default_value=4,
            description='The number of geographic zones to batch images into.',
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
        if 'batch_input_image_dir' in self.params:
            return self.params['batch_input_image_dir'].get_value()
        else:
            return os.path.join(self.params['output_dir'].get_value(), "raw_images")

    def __get_flight_log_path(self):
        if 'batch_flight_log_path' in self.params:
            return self.params['batch_flight_log_path'].get_value()
        else:
            if 'geo_input_image_dir' in self.params:
                return os.path.join(self.params['geo_input_image_dir'].get_value(), "flight_log.txt")
            else:
                return os.path.join(self.params['output_dir'].get_value(), "flight_log.txt")

    def __get_flight_log_gdf(self, flight_log_path):
        if flight_log_path is None:
            return None
        try:
            df = pd.read_csv(flight_log_path, delimiter=';')
            df = df.rename(columns={'Name': 'filename', 'X (East)': 'x', 'Y (North)': 'y'})
            df = df[['filename', 'x', 'y']].dropna(subset=['x', 'y'])
            geometry = [Point(xy) for xy in zip(df.x, df.y)]
            gdf = gpd.GeoDataFrame(df, geometry=geometry)
            return gdf
        except Exception as e:
            self.logger.error(f"Error reading or processing flight log: {e}")
            return None

    def __create_geographic_zones(self, gdf, num_zones, overlap_percent):
        if gdf is None or gdf.empty:
            return [], {}, None

        coords = np.array(list(gdf.geometry.apply(lambda p: (p.x, p.y))))
        kmeans = KMeans(n_clusters=num_zones, random_state=42, n_init=10).fit(coords)
        gdf['cluster'] = kmeans.labels_

        base_zones_gdf = [gdf[gdf['cluster'] == i] for i in range(num_zones)]
        base_zones_files = {i: zone['filename'].tolist() for i, zone in enumerate(base_zones_gdf)}

        final_zones = []
        if overlap_percent > 0:
            for i in range(num_zones):
                zone_i_gdf = base_zones_gdf[i]
                other_gdf = gdf[gdf['cluster'] != i]

                final_zone_files = list(base_zones_files[i])

                if other_gdf.empty:
                    final_zones.append(final_zone_files)
                    continue

                overlap_size = int(len(zone_i_gdf) * (overlap_percent / 100))
                if overlap_size == 0:
                    final_zones.append(final_zone_files)
                    continue

                tree = cKDTree(zone_i_gdf.geometry.apply(lambda p: (p.x, p.y)).tolist())
                distances, _ = tree.query(other_gdf.geometry.apply(lambda p: (p.x, p.y)).tolist(), k=1)

                other_gdf_with_dist = other_gdf.copy()
                other_gdf_with_dist['distance_to_zone'] = distances

                closest_external_points = other_gdf_with_dist.sort_values('distance_to_zone')
                files_to_add = closest_external_points.head(overlap_size)['filename'].tolist()

                final_zone_files.extend(files_to_add)
                final_zones.append(final_zone_files)
        else:
            final_zones = [files for _, files in base_zones_files.items()]

        kde = KernelDensity(kernel='gaussian', bandwidth=0.5).fit(coords)
        gdf['density'] = np.exp(kde.score_samples(coords))

        return final_zones, base_zones_files, gdf

    def __plot_results(self, gdf, zones, output_dir):
        plt.figure(figsize=(12, 10))
        sns.kdeplot(x=gdf.geometry.x, y=gdf.geometry.y, cmap="viridis", fill=True, thresh=0.05)
        plt.scatter(gdf.geometry.x, gdf.geometry.y, c=gdf['density'], cmap='viridis', s=10)
        plt.title('Kernel Density Estimation of Image Locations')
        plt.xlabel('X (Easting)')
        plt.ylabel('Y (Northing)')
        plt.colorbar(label='Density')
        kernel_plot_path = os.path.join(output_dir, 'kernel_density.png')
        plt.savefig(kernel_plot_path)
        plt.close()
        self.logger.info(f"Kernel density plot saved to: {kernel_plot_path}")

        plt.figure(figsize=(12, 10))
        palette = sns.color_palette("husl", len(zones))
        plt.scatter(gdf.geometry.x, gdf.geometry.y, color='gray', s=10, alpha=0.2, label='All Points')

        for i, zone_files in enumerate(zones):
            zone_gdf = gdf[gdf['filename'].isin(zone_files)]
            color = palette[i]
            plt.scatter(zone_gdf.geometry.x, zone_gdf.geometry.y, color=color, label=f'Zone {i + 1}', s=25, alpha=0.8)

            if len(zone_gdf) >= 3:
                try:
                    points = zone_gdf.geometry.apply(lambda p: (p.x, p.y)).tolist()
                    hull = ConvexHull(points)
                    for simplex in hull.simplices:
                        plt.plot(np.array(points)[simplex, 0], np.array(points)[simplex, 1], color=color, linewidth=2.0)
                except Exception as e:
                    self.logger.warning(f"Could not generate convex hull for Zone {i + 1}: {e}")

        plt.title('Image Batches by Geographic Zone')
        plt.xlabel('X (Easting)')
        plt.ylabel('Y (Northing)')
        plt.legend()
        zones_plot_path = os.path.join(output_dir, 'batch_zones.png')
        plt.savefig(zones_plot_path)
        plt.close()
        self.logger.info(f"Batch zones plot saved to: {zones_plot_path}")

    def __copy_files(self, input_dir, batch_folder_dir, files):
        for file in files:
            file_path = os.path.join(input_dir, file)
            output_path = os.path.join(batch_folder_dir, file)
            if not os.path.exists(output_path):
                shutil.copy(file_path, output_path)

    def __create_batch_folders(self, output_dir, zones, input_dir, flight_log_path=None):
        if not zones:
            raise ValueError('No geographic zones were created.')

        flight_log_df = None
        if flight_log_path:
            flight_log_df = pd.read_csv(flight_log_path, delimiter=';').set_index('Name')

        bar = self._initialize_loading_bar(len(zones), 'Creating Batch Folders')
        for i, zone_files in enumerate(zones):
            batch_folder_name = f"zone_{i + 1}"
            batch_folder_dir = os.path.join(output_dir, batch_folder_name)

            if not os.path.isdir(batch_folder_dir):
                os.makedirs(batch_folder_dir)

            unique_zone_files = list(dict.fromkeys(zone_files))
            self.__copy_files(input_dir, batch_folder_dir, unique_zone_files)

            if flight_log_df is not None:
                batch_flight_log_path = os.path.join(batch_folder_dir, 'flight_log.txt')
                zone_flight_log_df = flight_log_df.loc[flight_log_df.index.isin(unique_zone_files)]
                zone_flight_log_df.to_csv(batch_flight_log_path, sep=';')

            self._update_loading_bar(bar, 1)

    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        num_zones = self.params['batch_num_zones'].get_value()
        overlap_percent = self.params['batch_initial_overlap_percent'].get_value()
        output_dir = os.path.join(self.params['output_dir'].get_value(), 'batched_images_by_zone')
        input_dir = self.__get_input_dir()
        flight_log_path = self.__get_flight_log_path()

        gdf = self.__get_flight_log_gdf(flight_log_path)
        if gdf is None or gdf.empty:
            self.logger.error("Could not process flight log for geographic batching.")
            return {'Success': False}

        self.logger.info(f"Total number of georeferenced points: {len(gdf)}")

        while True:
            final_zones, base_zones, gdf_processed = self.__create_geographic_zones(gdf, num_zones, overlap_percent)

            print("\n--- Batch Summary ---")
            total_in_batches = 0
            for i in range(num_zones):
                # De-duplicate final list for accurate counting
                final_files_in_zone = list(dict.fromkeys(final_zones[i]))

                total_count = len(final_files_in_zone)
                base_count = len(base_zones[i])
                overlap_count = total_count - base_count

                total_in_batches += total_count

                print(f"Zone {i + 1}: {total_count} images ({base_count} base + {overlap_count} overlap)")

            print(f"Total images across all batches: {total_in_batches} ({len(gdf)} unique)")
            print("---------------------\n")

            self.__plot_results(gdf_processed, final_zones, output_dir)

            user_input = input("Accept these batches? (a)ccept, (r)eject and set new overlap: ").lower()

            if user_input == 'a':
                self.logger.info("Batches accepted. Proceeding to copy files.")
                break
            elif user_input == 'r':
                while True:
                    try:
                        new_overlap = float(input("Enter new overlap percentage (e.g., 25): "))
                        if 0 <= new_overlap <= 100:
                            overlap_percent = new_overlap
                            if os.path.isdir(output_dir):
                                shutil.rmtree(output_dir)
                            os.makedirs(output_dir)
                            break
                        else:
                            print("Please enter a value between 0 and 100.")
                    except ValueError:
                        print("Invalid input. Please enter a number.")
            else:
                print("Invalid input. Please enter 'a' or 'r'.")

        try:
            self.__create_batch_folders(output_dir, final_zones, input_dir, flight_log_path)
            return {
                'Success': True,
                'Number of Zones': len(final_zones),
                'Final Overlap': f"{overlap_percent}%",
                'Total Unique Images': len(gdf),
                'Total Images in Batches': total_in_batches,
                'Output Directory': output_dir
            }
        except ValueError as e:
            self.logger.error(e)
            return {'Success': False}

    def validate_parameters(self) -> (bool, str):
        success, message = super().validate_parameters()
        if not success:
            return success, message

        if 'batch_num_zones' not in self.params or self.params['batch_num_zones'].get_value() < 1:
            return False, 'Number of zones is invalid'

        if 'batch_initial_overlap_percent' not in self.params:
            return False, 'Initial overlap percent parameter not found'

        overlap = self.params['batch_initial_overlap_percent'].get_value()
        if not (0 <= overlap <= 100):
            return False, 'Overlap percent must be between 0 and 100'

        input_dir = self.__get_input_dir()
        if not os.path.isdir(input_dir):
            return False, 'Input directory does not exist'

        flight_log_path = self.__get_flight_log_path()
        if not flight_log_path or not os.path.isfile(flight_log_path):
            return False, 'A valid flight log is required for geographic batching.'

        output_dir = os.path.join(self.params['output_dir'].get_value(), 'batched_images_by_zone')
        if os.path.isdir(output_dir) and os.listdir(output_dir):
            self.logger.warning('Batched images folder already exists and may contain old plots. Overwrite? (y/n)')
            overwrite = input()
            if overwrite.lower() != 'y':
                return False, 'Batched images folder not created'
            else:
                shutil.rmtree(output_dir)

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        return True, None