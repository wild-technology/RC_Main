"""Progress bar widget for pipeline step execution."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class ProgressWidget(QWidget):
    """Displays a progress bar with operation name, percentage, and ETA."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._operation_label = QLabel("Idle")
        self._operation_label.setStyleSheet("font-weight: bold;")

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)

        self._eta_label = QLabel("")
        self._count_label = QLabel("")

        info_layout = QHBoxLayout()
        info_layout.addWidget(self._eta_label)
        info_layout.addStretch()
        info_layout.addWidget(self._count_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._operation_label)
        layout.addWidget(self._progress_bar)
        layout.addLayout(info_layout)

    def set_operation(self, name: str) -> None:
        """Set the current operation name."""
        self._operation_label.setText(name)

    def set_progress(self, percent: int, eta: str = "", counts: str = "") -> None:
        """Update progress bar value and info labels."""
        self._progress_bar.setValue(max(0, min(100, percent)))
        self._eta_label.setText(eta)
        self._count_label.setText(counts)

    def reset(self) -> None:
        """Reset to idle state."""
        self._operation_label.setText("Idle")
        self._progress_bar.setValue(0)
        self._eta_label.setText("")
        self._count_label.setText("")
