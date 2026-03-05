"""Main window for the RC Pipeline PySide6 GUI."""
from __future__ import annotations

import importlib
import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from gui.widgets import LogViewer, ParameterForm, ProgressWidget, StatsTable
from modules.rc_common.session import SessionState

_logger = logging.getLogger("gui")

PIPELINE_STEPS = [
    "Extract Images",
    "Enhance Images",
    "Georeference Images",
    "Batch Directory",
    "Camera Setup",
    "RealityCapture Alignment",
    "Component Export",
    "Model Generation",
]

MODULE_MAP = {
    "Extract Images": ("modules.extract_images.extract_images", "ExtractImages"),
    "Enhance Images": ("modules.image_enhancement.image_enhancement", "ImageEnhancement"),
    "Georeference Images": ("modules.georeference.georeference_images", "GeoreferenceImages"),
    "Batch Directory": ("modules.image_batcher.batch_directory", "BatchDirectory"),
    "Camera Setup": ("modules.camera_setup.camera_setup", "CameraSetup"),
    "RealityCapture Alignment": (
        "modules.realitycapture_interface.realitycapture_interface",
        "RealityCaptureAlignment",
    ),
    "Component Export": ("modules.component_export.component_export", "ComponentExportModule"),
    "Model Generation": ("modules.model_generation.model_generation", "ModelGeneration"),
}

_STATUS_ICONS = {
    "pending": "\u25CB",   # ○
    "running": "\u25C9",   # ◉
    "complete": "\u2713",  # ✓
    "failed": "\u2717",    # ✗
}


def _load_module_parameters(step_name: str) -> list:
    """Dynamically load a module class and return its parameters.

    Returns an empty list if the module cannot be imported.
    """
    if step_name not in MODULE_MAP:
        return []
    module_path, class_name = MODULE_MAP[step_name]
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls(_logger)
        return instance.get_parameters()
    except (ImportError, AttributeError, TypeError, Exception) as exc:
        _logger.warning("Could not load parameters for '%s': %s", step_name, exc)
        return []


