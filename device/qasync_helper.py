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
