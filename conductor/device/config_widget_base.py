################################################################################
# conductor/device/config_widget_base.py
#
# This file is part of the inst_conductor software suite.
#
# It contains the parent class for all instrument configuration widgets to
# provide utility functions and a consistent look and feel.
#
# Copyright 2023 Robert S. French (rfrench@rfrench.org)
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

from PyQt6.QtWidgets import (QAbstractSpinBox,
                             QApplication,
                             QDialog,
                             QDialogButtonBox,
                             QDoubleSpinBox,
                             QFileDialog,
                             QLayout,
                             QMenuBar,
                             QPlainTextEdit,
                             QPushButton,
                             QStatusBar,
                             QStyledItemDelegate,
                             QVBoxLayout,
                             QWidget)
from PyQt6.QtCore import Qt, QAbstractTableModel, QTimer
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtPrintSupport import QPrintDialog

from conductor.qasync import asyncSlot, asyncClose
from conductor.qasync.qasync_helper import (QAsyncInputDialog,
                                            QAsyncMessageBox)
from conductor.stylesheet import QSS_THEME

from conductor.device import (InstrumentClosed,
                              NotConnected)


class ConfigureWidgetBase(QWidget):
    """The base class for all instrument configuration widgets.

    Must call refresh after instance creation."""
    def __init__(self, main_window, instrument, measurements_only=False):
        super().__init__()
        self._style_env = main_window._style_env
        self._main_window = main_window
        self._inst = instrument
        self._statusbar = None
        self._init_widgets(measurements_only=measurements_only)
        self.show() # Do this here so all the widgets get their sizes before being hidden

    ### The following functions must be implemented by all configuration widgets.

    async def refresh(self):
        """Reload the UI state from the instrument state."""
        raise NotImplementedError

    async def update_measurements_and_triggers(self):
        """Read current values, update control panel display, return the values."""
        raise NotImplementedError

    @property
    def connected(self):
        """See if the associated instrument is still connected."""
        return self._inst.connected

    ### Internal utility functions.

    def _toplevel_widget(self, has_reset=True):
        """Create the basic toplevel configuration widget.

        This widget has a menu bar containing Configuration, Device, View, and Help
        menus; a scalable central widget; and a status bar.
        """
        QWidget.__init__(self)
        self.setWindowTitle(f'{self._inst.long_name} ({self._inst.name})')

        self.setStyleSheet(QSS_THEME)

        layoutv = QVBoxLayout(self)
        layoutv.setContentsMargins(0, 0, 0, 0)
        layoutv.setSpacing(0)
        layoutv.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        ### Create and populate the menu bar

        self._menubar = QMenuBar()
        self._menubar.setStyleSheet('margin: 0px; padding: 0px;')

        self._menubar_configure = self._menubar.addMenu('&Configuration')
        action = QAction('&Load...', self)
        action.triggered.connect(self._menu_do_load_configuration)
        self._menubar_configure.addAction(action)
        action = QAction('&Save As...', self)
        action.triggered.connect(self._menu_do_save_configuration)
        self._menubar_configure.addAction(action)
        if has_reset:
            # Not all devices support a reset SCPI command
            action = QAction('Reset device to &default', self)
            action.triggered.connect(self._menu_do_reset_device)
            self._menubar_configure.addAction(action)
        action = QAction('&Refresh from instrument', self)
        action.triggered.connect(self._menu_do_refresh_configuration)
        self._menubar_configure.addAction(action)

        self._menubar_device = self._menubar.addMenu('&Device')
        action = QAction('&Rename...', self)
        action.triggered.connect(self._menu_do_rename_device)
        self._menubar_device.addAction(action)

        self._menubar_view = self._menubar.addMenu('&View')

        self._menubar_help = self._menubar.addMenu('&Help')
        action = QAction('&About...', self)
        action.triggered.connect(self._menu_do_about)
        self._menubar_help.addAction(action)

        layoutv.addWidget(self._menubar)

        ### Create the central widget

        central_widget = QWidget()
        layoutv.addWidget(central_widget)

        ### Create the status bar

        self._statusbar = QStatusBar()
        self._statusbar.setSizeGripEnabled(False)
        ss = """color: black; background-color: #c0c0c0; font-weight: bold;"""
        self._statusbar.setStyleSheet(ss)
        layoutv.addWidget(self._statusbar)

        return central_widget

    @asyncSlot()
    async def _menu_do_refresh_configuration(self):
        """Execute Configuration:Refresh from instrument menu option."""
        await self.refresh()

    @asyncSlot()
    async def _menu_do_rename_device(self):
        """Execute Device:Rename menu option."""
        new_name, ok = await QAsyncInputDialog.getText(self,
                                                       'Change device name',
                                                       'Device name:',
                                                       text=self._inst.name)
        if ok and new_name != self._inst.name:
            if new_name in self._main_window.device_names:
                QAsyncMessageBox.critical(self, 'Duplicate Name',
                                          f'Name "{new_name}" is already used!')
                return
            self._inst.name = new_name
            await self.device_renamed()

    async def device_renamed(self):
        """Called when the device is renamed."""
        self.setWindowTitle(f'{self._inst.long_name} ({self._inst.name})')
        # Notify the main widget so that all of the UI lists and whatnot can be updated.
        await self._main_window.device_renamed(self)

    def _menu_do_load_configuration(self):
        """Execute Configuration:Load menu option."""
        raise NotImplementedError

    def _menu_do_save_configuration(self):
        """Execute Configuration:Save As menu option."""
        raise NotImplementedError

    def _menu_do_reset_device(self):
        """Execute Configuration:Reset device to default menu option."""
        raise NotImplementedError

    def _menu_do_about(self):
        """Execute Help:About menu option."""
        raise NotImplementedError

    def closeEvent(self, event):
        """Handle window close event.

        We really want to disconnect from the instrument and notify the main_window
        here, but we can't because of a problem with qasyncio. The closeEvent()
        slot can't be marked asyncSlot because it throws an exception about a
        TypeError and invalid return type. This exception can't be caught and appears
        to be down at the C interface level. So we do a hack and simply mark
        the instrument as read_to_close, and the next time we do anything with it
        we close it."""
        self._inst._ready_to_close = True
        super().closeEvent(event)

    async def _actually_close(self):
        """Actually do the close operation in an async environment.

        See above.
        """
        self._inst.ready_to_close = False # Want this to actually succeed
        try:
            await self._inst.disconnect()
        except (InstrumentClosed, NotConnected):
            pass
        # Notify the main widget so that all of the UI lists and whatnot can be updated.
        await self._main_window.device_window_closed(self._inst)

    async def _connection_lost(self):
        """Announce that we lost the connection and close the window."""
        self.setEnabled(False)
        self.close()
        QAsyncMessageBox.critical(self, 'Connection Lost',
                                  f'Lost connection to {self._inst.long_name}')
        await self._actually_close()


