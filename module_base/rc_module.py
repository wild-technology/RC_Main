#!/usr/bin/env python3
from __future__ import annotations

import abc
import logging
import os
import sys
import time
from tqdm import tqdm

from module_base.parameter import Parameter

class RCModule(abc.ABC):
    """
    Base class for all ROV-processing modules.
    """

    params: dict[str, Parameter] = None
    loading_bars: list[tqdm] = None
    logger: logging.Logger

    def __init__(self, name: str, logger: logging.Logger):
        self._name = name
        self.logger = logger
        self.params = {}
        self.loading_bars = []

    @property
    def name(self) -> str:
        return self._name

    def get_name(self) -> str:
        return self._name

    def set_params(self, all_params: dict[str, Parameter]) -> None:
        """
        Injects the global Parameter dict so this module can pick out its own.
        """
        self.params = all_params

    def get_parameters(self) -> dict[str, Parameter]:
        additional_params = {}

        additional_params['rc_batched_images_dir'] = Parameter(
            name='Batched Images Root Directory',
            cli_short='rc_b',
            cli_long='rc_batched_dir',
            type=str,
            default_value=None,
            description='Root directory containing zone folders (e.g., batched_images_by_zone)',
            prompt_user=True,
            disable_when_module_active='Batch Directory'
        )

        additional_params['rc_process_zones'] = Parameter(
            name='Process Zones Sequentially',
            cli_short='rc_z',
            cli_long='rc_zones',
            type=bool,
            default_value=True,
            description='Process each zone folder sequentially with alignment',
            prompt_user=False
        )

        return additional_params

    def __align_zones_sequentially(self, batched_images_dir, output_base_dir, display_output=False):
        """
        Process each zone folder sequentially: open project, align, save, close.
        """
        if not batched_images_dir:
            raise ValueError("Batched images directory is not specified")

        if not os.path.isdir(batched_images_dir):
            raise ValueError(f"Batched images directory {batched_images_dir} is not a directory")

        if not os.path.isdir(output_base_dir):
            self.logger.info(f"Output directory does not exist. Creating: {output_base_dir}")
            os.makedirs(output_base_dir)

        this_file_dir = os.path.dirname(os.path.realpath(__file__))
        scripts_dir = os.path.join(this_file_dir, 'RC_CLI', 'Scripts')
        log_dir = os.path.join(output_base_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        # Run the sequential zone processing script
        self.__run_subprocess(
            ["cmd", "/c", "AlignZonesSequentially.bat", batched_images_dir, output_base_dir],
            scripts_dir,
            log_dir,
            display_output
        )

        # Wait for RealityCapture to finish
        self.logger.info("Waiting for RealityCapture to complete all zones...")
        while True:
            reality_capture_running = subprocess.run(
                ['tasklist', '/FI', 'IMAGENAME eq RealityCapture.exe'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )

            if 'RealityCapture.exe' not in reality_capture_running.stdout:
                break

            time.sleep(5)

        # Count completed zones
        zone_folders = [d for d in os.listdir(batched_images_dir)
                        if os.path.isdir(os.path.join(batched_images_dir, d)) and d.startswith('zone_')]

        completed_zones = 0
        for zone in zone_folders:
            zone_output = os.path.join(output_base_dir, zone)
            project_file = os.path.join(zone_output, f"{zone}.rcproj")
            if os.path.exists(project_file):
                completed_zones += 1

        return {
            'Success': True,
            'Total Zones': len(zone_folders),
            'Completed Zones': completed_zones,
            'Output Directory': output_base_dir
        }
    
    def run(self):
        success, message = self.validate_parameters()
        if not success:
            self.logger.error(message)
            return {'Success': False}

        process_zones = self.params.get('rc_process_zones')
        if process_zones and process_zones.get_value():
            # Process zones sequentially
            if 'rc_batched_images_dir' in self.params:
                batched_dir = self.params['rc_batched_images_dir'].get_value()
            else:
                batched_dir = os.path.join(self.params['output_dir'].get_value(), 'batched_images_by_zone')
            
            output_dir = os.path.join(self.params['output_dir'].get_value(), 'aligned_zones')
            
            display_output = self.params.get('rc_display_output')
            display = display_output.get_value() if display_output else False
            
            self.logger.info(f"Processing zones from: {batched_dir}")
            self.logger.info(f"Output to: {output_dir}")
            
            return self.__align_zones_sequentially(batched_dir, output_dir, display)
        
        else:
            # ... existing single folder processing code ...
            pass

    def finish(self) -> None:
        """
        Optional hook after run() completes; closes any open loading bars.
        """
        for bar in self.loading_bars:
            bar.close()
        time.sleep(0.2)

    def validate_parameters(self) -> tuple[bool, str | None]:
        # Base class has no validation - subclasses should override
        return True, None

    def _initialize_loading_bar(self, total: int, description: str) -> tqdm:
        bar = tqdm(
            total=total,
            unit="steps",
            desc=description,
            leave=True,
            miniters=1,
            file=sys.stdout,
        )
        self.loading_bars.append(bar)
        return bar

    def _update_loading_bar(self, bar: tqdm, increment: int = 1) -> None:
        bar.n = min(bar.n + increment, bar.total)
        bar.refresh()

    def _finish_loading_bar(self, bar: tqdm) -> None:
        bar.n = bar.total
        bar.refresh()

    def get_progress(self) -> float:
        if not self.loading_bars:
            return 0.0
        total = sum(bar.n / bar.total for bar in self.loading_bars)
        return total / len(self.loading_bars)
