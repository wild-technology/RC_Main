#!/usr/bin/env python3
from __future__ import annotations

import sys
import logging
import argparse
import os
from typing import Optional, List

from module_base.parameter import Parameter
from module_base.rc_module import RCModule
from modules.extract_images.extract_images import ExtractImages
from modules.georeference.georeference_images import GeoreferenceImages
from modules.image_batcher.batch_directory import BatchDirectory
from modules.realitycapture_interface.realitycapture_interface import RealityCaptureAlignment
from modules.image_enhancement.image_enhancement import ImageEnhancement
from modules.camera_setup.camera_setup import CameraSetup
from modules.component_export.component_export import ComponentExportModule as ComponentExport
from modules.model_generation.model_generation import ModelGeneration
from modules.prepare_model.prepare_model import PrepareModel
from modules.model_export.model_export import ModelExport
from modules.rc_common.session import SessionState
from modules.rc_common.progress import ProgressReporter, TqdmBackend, LogBackend

RC_TRACK_MODULES = {
    'Camera Setup', 'RealityCapture Alignment', 'Export Components',
    'Model Generation', 'Prepare Model', 'Model Export',
}

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
        # ── Preparation ──
        'Extract Images': ExtractImages(logger),
        'Enhance Images': ImageEnhancement(logger),
        'Georeference Images': GeoreferenceImages(logger),
        'Batch Directory': BatchDirectory(logger),
        # ── RC Alignment & Model ──
        'Camera Setup': CameraSetup(logger),
        'RealityCapture Alignment': RealityCaptureAlignment(logger),
        'Export Components': ComponentExport(logger),
        'Model Generation': ModelGeneration(logger),
        'Prepare Model': PrepareModel(logger),
        'Model Export': ModelExport(logger),
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
    try:
        import inquirer
    except ImportError:
        logger.warning("inquirer not installed — enabling all modules. "
                       "Install with: pip install inquirer")
        return available_modules

    module_choices = [
        inquirer.Checkbox(
            'modules',
            message='Select modules to enable (arrow keys to move, space to select, enter to confirm)',
            choices=[
                '── Preparation ──',
                'Extract Images',
                'Enhance Images',
                'Georeference Images',
                'Batch Directory',
                '── RC Alignment & Model ──',
                'Camera Setup',
                'RealityCapture Alignment',
                'Export Components',
                'Model Generation',
                'Prepare Model',
                'Model Export',
            ],
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
    params['expedition_name'] = Parameter(
        name='Expedition Name',
        cli_short='exp',
        cli_long='expedition_name',
        type=str,
        default_value=None,
        description='Expedition identifier (e.g., NA168)',
        prompt_user=True
    )

    params['dive_name'] = Parameter(
        name='Dive Name',
        cli_short='dive',
        cli_long='dive_name',
        type=str,
        default_value=None,
        description='Dive identifier (e.g., H2080)',
        prompt_user=True
    )

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

    # Shared RC parameters (used by Camera Setup, Alignment, Component Export, Model Generation)
    params['rc_executable_path'] = Parameter(
        name='RealityScan Executable Path',
        cli_short='rc_exe',
        cli_long='rc_executable_path',
        type=str,
        default_value=None,
        description='Path to RealityScan.exe (auto-detected if not set)',
        parameter_group='RealityCapture',
        file_filter='*.exe',
    )

    params['rc_instance_name'] = Parameter(
        name='RC Instance Name',
        cli_short='rc_inst',
        cli_long='rc_instance_name',
        type=str,
        default_value='*',
        description='RealityScan instance name for delegation (* = any)',
        parameter_group='RealityCapture',
    )

    params['camera_profiles_path'] = Parameter(
        name='Camera Profiles Path',
        cli_short='cam_prof',
        cli_long='camera_profiles_path',
        type=str,
        default_value='config/camera_profiles.json',
        description='Path to camera profiles JSON file',
        parameter_group='RealityCapture',
        file_filter='*.json',
    )

    params['session_file'] = Parameter(
        name='Session File',
        cli_short='sess',
        cli_long='session_file',
        type=str,
        default_value=None,
        description='Path to session file for save/resume (auto-generated if not set)',
        parameter_group='General',
    )

    params['rc_checkpoint_dir'] = Parameter(
        name='Checkpoint Directory',
        cli_short='ckpt',
        cli_long='rc_checkpoint_dir',
        type=str,
        default_value=None,
        description='Directory for operation checkpoints (default: output_dir/.checkpoints)',
        parameter_group='General',
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

    # Auto-disable batch_input_image_dir prompt when both Georeference and Batch are active
    if 'Georeference Images' in modules and 'Batch Directory' in modules:
        if 'batch_input_image_dir' in params:
            params['batch_input_image_dir'].prompt_user = False

    return params

def parse_arguments(argv, params, logger) -> None:
    """
    Parses CLI args and prompts for any missing values.
    Honors RC_NO_INTERACTIVE env var to skip prompts and use defaults.
    Also skips RealityCapture model-related prompts when model generation is disabled.
    """
    no_interactive = os.environ.get('RC_NO_INTERACTIVE', '').strip().lower() in ('1', 'true', 'yes', 'y')

    parser = argparse.ArgumentParser()
    for p in params.values():
        if p.get_type() is bool:
            parser.add_argument(f'-{p.cli_short}', f'--{p.cli_long}', action='store_true', default=None, help=p.get_description())
        else:
            parser.add_argument(f'-{p.cli_short}', f'--{p.cli_long}', type=p.get_type(), help=p.get_description())
    args = parser.parse_args(argv[1:])

    # Track whether RC model generation is disabled to skip dependent prompts
    rc_model_generate_value = None

    for p in params.values():
        val = getattr(args, p.cli_long, None)

        # If we've already determined rc_model_generate is False, skip prompts for its dependents
        if rc_model_generate_value is False and p.cli_long in ('r_model_cull_poly', 'r_model_texture', 'r_model_simplify'):
            # Do not prompt; force False to avoid confusion
            val = False if val is None else val
        elif val is None and p.prompt_user and not no_interactive:
            try:
                inp = input(f'{p.get_description()}: ').strip()
                # Strip surrounding quotes if present
                if inp and inp[0] in ('"', "'") and inp[-1] == inp[0]:
                    inp = inp[1:-1]
                if not inp:
                    val = p.get_default_value()
                elif p.get_type() is bool:
                    val = inp.lower() in ('true', 't', 'yes', 'y')
                else:
                    val = p.get_type()(inp)
            except ValueError:
                logger.warning(f'Invalid value for {p.get_name()}, using default {p.get_default_value()}')
                val = p.get_default_value()
        if val is None:
            val = p.get_default_value()
        p.set_value(val)

        # Capture rc_model_generate choice as soon as it's set
        if p.cli_long == 'r_model_generate':
            rc_model_generate_value = bool(val)

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

def _resolve_session_path(params: dict[str, Parameter]) -> str | None:
    """Determine the session file path from params or auto-generate."""
    session_file = params.get('session_file')
    if session_file and session_file.get_value():
        return session_file.get_value()

    output_dir = params.get('output_dir')
    if output_dir and output_dir.get_value():
        expedition = params.get('expedition_name')
        dive = params.get('dive_name')
        exp_str = expedition.get_value() if expedition and expedition.get_value() else "session"
        dive_str = dive.get_value() if dive and dive.get_value() else ""
        parts = [exp_str]
        if dive_str:
            parts.append(dive_str)
        name = "_".join(parts)
        return os.path.join(output_dir.get_value(), f"{name}_session.json")

    return None


def _load_or_create_session(params: dict[str, Parameter], logger: logging.Logger) -> SessionState:
    """Load existing session or create a new one from params."""
    session = SessionState()

    session_path = _resolve_session_path(params)
    if session_path and os.path.exists(session_path):
        try:
            session.load(session_path)
            logger.info("Resumed session from %s (completed: %s)", session_path, session.completed_steps)
            return session
        except Exception as e:
            logger.warning("Could not load session %s: %s — starting fresh", session_path, e)

    # Populate from current params
    exp = params.get('expedition_name')
    dive = params.get('dive_name')
    session.expedition = exp.get_value() if exp and exp.get_value() else ""
    session.dive = dive.get_value() if dive and dive.get_value() else ""

    # Serialize param values for session
    for name, p in params.items():
        val = p.get_value()
        if val is not None:
            session.parameters[name] = str(val) if not isinstance(val, (int, float, bool)) else val

    return session


def _save_session(session: SessionState, params: dict[str, Parameter], logger: logging.Logger) -> None:
    """Save session state to file."""
    session_path = _resolve_session_path(params)
    if session_path:
        try:
            session.save(session_path)
        except Exception as e:
            logger.warning("Could not save session: %s", e)


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
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as e:
                logger.error(f"Cannot create output directory {output_dir}: {e}")
                return

            if not os.access(output_dir, os.W_OK):
                logger.error(f"Output directory {output_dir} is not writable")
                return

            logger.info(f"Output directory validated: {output_dir}")

    # Initialize session state
    session = _load_or_create_session(params, logger)

    # Set up progress reporter with tqdm + log backends
    progress_backends = [TqdmBackend()]
    output_dir_val = params.get('output_dir')
    if output_dir_val and output_dir_val.get_value():
        log_dir = os.path.join(output_dir_val.get_value(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        progress_backends.append(LogBackend(logging.getLogger("progress")))
    progress_reporter = ProgressReporter(progress_backends)

    # Inject session + progress into modules
    for mod in modules.values():
        if hasattr(mod, 'set_session_state'):
            mod.set_session_state(session)
        if hasattr(mod, 'set_progress_reporter'):
            mod.set_progress_reporter(progress_reporter)

    overall_data: dict[str, object] = {}
    module_names = list(modules.keys())

    rc_startup_done = False
    for idx, (name, mod) in enumerate(modules.items()):
        # RC startup guard — run once before first RC-track module
        if name in RC_TRACK_MODULES and not rc_startup_done:
            if hasattr(mod, '_rc_startup_check'):
                if not mod._rc_startup_check():
                    logger.error("RC startup check failed. Aborting RC track.")
                    break
            rc_startup_done = True

        # Skip already-completed steps if resuming session
        if session.is_step_complete(name):
            logger.info(f"Skipping completed step: {name}")
            prev_output = session.get_step_output(name)
            if prev_output:
                overall_data[name] = prev_output
            continue

        ok, msg = mod.validate_parameters()
        if not ok:
            logger.error(f"Module {mod.get_name()} validation failed: {msg}")
            _save_session(session, params, logger)
            return

        session.set_current_step(name)
        logger.info(f'Running module: {mod.get_name()}')

        try:
            out = mod.run()
        except Exception as e:
            logger.error(f"Module {mod.get_name()} failed with exception: {e}")
            mod.finish()
            _save_session(session, params, logger)
            return

        mod.finish()
        logger.info(f'Finished module: {mod.get_name()}')

        # Check for module failure
        if out is None:
            logger.error(f"Module {mod.get_name()} returned None - treating as failure")
            _save_session(session, params, logger)
            return

        if isinstance(out, dict) and out.get('Success') is False:
            logger.error(f"Module {mod.get_name()} reported failure")
            _save_session(session, params, logger)
            return

        overall_data[name] = out

        # Serialize output for session (filter non-serializable values)
        serializable_out = {}
        if isinstance(out, dict):
            for k, v in out.items():
                if isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    serializable_out[k] = v
                else:
                    serializable_out[k] = str(v)
        session.mark_step_complete(name, serializable_out)
        _save_session(session, params, logger)

        no_interactive = os.environ.get('RC_NO_INTERACTIVE', '').strip().lower() in ('1', 'true', 'yes', 'y')
        if not no_interactive and not params['continue_automatically'].get_value() and idx < len(module_names) - 1:
            input("Press enter to continue...")

    logger.info("Output Data:")
    log_output_data(logger, overall_data)
    logger.info("Pipeline complete.")

if __name__ == '__main__':
    main(sys.argv)