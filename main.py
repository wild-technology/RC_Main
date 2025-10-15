#!/usr/bin/env python3
from __future__ import annotations

import sys
import logging
import argparse
import inquirer

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.extract_images.extract_images import ExtractImages
from modules.georeference.georeference_images import GeoreferenceImages
from modules.image_batcher.batch_directory import BatchDirectory
from modules.realitycapture_interface.realitycapture_interface import RealityCaptureAlignment

def intialize_logger() -> logging.Logger:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    return logger

def initialize_modules(logger) -> dict[str, RCModule]:
    """
    Initializes the modules and returns a dict of the active modules.
    """
    available_modules: dict[str, RCModule] = {
        'Extract Images': ExtractImages(logger),
        'Georeference Images': GeoreferenceImages(logger),
        'Batch Directory': BatchDirectory(logger),
        'RealityCapture Alignment': RealityCaptureAlignment(logger)
    }

    module_choices = [
        inquirer.Checkbox(
            'modules',
            message='Select modules to enable (arrow keys to move, space to select, enter to confirm)',
            choices=list(available_modules.keys()),
            default=list(available_modules.keys()),
            carousel=True
        )
    ]

    answers = inquirer.prompt(module_choices)

    enabled_modules: dict[str, RCModule] = {}
    for name, mod in available_modules.items():
        if name in answers.get('modules', []):
            enabled_modules[name] = mod

    return enabled_modules

def initialize_parameters(modules) -> dict[str, Parameter]:
    """
    Initializes the parameters and returns a dict of the active parameters.
    """
    params: dict[str, Parameter] = {}

    # Global Parameters
    params['output_dir'] = Parameter(
        name='Output Directory',
        cli_short='o',
        cli_long='output_dir',
        type=str,
        default_value=None,
        description='Path to the output directory',
        prompt_user=True
    )

    params['continue_automatically'] = Parameter(
        name='Continue Automatically',
        cli_short='c',
        cli_long='continue_automatically',
        type=bool,
        default_value=False,
        description='Whether to continue automatically after each module',
        prompt_user=True
    )

    # Module-specific parameters
    for module in modules.values():
        for pname, p in module.get_parameters().items():
            disable = p.disable_when_module_active
            if disable is not None:
                if isinstance(disable, list):
                    if any(m in modules for m in disable):
                        continue
                else:
                    if disable in modules:
                        continue
            params[pname] = p

    return params

def parse_arguments(argv, params, logger) -> None:
    """
    Parses CLI args and prompts for any missing values.
    """
    parser = argparse.ArgumentParser()
    for p in params.values():
        parser.add_argument(f'-{p.cli_short}', f'--{p.cli_long}',
                            type=p.get_type(), help=p.get_description())
    args = parser.parse_args(argv[1:])

    for p in params.values():
        val = getattr(args, p.cli_long, None)
        if val is None and p.prompt_user:
            try:
                inp = input(f'{p.get_description()}: ')
                if p.get_type() is bool:
                    val = inp.lower() in ('true', 't', 'yes', 'y')
                else:
                    val = p.get_type()(inp)
            except ValueError:
                logger.warning(f'Invalid value for {p.get_name()}, using default {p.get_default_value()}')
                val = p.get_default_value()
        if val is None and not p.prompt_user:
            val = p.get_default_value()
        p.set_value(val)

def update_parameters(params, modules) -> None:
    """
    Injects the global params dict into each module.
    """
    for mod in modules.values():
        mod.set_params(params)

def log_output_data(logger, output_data: dict[str, object], indent: int = 0) -> None:
    """
    Recursively logs output data.
    """
    pad = '  ' * indent
    for key, val in output_data.items():
        if isinstance(val, dict):
            logger.info(f'{pad}{key}:')
            log_output_data(logger, val, indent + 1)
        else:
            logger.info(f'{pad}{key}: {val}')

def main(argv) -> None:
    logger = intialize_logger()
    modules = initialize_modules(logger)
    params = initialize_parameters(modules)
    parse_arguments(argv, params, logger)
    update_parameters(params, modules)

    logger.info("Parameters:")
    for name, p in params.items():
        logger.info(f'  {name} ({p.cli_short}): {p.get_value()}')

    overall_data: dict[str, object] = {}
    for idx, mod in enumerate(modules.values()):
        ok, msg = mod.validate_parameters()
        if not ok:
            logger.error(msg)
            return

        logger.info(f'Running module: {mod.get_name()}')
        out = mod.run()
        mod.finish()
        logger.info(f'Finished module: {mod.get_name()}')
        overall_data[mod.get_name()] = out or {}

        if not params['continue_automatically'].get_value() and idx < len(modules) - 1:
            input("Press enter to continue...")

    logger.info("Output Data:")
    log_output_data(logger, overall_data)

if __name__ == '__main__':
    main(sys.argv)
