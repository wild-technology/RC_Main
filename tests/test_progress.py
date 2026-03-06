"""Tests for the unified progress reporting system."""

import pytest

from modules.rc_common.progress import (
    LogBackend,
    ProgressEvent,
    ProgressReporter,
    SignalBackend,
    TqdmBackend,
)


class TestProgressEvent:
    """Tests for the ProgressEvent dataclass."""

    def test_create_event(self):
        event = ProgressEvent(
            module_name="Alignment",
            operation_name="Align Zone 1",
            progress_pct=50.0,
            elapsed_sec=120.0,
            eta_sec=120.0,
            message="Processing",
        )
        assert event.module_name == "Alignment"
        assert event.progress_pct == 50.0
        assert event.current_file is None
        assert event.file_index is None

    def test_create_event_with_file(self):
        event = ProgressEvent(
            module_name="Enhancement",
            operation_name="CLAHE",
            progress_pct=25.0,
            elapsed_sec=10.0,
            eta_sec=30.0,
            message="enhanced",
            current_file="/path/to/image.jpg",
            file_index=5,
            file_total=20,
        )
        assert event.current_file == "/path/to/image.jpg"
        assert event.file_index == 5
        assert event.file_total == 20


class TestSignalBackend:
    """Tests for the PySide6 stub backend."""

    def test_callback_called(self):
        received = []
        backend = SignalBackend(callback=lambda e: received.append(e))
        event = ProgressEvent(
            module_name="Test",
            operation_name="Op",
            progress_pct=50.0,
            elapsed_sec=1.0,
            eta_sec=1.0,
            message="test",
        )
        backend.report(event)
        assert len(received) == 1
        assert received[0].progress_pct == 50.0

    def test_no_callback(self):
        backend = SignalBackend()
        event = ProgressEvent(
            module_name="Test",
            operation_name="Op",
            progress_pct=50.0,
            elapsed_sec=1.0,
            eta_sec=1.0,
            message="test",
        )
        # Should not raise
        backend.report(event)


class TestLogBackend:
    """Tests for the logging backend."""

    def test_start_finish(self):
        backend = LogBackend()
        backend.start_operation("Test Op", 10)
        backend.update(5)
        backend.finish()
        # No assertions needed beyond not raising

    def test_report_with_file(self):
        backend = LogBackend()
        event = ProgressEvent(
            module_name="Test",
            operation_name="Process",
            progress_pct=50.0,
            elapsed_sec=10.0,
            eta_sec=10.0,
            message="ok",
            current_file="/path/to/file.jpg",
            file_index=5,
            file_total=10,
        )
        # Should not raise
        backend.report(event)


class TestProgressReporter:
    """Tests for the aggregator."""

    def test_report_fans_out(self):
        received = []
        signal_backend = SignalBackend(callback=lambda e: received.append(e))
        reporter = ProgressReporter(backends=[signal_backend])

        reporter.set_module_name("TestModule")
        reporter.start_operation("TestOp", 10)
        reporter.set_current_file("/path/to/file.jpg")
        reporter.update(1)
        reporter.report("step done")

        assert len(received) == 1
        assert received[0].module_name == "TestModule"
        assert received[0].current_file == "/path/to/file.jpg"

    def test_progress_calculation(self):
        received = []
        signal_backend = SignalBackend(callback=lambda e: received.append(e))
        reporter = ProgressReporter(backends=[signal_backend])

        reporter.start_operation("Test", 4)
        reporter.update(2)
        reporter.report()

        assert len(received) == 1
        assert received[0].progress_pct == pytest.approx(50.0)

    def test_multiple_backends(self):
        received_1 = []
        received_2 = []
        b1 = SignalBackend(callback=lambda e: received_1.append(e))
        b2 = SignalBackend(callback=lambda e: received_2.append(e))
        reporter = ProgressReporter(backends=[b1, b2])

        reporter.start_operation("Test", 1)
        reporter.report("msg")

        assert len(received_1) == 1
        assert len(received_2) == 1

    def test_finish(self):
        reporter = ProgressReporter(backends=[LogBackend()])
        reporter.start_operation("Test", 5)
        reporter.update(5)
        reporter.finish()
        # Should not raise

    def test_empty_backends(self):
        reporter = ProgressReporter(backends=[])
        reporter.start_operation("Test", 5)
        reporter.update(1)
        reporter.report("ok")
        reporter.finish()
        # Should not raise
