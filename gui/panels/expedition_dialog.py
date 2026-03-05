"""Dialog for managing expeditions and dives."""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.state.metadata_db import MetadataDB

_log = logging.getLogger(__name__)


class ExpeditionDialog(QDialog):
    """Dialog for selecting or creating expedition/dive pairs."""

    def __init__(
        self,
        db: MetadataDB | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Expedition & Dive Selection")
        self.setMinimumSize(500, 400)
        self._db = db
        self._selected_expedition = ""
        self._selected_dive = ""
        self._build_ui()
        self._refresh_lists()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Existing expeditions
        existing_group = QGroupBox("Existing Expeditions")
        existing_layout = QVBoxLayout(existing_group)

        self._expedition_list = QListWidget()
        self._expedition_list.currentTextChanged.connect(self._on_expedition_selected)
        existing_layout.addWidget(QLabel("Expeditions:"))
        existing_layout.addWidget(self._expedition_list)

        self._dive_list = QListWidget()
        existing_layout.addWidget(QLabel("Dives:"))
        existing_layout.addWidget(self._dive_list)

        layout.addWidget(existing_group)

        # New expedition/dive
        new_group = QGroupBox("Create New")
        form = QFormLayout(new_group)

        self._expedition_input = QLineEdit()
        self._expedition_input.setPlaceholderText("e.g., NA173")
        form.addRow("Expedition:", self._expedition_input)

        self._dive_input = QLineEdit()
        self._dive_input.setPlaceholderText("e.g., H2102")
        form.addRow("Dive:", self._dive_input)

        layout.addWidget(new_group)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_lists(self) -> None:
        self._expedition_list.clear()
        if self._db:
            for exp in self._db.list_expeditions():
                self._expedition_list.addItem(exp["name"])

    def _on_expedition_selected(self, name: str) -> None:
        self._dive_list.clear()
        self._expedition_input.setText(name)
        if self._db and name:
            for dive in self._db.list_dives(name):
                self._dive_list.addItem(dive["name"])

    def _on_accept(self) -> None:
        exp = self._expedition_input.text().strip()
        dive = self._dive_input.text().strip()

        if not exp:
            QMessageBox.warning(self, "Missing Input", "Expedition name is required.")
            return
        if not dive:
            QMessageBox.warning(self, "Missing Input", "Dive name is required.")
            return

        self._selected_expedition = exp
        self._selected_dive = dive

        # Add to DB if available
        if self._db:
            self._db.add_dive(exp, dive)

        self.accept()

    @property
    def expedition(self) -> str:
        return self._selected_expedition

    @property
    def dive(self) -> str:
        return self._selected_dive
