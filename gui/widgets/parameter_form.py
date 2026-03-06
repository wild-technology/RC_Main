"""Dynamic parameter form widget built from Parameter objects."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ParameterForm(QWidget):
    """Renders a list of Parameter objects as an editable form.

    Parameters are grouped by parameter_group and rendered with
    appropriate input widgets based on their type and constraints.
    """

    parameters_changed = Signal()

    def __init__(self, parameters: list | None = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._parameters: list = parameters or []
        self._widgets: dict[str, QWidget] = {}

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._form_container = QWidget()
        self._form_layout = QVBoxLayout(self._form_container)
        self._form_layout.setContentsMargins(4, 4, 4, 4)

        self._scroll.setWidget(self._form_container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

        if self._parameters:
            self._build_form()

    def set_parameters(self, parameters: list) -> None:
        """Replace current parameters and rebuild the form."""
        self._parameters = parameters
        self._widgets.clear()
        # Clear existing layout
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._build_form()

    def get_values(self) -> dict[str, object]:
        """Return current parameter values as {name: value}."""
        values = {}
        for param in self._parameters:
            widget = self._widgets.get(param.name)
            if widget is None:
                continue
            if isinstance(widget, QCheckBox):
                values[param.name] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                values[param.name] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                values[param.name] = widget.value()
            elif isinstance(widget, QComboBox):
                values[param.name] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                values[param.name] = widget.text()
        return values

    def _build_form(self) -> None:
        """Build form widgets grouped by parameter_group."""
        groups: dict[str, list] = {}
        for param in self._parameters:
            group_name = param.parameter_group or "General"
            groups.setdefault(group_name, []).append(param)

        for group_name, params in groups.items():
            group_box = QGroupBox(group_name)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

            for param in params:
                widget = self._create_widget(param)
                if widget is not None:
                    self._widgets[param.name] = widget
                    label_text = param.name.replace("_", " ").title()
                    if param.description:
                        label_text += f"  ({param.description})"
                    form.addRow(label_text + ":", widget)

            group_box.setLayout(form)
            self._form_layout.addWidget(group_box)

        self._form_layout.addStretch()

    def _create_widget(self, param) -> QWidget | None:
        """Create the appropriate input widget for a parameter."""
        if param.choices:
            combo = QComboBox()
            combo.addItems([str(c) for c in param.choices])
            if param.value is not None:
                idx = combo.findText(str(param.value))
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentTextChanged.connect(lambda: self.parameters_changed.emit())
            return combo

        if param.type is bool:
            cb = QCheckBox()
            cb.setChecked(bool(param.value))
            cb.stateChanged.connect(lambda: self.parameters_changed.emit())
            return cb

        if param.type is int:
            spin = QSpinBox()
            spin.setRange(
                int(param.min_value) if param.min_value is not None else -999999,
                int(param.max_value) if param.max_value is not None else 999999,
            )
            if param.value is not None:
                spin.setValue(int(param.value))
            spin.valueChanged.connect(lambda: self.parameters_changed.emit())
            return spin

        if param.type is float:
            dspin = QDoubleSpinBox()
            dspin.setDecimals(4)
            dspin.setRange(
                float(param.min_value) if param.min_value is not None else -999999.0,
                float(param.max_value) if param.max_value is not None else 999999.0,
            )
            if param.value is not None:
                dspin.setValue(float(param.value))
            dspin.valueChanged.connect(lambda: self.parameters_changed.emit())
            return dspin

        # Default: string / path input
        if param.file_filter:
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            le = QLineEdit(str(param.value) if param.value else "")
            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(
                lambda checked, line=le, ff=param.file_filter: self._browse(line, ff)
            )
            h.addWidget(le, stretch=1)
            h.addWidget(browse_btn)
            le.textChanged.connect(lambda: self.parameters_changed.emit())
            # Store the line edit as the tracked widget
            self._widgets[param.name] = le
            return container

        le = QLineEdit(str(param.value) if param.value else "")
        le.textChanged.connect(lambda: self.parameters_changed.emit())
        return le

    def _browse(self, line_edit: QLineEdit, file_filter: str) -> None:
        """Open a file dialog and set the result in the line edit."""
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", file_filter)
        if path:
            line_edit.setText(path)