class PrintableTextDialog(QDialog):
    """Text-containing Dialog that can be saved and printed."""
    def __init__(self, title, contents, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layoutv = QVBoxLayout()
        self.setLayout(layoutv)
        self._text_widget = QPlainTextEdit()
        layoutv.addWidget(self._text_widget)
        self._text_widget.setPlainText(contents)
        self._button_box = QDialogButtonBox()
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        self._close_button = self._button_box.addButton(
            'Close', QDialogButtonBox.ButtonRole.RejectRole)
        self._print_button = self._button_box.addButton(
            'Print', QDialogButtonBox.ButtonRole.ActionRole)
        self._save_button = self._button_box.addButton(
            'Save', QDialogButtonBox.ButtonRole.ActionRole)
        self._print_button.clicked.connect(self._on_print)
        self._save_button.clicked.connect(self._on_save)
        layoutv.addWidget(self._button_box)

    def _on_print(self):
        """Handle PRINT button."""
        pr = QPrintDialog()
        if pr.exec():
            self._text_widget.print(pr.printer())

    def _on_save(self):
        """Handle SAVE button."""
        fn = QFileDialog.getSaveFileName(self, caption='Save Report',
                                         filter='All (*.*);;Text (*.txt);;Log (*.log)',
                                         initialFilter='Text (*.txt)')
        fn = fn[0]
        if not fn:
            return
        with open(fn, 'w') as fp:
            fp.write(self._text_widget.toPlainText())


class DoubleSpinBoxDelegate(QStyledItemDelegate):
    """Numerical input field to use in a QTableView."""
    def __init__(self, parent, fmt, minmax):
        super().__init__(parent)
        self._fmt = fmt
        self._min_val, self._max_val = minmax

    def createEditor(self, parent, option, index):
        input = QDoubleSpinBox(parent)
        input.setAlignment(Qt.AlignmentFlag.AlignLeft)
        if self._fmt[-1] == 'd':
            input.setDecimals(0)
        else:
            input.setDecimals(int(self._fmt[1:-1]))
        input.setMinimum(self._min_val)
        input.setMaximum(self._max_val)
        input.setStepType(QAbstractSpinBox.StepType.AdaptiveDecimalStepType)
        input.setAccelerated(True)
        return input

    def setEditorData(self, editor, index):
        val = index.model().data(index, Qt.ItemDataRole.EditRole)
        editor.setValue(val)


class ListTableModel(QAbstractTableModel):
    """Table model for the List table."""
    def __init__(self, data_changed_callback):
        super().__init__()
        self._data = [[]]
        self._fmts = []
        self._header = []
        self._highlighted_row = None
        self._data_changed_calledback = data_changed_callback

    def set_params(self, data, fmts, header):
        self._data = data
        self._fmts = fmts
        self._header = header
        self.layoutChanged.emit()
        index_1 = self.index(0, 0)
        index_2 = self.index(len(self._data)-1, len(self._fmts)-1)
        self.dataChanged.emit(index_1, index_2, [Qt.ItemDataRole.DisplayRole])

    def set_highlighted_row(self, row):
        if self._highlighted_row == row:
            return
        if self._highlighted_row is not None:
            index_1 = self.index(self._highlighted_row, 0)
            index_2 = self.index(self._highlighted_row, len(self._fmts)-1)
            # Remove old highlight
            self._highlighted_row = None
            self.dataChanged.emit(index_1, index_2, [Qt.ItemDataRole.BackgroundRole])
        self._highlighted_row = row
        if row is not None:
            index_1 = self.index(row, 0)
            index_2 = self.index(row, len(self._fmts)-1)
            # Set new highlight
            self.dataChanged.emit(index_1, index_2, [Qt.ItemDataRole.BackgroundRole])

    def cur_data(self):
        return self._data

    def data(self, index, role):
        row = index.row()
        column = index.column()
        match role:
            case Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            case Qt.ItemDataRole.DisplayRole:
                val = self._data[row][column]
                return (('%'+self._fmts[column]) % val)
            case Qt.ItemDataRole.EditRole:
                return self._data[row][column]
            case Qt.ItemDataRole.BackgroundRole:
                if row == self._highlighted_row:
                    return QColor('yellow')
                return None
        return None

    def setData(self, index, val, role):
        if role == Qt.ItemDataRole.EditRole:
            row = index.row()
            column = index.column()
            val = float(val)
            self._data[row][column] = val
            self._data_changed_calledback(row, column, val)
            return True
        return False

    def rowCount(self, index):
        return len(self._data)

    def columnCount(self, index):
        return len(self._data[0])

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal:
            match role:
                case Qt.ItemDataRole.TextAlignmentRole:
                    return Qt.AlignmentFlag.AlignCenter
                case Qt.ItemDataRole.DisplayRole:
                    if 0 <= section < len(self._header):
                        return self._header[section]
                    return ''
        else:
            match role:
                case Qt.ItemDataRole.TextAlignmentRole:
                    return Qt.AlignmentFlag.AlignRight
                case Qt.ItemDataRole.DisplayRole:
                    return '%d' % (section+1)

    def flags(self, index):
        return (Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsEditable)


class LongClickButton(QPushButton):
    """Button that implements both normal click and long-hold click."""
    def __init__(self, text, click_handler, long_click_handler,
                 delay=1000):
        super().__init__(text)
        self._click_handler = click_handler
        self._long_click_handler = long_click_handler
        self._delay = delay
        self._timer = QTimer()
        # Execute longClick() after timer expires
        self._timer.timeout.connect(self.long_click)

    def mousePressEvent(self, e):
        """Rewrite the mousePressEvent method and turn on the timer."""
        self._timer.start(self._delay)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        """Override the mouseReleaseEvent method and close the timer."""
        active = self._timer.isActive()
        self._timer.stop()
        if active:
            self._click_handler(self)
        super().mouseReleaseEvent(e)

    def long_click(self):
        """Execute the long click callback handler."""
        # Here, stop must also be called once, because mouseReleaseEvent will not be
        # executed in some scenarios, for example when QMessageBox is called.
        self._timer.stop()
        self._long_click_handler(self)


class MultiSpeedSpinBox(QDoubleSpinBox):
    """DoubleSpinBox that supports click, Ctrl+click, and Shift+click steps.

    Click means normal step. Then moving counterclockwise around the keyboard,
    Shift means 0.1, ctrl means 0.01, and alt means 0.001."""
    def __init__(self, default_step, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._default_step = default_step

    def setSingleStep(self, val):
        self._default_step = val
        super().setSingleStep(val)

    def stepBy(self, steps):
        new_step = self._default_step
        mods = QApplication.queryKeyboardModifiers()
        if mods & Qt.KeyboardModifier.ShiftModifier:
            new_step *= 0.1
        elif mods & Qt.KeyboardModifier.ControlModifier:
            new_step *= 0.01
            steps = steps // 10  # Qt will already have bumped this up because of Ctrl
        elif mods & Qt.KeyboardModifier.AltModifier:
            # Note for some reason Alt+mouse wheel doesn't work. This is a Qt or
            # Windows problem.
            new_step *= 0.001
        super().setSingleStep(new_step)
        super().stepBy(steps)
