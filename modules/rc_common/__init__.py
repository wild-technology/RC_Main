"""Shared infrastructure for the RC pipeline.

This package provides the common building blocks used across all pipeline
modules: delegation client, status parser, progress reporting, camera
utilities, file validation, naming conventions, session state, and RC XML
parameter file management.
"""

from modules.rc_common.camera_utils import (
    detect_camera_type,
    get_camera_groups,
    get_camera_profile,
    load_camera_profiles,
)
from modules.rc_common.file_validators import (
    validate_flight_log,
    validate_image,
    validate_output_path,
    validate_rc_xml,
    validate_rov_csv,
)
from modules.rc_common.naming import generate_filename, validate_filename_convention
from modules.rc_common.progress import (
    LogBackend,
    ProgressBackend,
    ProgressEvent,
    ProgressReporter,
    SignalBackend,
    TqdmBackend,
)
from modules.rc_common.rc_delegation import RCDelegationClient
from modules.rc_common.rc_status import RCStatusParser
from modules.rc_common.rc_xml import (
    generate_rc_xml_string,
    merge_rc_xml,
    read_rc_xml,
    write_rc_xml,
)
from modules.rc_common.session import CheckpointManager, SessionState

__all__ = [
    # Status & delegation
    "RCStatusParser",
    "RCDelegationClient",
    # Progress
    "ProgressEvent",
    "ProgressBackend",
    "ProgressReporter",
    "TqdmBackend",
    "LogBackend",
    "SignalBackend",
    # Camera
    "detect_camera_type",
    "get_camera_profile",
    "get_camera_groups",
    "load_camera_profiles",
    # Validators
    "validate_flight_log",
    "validate_rov_csv",
    "validate_rc_xml",
    "validate_image",
    "validate_output_path",
    # Naming
    "generate_filename",
    "validate_filename_convention",
    # Session
    "SessionState",
    "CheckpointManager",
    # RC XML
    "read_rc_xml",
    "write_rc_xml",
    "generate_rc_xml_string",
    "merge_rc_xml",
]
