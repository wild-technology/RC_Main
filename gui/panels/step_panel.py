"""Base panel for pipeline steps with shared UI structure."""
from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.widgets.parameter_form import ParameterForm
from gui.widgets.stats_table import StatsTable

_log = logging.getLogger(__name__)


class StepPanel(QWidget):
    """Base panel for a pipeline step.

    Provides: header, description, parameter form, run button,
    statistics table, and status label.
    """
    run_requested = Signal(str)  # step_name

    def __init__(
        self,
        step_name: str,
        description: str = "",
        parameters: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.step_name = step_name
        self._build_ui(description, parameters or {})

    def _build_ui(self, description: str, parameters: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QLabel(f"<h2>{self.step_name}</h2>")
        layout.addWidget(header)

        if description:
            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: #666; margin-bottom: 8px;")
            layout.addWidget(desc_label)

        # Parameter form
        self.param_form = ParameterForm(parameters)
        layout.addWidget(self.param_form, stretch=2)

        # Run button row
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton(f"Run {self.step_name}")
        self._run_btn.setMinimumHeight(36)
        self._run_btn.clicked.connect(lambda: self.run_requested.emit(self.step_name))
        btn_row.addWidget(self._run_btn)

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.setMinimumHeight(36)
        btn_row.addWidget(self._skip_btn)

        self._status_label = QLabel("Status: Pending")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Statistics
        stats_label = QLabel("<b>Results</b>")
        layout.addWidget(stats_label)
        self.stats_table = StatsTable()
        layout.addWidget(self.stats_table, stretch=1)

    def set_status(self, status: str) -> None:
        self._status_label.setText(f"Status: {status.capitalize()}")
        colors = {"pending": "#888", "running": "#2196F3", "complete": "#4CAF50", "failed": "#F44336"}
        color = colors.get(status, "#888")
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_running(self, running: bool) -> None:
        self._run_btn.setEnabled(not running)
        self._skip_btn.setEnabled(not running)

    def set_results(self, results: dict) -> None:
        display = {k: v for k, v in results.items() if isinstance(v, (str, int, float, bool))}
        self.stats_table.set_stats(display)

    def get_form_values(self) -> dict:
        return self.param_form.get_values()
