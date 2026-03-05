"""Pipeline overview panel showing step status and quick actions."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_STATUS_COLORS = {
    "pending": "#9E9E9E",
    "running": "#2196F3",
    "complete": "#4CAF50",
    "failed": "#F44336",
}


class StepCard(QFrame):
    """Compact card showing a single pipeline step status."""

    clicked = Signal(str)  # step_name

    def __init__(self, step_name: str, step_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_name = step_name
        self._status = "pending"
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setFixedHeight(60)
        self.setCursor(Qt.CursorShape.PointingHandCursor) if hasattr(Qt, 'CursorShape') else None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self._number_label = QLabel(f"<b>{step_number}</b>")
        self._number_label.setFixedWidth(24)
        layout.addWidget(self._number_label)

        self._name_label = QLabel(step_name)
        layout.addWidget(self._name_label, stretch=1)

        self._status_label = QLabel("Pending")
        self._status_label.setStyleSheet(f"color: {_STATUS_COLORS['pending']};")
        layout.addWidget(self._status_label)

    def set_status(self, status: str) -> None:
        self._status = status
        color = _STATUS_COLORS.get(status, _STATUS_COLORS["pending"])
        self._status_label.setText(status.capitalize())
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.step_name)
        super().mousePressEvent(event)


class PipelineOverview(QWidget):
    """Overview panel showing all pipeline steps as cards."""

    step_selected = Signal(str)  # step_name
    run_all_requested = Signal()

    def __init__(self, step_names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cards: dict[str, StepCard] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>Pipeline Overview</h2>"))

        # Step cards in a grid
        grid = QGridLayout()
        for i, name in enumerate(step_names):
            card = StepCard(name, i + 1)
            card.clicked.connect(self.step_selected.emit)
            self._cards[name] = card
            row = i // 2
            col = i % 2
            grid.addWidget(card, row, col)
        layout.addLayout(grid)

        # Overall progress
        self._overall_progress = QProgressBar()
        self._overall_progress.setMaximum(len(step_names))
        self._overall_progress.setValue(0)
        layout.addWidget(QLabel("Overall Progress:"))
        layout.addWidget(self._overall_progress)

        # Run All button
        run_all_btn = QPushButton("Run All Steps")
        run_all_btn.setMinimumHeight(40)
        run_all_btn.clicked.connect(self.run_all_requested.emit)
        layout.addWidget(run_all_btn)

        layout.addStretch()

    def update_status(self, step_name: str, status: str) -> None:
        if step_name in self._cards:
            self._cards[step_name].set_status(status)
        complete = sum(1 for c in self._cards.values() if c._status == "complete")
        self._overall_progress.setValue(complete)