class MainWindow(QMainWindow):
    """Primary application window for the RC Pipeline GUI."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("RC Pipeline - Photogrammetry Processing")
        self.setMinimumSize(1200, 800)

        # --- internal state ---
        self._session = SessionState()
        self._step_status: dict[str, str] = {step: "pending" for step in PIPELINE_STEPS}

        # --- build UI ---
        self._build_menu_bar()
        self._build_toolbar()
        self._build_central_widget()
        self._build_status_bar()

        # Select the first step by default
        if self._step_list.count() > 0:
            self._step_list.setCurrentRow(0)

    # ------------------------------------------------------------------ menu

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        # File menu
        file_menu = menu_bar.addMenu("&File")

        self._action_new = QAction("&New Session", self)
        self._action_new.setShortcut("Ctrl+N")
        self._action_new.triggered.connect(self._new_session)
        file_menu.addAction(self._action_new)

        self._action_open = QAction("&Open Session", self)
        self._action_open.setShortcut("Ctrl+O")
        self._action_open.triggered.connect(self._open_session)
        file_menu.addAction(self._action_open)

        self._action_save = QAction("&Save Session", self)
        self._action_save.setShortcut("Ctrl+S")
        self._action_save.triggered.connect(self._save_session)
        file_menu.addAction(self._action_save)

        file_menu.addSeparator()

        action_exit = QAction("E&xit", self)
        action_exit.setShortcut("Ctrl+Q")
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        # Tools menu
        tools_menu = menu_bar.addMenu("&Tools")

        self._action_run_step = QAction("&Run Selected Step", self)
        self._action_run_step.triggered.connect(self._run_selected_step)
        tools_menu.addAction(self._action_run_step)

        self._action_run_all = QAction("Run &All", self)
        self._action_run_all.triggered.connect(self._run_all)
        tools_menu.addAction(self._action_run_all)

        # Help menu
        help_menu = menu_bar.addMenu("&Help")

        action_about = QAction("&About", self)
        action_about.triggered.connect(self._show_about)
        help_menu.addAction(action_about)

    # ---------------------------------------------------------------- toolbar

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self._action_new)
        toolbar.addAction(self._action_open)
        toolbar.addAction(self._action_save)
        toolbar.addSeparator()
        toolbar.addAction(self._action_run_step)
        toolbar.addAction(self._action_run_all)

        self._action_stop = QAction("Stop", self)
        self._action_stop.setEnabled(False)
        self._action_stop.triggered.connect(self._stop_execution)
        toolbar.addAction(self._action_stop)

    # --------------------------------------------------------- central widget

    def _build_central_widget(self) -> None:
        # --- left panel: step list ---
        self._step_list = QListWidget()
        self._step_list.setFixedWidth(260)
        for step_name in PIPELINE_STEPS:
            icon = _STATUS_ICONS["pending"]
            item = QListWidgetItem(f"{icon}  {step_name}")
            self._step_list.addItem(item)
        self._step_list.currentRowChanged.connect(self._on_step_selected)

        # --- right panel: stacked parameter/stats panels ---
        self._panel_stack = QStackedWidget()
        self._step_panels: dict[str, QWidget] = {}
        self._param_forms: dict[str, ParameterForm] = {}
        self._stats_tables: dict[str, StatsTable] = {}

        for step_name in PIPELINE_STEPS:
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(8, 8, 8, 8)

            header = QLabel(f"Panel for: {step_name}")
            header.setStyleSheet("font-size: 16px; font-weight: bold; margin-bottom: 8px;")
            layout.addWidget(header)

            # Parameter form - load from actual module
            params = _load_module_parameters(step_name)
            param_form = ParameterForm(parameters=params)
            self._param_forms[step_name] = param_form
            layout.addWidget(param_form, stretch=1)

            # Stats table
            stats = StatsTable()
            self._stats_tables[step_name] = stats
            layout.addWidget(stats)

            self._step_panels[step_name] = panel
            self._panel_stack.addWidget(panel)

        # --- top area: left list + right panels ---
        top_splitter = QSplitter(Qt.Orientation.Horizontal)
        top_splitter.addWidget(self._step_list)
        top_splitter.addWidget(self._panel_stack)
        top_splitter.setStretchFactor(0, 0)
        top_splitter.setStretchFactor(1, 1)

        # --- bottom panel: log viewer (collapsible) ---
        self._log_viewer = LogViewer()

        main_splitter = QSplitter(Qt.Orientation.Vertical)
        main_splitter.addWidget(top_splitter)
        main_splitter.addWidget(self._log_viewer)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)

        self.setCentralWidget(main_splitter)

    # ------------------------------------------------------------ status bar

    def _build_status_bar(self) -> None:
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._status_expedition = QLabel("Expedition: —")
        self._status_dive = QLabel("Dive: —")
        self._status_step = QLabel("Step: —")
        self._status_count = QLabel("0 / 8 complete")

        self._status_bar.addWidget(self._status_expedition)
        self._status_bar.addWidget(self._status_dive)
        self._status_bar.addWidget(self._status_step)
        self._status_bar.addPermanentWidget(self._status_count)

    def _refresh_status_bar(self) -> None:
        exp = self._session.expedition or "—"
        dive = self._session.dive or "—"
        self._status_expedition.setText(f"Expedition: {exp}")
        self._status_dive.setText(f"Dive: {dive}")

        current = self._session.current_step or "—"
        self._status_step.setText(f"Step: {current}")

        done = sum(1 for s in self._step_status.values() if s == "complete")
        self._status_count.setText(f"{done} / {len(PIPELINE_STEPS)} complete")

    # ----------------------------------------------------------- step events

    def _on_step_selected(self, row: int) -> None:
        """Switch the right panel to match the selected step."""
        if 0 <= row < len(PIPELINE_STEPS):
            self._panel_stack.setCurrentIndex(row)
            step_name = PIPELINE_STEPS[row]
            self._session.set_current_step(step_name)
            self._refresh_status_bar()

    def _update_step_status(self, step_name: str, status: str) -> None:
        """Update the status icon for a pipeline step.

        Args:
            step_name: Name of the step (must be in PIPELINE_STEPS).
            status: One of 'pending', 'running', 'complete', 'failed'.
        """
        if step_name not in self._step_status:
            return
        self._step_status[step_name] = status
        icon = _STATUS_ICONS.get(status, _STATUS_ICONS["pending"])

        idx = PIPELINE_STEPS.index(step_name)
        item = self._step_list.item(idx)
        if item is not None:
            item.setText(f"{icon}  {step_name}")

        self._refresh_status_bar()

    # --------------------------------------------------------- file actions

    def _new_session(self) -> None:
        """Reset to a fresh session."""
        self._session = SessionState()
        self._step_status = {step: "pending" for step in PIPELINE_STEPS}
        for step_name in PIPELINE_STEPS:
            self._update_step_status(step_name, "pending")
            self._stats_tables[step_name].clear_data()
        self._log_viewer.clear_log()
        self._log_viewer.append_info("New session created.")
        self._refresh_status_bar()

    def _open_session(self) -> None:
        """Open a session JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Session", "", "Session Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            self._session.load(path)
            # Restore step statuses from session
            for step in PIPELINE_STEPS:
                if self._session.is_step_complete(step):
                    self._update_step_status(step, "complete")
                else:
                    self._update_step_status(step, "pending")
            self._log_viewer.append_info(f"Session loaded from {path}")
            self._refresh_status_bar()
        except Exception as exc:
            self._log_viewer.append_error(f"Failed to load session: {exc}")
            QMessageBox.critical(self, "Error", f"Could not load session:\n{exc}")

    def _save_session(self) -> None:
        """Save the current session to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session", "", "Session Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            self._session.save(path)
            self._log_viewer.append_info(f"Session saved to {path}")
        except Exception as exc:
            self._log_viewer.append_error(f"Failed to save session: {exc}")
            QMessageBox.critical(self, "Error", f"Could not save session:\n{exc}")

    # ----------------------------------------------------------- run actions

    def _run_selected_step(self) -> None:
        """Run the currently selected pipeline step (not yet implemented)."""
        self._log_viewer.append_warning("Not implemented yet")

    def _run_all(self) -> None:
        """Run all pipeline steps sequentially (not yet implemented)."""
        self._log_viewer.append_warning("Not implemented yet")

    def _stop_execution(self) -> None:
        """Stop the currently running operation (not yet implemented)."""
        self._log_viewer.append_warning("Not implemented yet")

    # ------------------------------------------------------------- help

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About RC Pipeline",
            "RC Pipeline - Photogrammetry Processing\n\n"
            "ROV underwater photogrammetry pipeline GUI.\n"
            "Orchestrates image processing through RealityScan.",
        )
