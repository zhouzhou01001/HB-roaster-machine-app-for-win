"""Use real fonts and release Qt test windows between independent cases."""

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtWidgets


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from app.export.chart_pdf import _pdf_font_family
    _pdf_font_family()
    yield application


@pytest.fixture(autouse=True)
def release_qt_test_windows(qt_application):
    # Avoid cyclic collection re-entering Qt while a graphics item is only
    # partially constructed. Each test owns its widgets and cleanup callbacks.
    automatic_gc = gc.isenabled()
    gc.disable()
    yield
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    qt_application.processEvents()
    gc.collect()
    if automatic_gc:
        gc.enable()
