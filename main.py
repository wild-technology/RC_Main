#!/usr/bin/env python3
from __future__ import annotations

import sys
import logging
import argparse
import os
import inquirer

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.extract_images.extract_images import ExtractImages
from modules.georeference.georeference_images import GeoreferenceImages
from modules.image_batcher.batch_directory import BatchDirectory
from modules.realitycapture_interface.realitycapture_interface import RealityCaptureAlignment

def initialize_logger() -> logging.Logger:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    return logger

def initialize_modules(logger) -> dict[str, RCModule]:
    """
    Initializes the modules and returns a dict of the active modules.
    Honors optional environment variables:
    - RC_MODULES: comma-separated list of module names to enable (use display names as shown)
    - RC_NO_INTERACTIVE: any truthy value disables interactive prompts and enables all or RC_MODULES selection
    """
    available_modules: dict[str, RCModule] = {
        'Extract Images': ExtractImages(logger),
        'Georeference Images': GeoreferenceImages(logger),
        'Batch Directory': BatchDirectory(logger),
        'RealityCapture Alignment': RealityCaptureAlignment(logger)
    }

    # Non-interactive selection via environment
    no_interactive = os.environ.get('RC_NO_INTERACTIVE', '').strip().lower() in ('1', 'true', 'yes', 'y')
    modules_env = os.environ.get('RC_MODULES')
    if no_interactive or modules_env:
        selected = list(available_modules.keys())
        if modules_env:
            requested = [m.strip() for m in modules_env.split(',') if m.strip()]
            selected = [m for m in requested if m in available_modules]
            if not selected:
                selected = list(available_modules.keys())
        return {name: available_modules[name] for name in selected}

    # Fallback to interactive prompt
    module_choices = [
        inquirer.Checkbox(
            'modules',
            message='Select modules to enable (arrow keys to move, space to select, enter to confirm)',
            choices=list(available_modules.keys()),
            default=list(available_modules.keys()),
            carousel=True
        )
    ]

    answers = inquirer.prompt(module_choices) or {}

    enabled_modules: dict[str, RCModule] = {}
    for name, mod in available_modules.items():
        if name in answers.get('modules', []):
            enabled_modules[name] = mod

    # If user cancelled or nothing selected, default to all
    if not enabled_modules:
        enabled_modules = available_modules

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
    Honors RC_NO_INTERACTIVE env var to skip prompts and use defaults.
    """
    no_interactive = os.environ.get('RC_NO_INTERACTIVE', '').strip().lower() in ('1', 'true', 'yes', 'y')

    parser = argparse.ArgumentParser()
    for p in params.values():
        if p.get_type() is bool:
            parser.add_argument(f'-{p.cli_short}', f'--{p.cli_long}', action='store_true', help=p.get_description())
        else:
            parser.add_argument(f'-{p.cli_short}', f'--{p.cli_long}', type=p.get_type(), help=p.get_description())
    args = parser.parse_args(argv[1:])

    for p in params.values():
        val = getattr(args, p.cli_long, None)
        if val is None and p.prompt_user and not no_interactive:
            try:
                inp = input(f'{p.get_description()}: ')
                if p.get_type() is bool:
                    val = inp.lower() in ('true', 't', 'yes', 'y')
                else:
                    val = p.get_type()(inp)
            except ValueError:
                logger.warning(f'Invalid value for {p.get_name()}, using default {p.get_default_value()}')
                val = p.get_default_value()
        if val is None:
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
    logger = initialize_logger()
    modules = initialize_modules(logger)
    params = initialize_parameters(modules)
    parse_arguments(argv, params, logger)
    update_parameters(params, modules)

    logger.info("Parameters:")
    for name, p in params.items():
        logger.info(f'  {name} ({p.cli_short}): {p.get_value()}')

    # Validate output directory early
    if 'output_dir' in params:
        output_dir = params['output_dir'].get_value()
        if output_dir:
            # Try to create output directory if it doesn't exist
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                logger.error(f"Cannot create output directory {output_dir}: {e}")
                return

            # Verify it's writable
            if not os.access(output_dir, os.W_OK):
                logger.error(f"Output directory {output_dir} is not writable")
                return

            logger.info(f"Output directory validated: {output_dir}")

    overall_data: dict[str, object] = {}
    for idx, mod in enumerate(modules.values()):
        ok, msg = mod.validate_parameters()
        if not ok:
            logger.error(f"Module {mod.get_name()} validation failed: {msg}")
            return

        logger.info(f'Running module: {mod.get_name()}')
        try:
            out = mod.run()
        except Exception as e:
            logger.error(f"Module {mod.get_name()} failed with exception: {e}")
            mod.finish()
            return

        mod.finish()
        logger.info(f'Finished module: {mod.get_name()}')

        # Check for module failure
        if out is None:
            logger.error(f"Module {mod.get_name()} returned None - treating as failure")
            return

        if isinstance(out, dict) and out.get('Success') == False:
            logger.error(f"Module {mod.get_name()} reported failure")
            return

        overall_data[mod.get_name()] = out

        if not params['continue_automatically'].get_value() and idx < len(modules) - 1:
            input("Press enter to continue...")

    logger.info("Output Data:")
    log_output_data(logger, overall_data)

if __name__ == '__main__':
    main(sys.argv)
