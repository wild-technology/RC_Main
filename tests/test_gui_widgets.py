"""Tests for GUI widget modules (syntax and structure only).

PySide6 requires display libraries not available in this headless Linux
environment, so these tests verify code structure via AST parsing rather
than runtime widget instantiation.
"""

import ast
import importlib
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).parent.parent / "gui"
WIDGET_DIR = GUI_DIR / "widgets"


class TestWidgetSyntax:
    """Verify all widget files parse without syntax errors."""

    @pytest.fixture(params=[
        "log_viewer.py",
        "progress_widget.py",
        "parameter_form.py",
        "stats_table.py",
    ])
    def widget_file(self, request):
        return WIDGET_DIR / request.param

    def test_file_exists(self, widget_file):
        assert widget_file.exists(), f"Missing: {widget_file}"

    def test_parses_cleanly(self, widget_file):
        source = widget_file.read_text()
        tree = ast.parse(source, filename=str(widget_file))
        assert tree is not None


class TestWidgetClasses:
    """Verify expected classes exist in each widget module."""

    def _get_class_names(self, filepath: Path) -> list[str]:
        source = filepath.read_text()
        tree = ast.parse(source)
        return [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    def test_log_viewer_has_class(self):
        classes = self._get_class_names(WIDGET_DIR / "log_viewer.py")
        assert "LogViewer" in classes

    def test_progress_widget_has_class(self):
        classes = self._get_class_names(WIDGET_DIR / "progress_widget.py")
        assert "ProgressWidget" in classes

    def test_parameter_form_has_class(self):
        classes = self._get_class_names(WIDGET_DIR / "parameter_form.py")
        assert "ParameterForm" in classes

    def test_stats_table_has_class(self):
        classes = self._get_class_names(WIDGET_DIR / "stats_table.py")
        assert "StatsTable" in classes


class TestMainWindowStructure:
    """Verify main window and app files exist and parse."""

    def test_app_exists(self):
        assert (GUI_DIR / "app.py").exists()

    def test_app_parses(self):
        source = (GUI_DIR / "app.py").read_text()
        ast.parse(source)

    def test_main_window_exists(self):
        assert (GUI_DIR / "main_window.py").exists()

    def test_main_window_parses(self):
        source = (GUI_DIR / "main_window.py").read_text()
        ast.parse(source)

    def test_main_window_has_class(self):
        source = (GUI_DIR / "main_window.py").read_text()
        tree = ast.parse(source)
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "MainWindow" in classes


class TestDirectoryStructure:
    """Verify GUI package structure."""

    def test_gui_init(self):
        assert (GUI_DIR / "__init__.py").exists()

    def test_widgets_init(self):
        assert (WIDGET_DIR / "__init__.py").exists()

    def test_panels_init(self):
        assert (GUI_DIR / "panels" / "__init__.py").exists()

    def test_workers_init(self):
        assert (GUI_DIR / "workers" / "__init__.py").exists()

    def test_state_init(self):
        assert (GUI_DIR / "state" / "__init__.py").exists()
