################################################################################
# qasync_helper.py
#
# This file is part of the inst_conductor software suite.
#
# It contains code necessary to make the qasync project (PyQt combined with
# asyncio) work properly.
#
# Copyright 2022 Robert S. French (rfrench@rfrench.org)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
################################################################################


import asyncio
import functools
import sys

from PyQt6.QtCore import pyqtSlot as Slot
from PyQt6.QtWidgets import QFileDialog, QMessageBox


def asyncSlotSender(*args, **kwargs):
    """Make a Qt async slot run on asyncio loop and supply sender argument.

    This function is necessary because the original @asyncSlot decorator
    does not invoke the callback with a valid sender() field. This is
    probably because the underlying PyQt library is not designed to be
    multi-threaded in this way and whatever way they have of storing
    the sender gets overwritten before the task is run."""

    def _error_handler(task):
        try:
            task.result()
        except Exception:
            sys.excepthook(*sys.exc_info())

    def outer_decorator(fn):
        @Slot(*args, **kwargs)
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            sender = args[0].sender()
            if len(args) == 1:
                task = asyncio.ensure_future(fn(args[0], sender, **kwargs))
            else:
                task = asyncio.ensure_future(fn(args[0], sender, *args[1:], **kwargs))
            task.add_done_callback(_error_handler)
            return task

        return wrapper

    return outer_decorator


# Asyncio-aware versions of standard dialog boxes
# Modified from github.com/duniter/sakia/src/sakia/gui/widgets/dialogs.py (GPL)

def dialog_async_exec(dialog):
    future = asyncio.Future()
    dialog.finished.connect(lambda r: future.set_result(r))
    dialog.open()
    return future


class QAsyncFileDialog:
    @staticmethod
    async def getSaveFileName(parent, selectedFilter=None, defaultSuffix=None, **kwargs):
        dialog = QFileDialog(parent, **kwargs)
        if selectedFilter is not None:
            dialog.selectNameFilter(selectedFilter)
        if defaultSuffix is not None:
            dialog.setDefaultSuffix(defaultSuffix)
        # Fix linux crash if not native QFileDialog is async...
        if sys.platform != 'linux':
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        result = await dialog_async_exec(dialog)
        if result == 1: # QFileDialog.AcceptMode.AcceptSave:
            return dialog.selectedFiles()
        else:
            return []

    @staticmethod
    async def getOpenFileName(parent, selectedFilter=None, **kwargs):
        dialog = QFileDialog(parent, **kwargs)
        if selectedFilter is not None:
            dialog.selectNameFilter(selectedFilter)
        # Fix linux crash if not native QFileDialog is async...
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        if sys.platform != 'linux':
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        result = await dialog_async_exec(dialog)
        if result == 1: # QFileDialog.AcceptMode.AcceptOpen:
            return dialog.selectedFiles()
        else:
            return []


class QAsyncMessageBox:
    @staticmethod
    def critical(parent, title, label, buttons=QMessageBox.StandardButton.Ok):
        dialog = QMessageBox(QMessageBox.Icon.Critical, title, label, buttons, parent)
        return dialog_async_exec(dialog)

    @staticmethod
    def information(parent, title, label, buttons=QMessageBox.StandardButton.Ok):
        dialog = QMessageBox(QMessageBox.Icon.Information, title, label, buttons, parent)
        return dialog_async_exec(dialog)

    @staticmethod
    def warning(parent, title, label, buttons=QMessageBox.StandardButton.Ok):
        dialog = QMessageBox(QMessageBox.Icon.Warning, title, label, buttons, parent)
        return dialog_async_exec(dialog)

    @staticmethod
    def question(parent, title, label, buttons=QMessageBox.StandardButton.Yes|
                                               QMessageBox.StandardButton.No):
        dialog = QMessageBox(QMessageBox.Icon.Question, title, label, buttons, parent)
        return dialog_async_exec(dialog)

    @staticmethod
    def about(parent, title, text, buttons=QMessageBox.StandardButton.Ok):
        dialog = QMessageBox(QMessageBox.Icon.NoIcon, title, text, buttons, parent)
        return dialog_async_exec(dialog)