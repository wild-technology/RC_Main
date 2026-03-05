"""Tests for GUI panel modules (syntax and structure)."""

import ast
from pathlib import Path

import pytest

GUI_DIR = Path(__file__).parent.parent / "gui"
PANELS_DIR = GUI_DIR / "panels"


class TestPanelSyntax:
    """Verify all panel files parse without errors."""

    @pytest.fixture(params=[
        "step_panel.py",
        "expedition_dialog.py",
        "pipeline_overview.py",
    ])
    def panel_file(self, request):
        return PANELS_DIR / request.param

    def test_file_exists(self, panel_file):
        assert panel_file.exists()

    def test_parses_cleanly(self, panel_file):
        source = panel_file.read_text()
        ast.parse(source, filename=str(panel_file))


class TestPanelClasses:
    """Verify expected classes exist in each panel module."""

    def _get_class_names(self, filepath: Path) -> list[str]:
        source = filepath.read_text()
        tree = ast.parse(source)
        return [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]

    def test_step_panel_has_class(self):
        classes = self._get_class_names(PANELS_DIR / "step_panel.py")
        assert "StepPanel" in classes

    def test_expedition_dialog_has_class(self):
        classes = self._get_class_names(PANELS_DIR / "expedition_dialog.py")
        assert "ExpeditionDialog" in classes

    def test_pipeline_overview_has_classes(self):
        classes = self._get_class_names(PANELS_DIR / "pipeline_overview.py")
        assert "PipelineOverview" in classes
        assert "StepCard" in classes


class TestStatsTableAliases:
    """Verify StatsTable has both set_data/set_stats and clear_data/clear_stats."""

    def test_has_set_stats_alias(self):
        source = (GUI_DIR / "widgets" / "stats_table.py").read_text()
        assert "set_stats" in source

    def test_has_clear_stats_alias(self):
        source = (GUI_DIR / "widgets" / "stats_table.py").read_text()
        assert "clear_stats" in source


class TestFullGUIStructure:
    """Verify complete GUI package structure."""

    @pytest.fixture(params=[
        "gui/__init__.py",
        "gui/app.py",
        "gui/main_window.py",
        "gui/widgets/__init__.py",
        "gui/widgets/log_viewer.py",
        "gui/widgets/progress_widget.py",
        "gui/widgets/parameter_form.py",
        "gui/widgets/stats_table.py",
        "gui/panels/__init__.py",
        "gui/panels/step_panel.py",
        "gui/panels/expedition_dialog.py",
        "gui/panels/pipeline_overview.py",
        "gui/workers/__init__.py",
        "gui/workers/pipeline_worker.py",
        "gui/workers/rc_process.py",
        "gui/state/__init__.py",
        "gui/state/metadata_db.py",
        "gui/state/session_manager.py",
    ])
    def gui_file(self, request):
        return Path(__file__).parent.parent / request.param

    def test_gui_file_exists(self, gui_file):
        assert gui_file.exists(), f"Missing: {gui_file}"

    def test_gui_file_parses(self, gui_file):
        source = gui_file.read_text()
        if source.strip():  # Skip empty __init__.py files
            ast.parse(source, filename=str(gui_file))
