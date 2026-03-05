from __future__ import annotations

from datetime import datetime
from enum import Enum, auto
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _Category(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    PROGRESS = auto()
    RAW = auto()
    RC_COMMAND = auto()


_COLORS = {
    _Category.INFO: "#FFFFFF",
    _Category.WARNING: "#FFD700",
    _Category.ERROR: "#FF4444",
    _Category.PROGRESS: "#00CED1",
    _Category.RAW: "#FFFFFF",
    _Category.RC_COMMAND: "#FFFFFF",
}


class LogViewer(QWidget):
    """Read-only log viewer with color-coded messages and category filtering."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._messages: list[tuple[_Category, str, str]] = []  # (category, html, plain)
        self._active_filter: str = "All"

        # --- filter buttons row ---
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        self._filter_buttons: dict[str, QPushButton] = {}
        for label in ("All", "Warnings", "Errors", "RC Commands"):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(label == "All")
            btn.clicked.connect(lambda checked, l=label: self._on_filter(l))
            filter_layout.addWidget(btn)
            self._filter_buttons[label] = btn
        filter_layout.addStretch()

        # --- text area ---
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Consolas", 10, QFont.Weight.Normal))
        fallback = QFont("Courier New", 10, QFont.Weight.Normal)
        if not QFont("Consolas").exactMatch():
            self._text_edit.setFont(fallback)
        self._text_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #1E1E1E; color: #FFFFFF; }"
        )

        # --- main layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filter_layout)
        layout.addWidget(self._text_edit)

    # ------------------------------------------------------------------ public

    def append_info(self, msg: str) -> None:
        self._append_colored(msg, _Category.INFO)

    def append_warning(self, msg: str) -> None:
        self._append_colored(msg, _Category.WARNING)

    def append_error(self, msg: str) -> None:
        self._append_colored(msg, _Category.ERROR)

    def append_progress(self, msg: str) -> None:
        self._append_colored(msg, _Category.PROGRESS)

    def append_raw(self, msg: str, color: Optional[str] = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        c = color or _COLORS[_Category.RAW]
        html = f'<span style="color:{c}">[{timestamp}] {_escape(msg)}</span>'
        category = _Category.RC_COMMAND if msg.strip().startswith("-") else _Category.RAW
        self._messages.append((category, html, msg))
        if self._should_show(category):
            self._text_edit.appendHtml(html)
            self._scroll_to_bottom()

    def clear_log(self) -> None:
        self._messages.clear()
        self._text_edit.clear()

    # ----------------------------------------------------------------- private

    def _append_colored(self, msg: str, category: _Category) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = _COLORS[category]
        html = f'<span style="color:{color}">[{timestamp}] {_escape(msg)}</span>'
        self._messages.append((category, html, msg))
        if self._should_show(category):
            self._text_edit.appendHtml(html)
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        sb = self._text_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_filter(self, label: str) -> None:
        self._active_filter = label
        for name, btn in self._filter_buttons.items():
            btn.setChecked(name == label)
        self._rebuild()

    def _should_show(self, category: _Category) -> bool:
        f = self._active_filter
        if f == "All":
            return True
        if f == "Warnings":
            return category == _Category.WARNING
        if f == "Errors":
            return category == _Category.ERROR
        if f == "RC Commands":
            return category == _Category.RC_COMMAND
        return True

    def _rebuild(self) -> None:
        self._text_edit.clear()
        for category, html, _ in self._messages:
            if self._should_show(category):
                self._text_edit.appendHtml(html)
        self._scroll_to_bottom()


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
