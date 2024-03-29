################################################################################
# conductor/main_window.py
#
# This file is part of the inst_conductor software suite.
#
# It contains the main window displayed when the program is first run.
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

import asyncio
from collections import namedtuple
import configparser
import csv
import itertools
import logging
import math
import platform
import sys
import time

import numpy as np

from PyQt6.QtWidgets import (QButtonGroup,
                             QComboBox,
                             QDoubleSpinBox,
                             QFrame,
                             QGridLayout,
                             QGroupBox,
                             QHBoxLayout,
                             QLabel,
                             QLayout,
                             QMenuBar,
                             QPushButton,
                             QRadioButton,
                             QVBoxLayout,
                             QWidget)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence

from conductor.qasync import asyncSlot, asyncClose
from conductor.qasync.qasync_helper import (asyncSlotSender,
                                            AsyncIPAddressDialog,
                                            QAsyncFileDialog,
                                            QAsyncMessageBox)
from conductor.stylesheet import QSS_THEME

import conductor.device as device
from conductor.plot_histogram_window import PlotHistogramWindow
from conductor.plot_xy_window import PlotXYWindow
from conductor.version import VERSION


class MainWindow(QWidget):
    """The main window of the entire application."""

    ResourceAttributes = namedtuple('ResourceAttributes',
                                    ('name', 'inst', 'config_widget'))
    def __init__(self, app, config_file, measurements_only=False):
        super().__init__()
        self.setWindowTitle('Instrument Conductor')

        match platform.system():
            case 'Linux':
                self._style_env = 'linux'
            case 'Windows':
                self._style_env = 'windows'
            case 'Darwin':
                self._style_env = 'windows'

        self.app = app

        self._logger = logging.getLogger('ic')

        self._measurements_only = measurements_only

        # Tuple of ResourceAttributes and associated lock
        self._resources_lock = asyncio.Lock()
        self._open_resources = []

        # Recorded measurements and triggers and associated lock
        self._measurement_lock = asyncio.Lock()
        self._measurement_times = []
        self._measurements = {}
        self._measurement_units = {}
        self._measurement_formats = {}
        self._measurement_names = {}
        self._measurement_last_good = True
        self._triggers = {}
        self._trigger_names = {}

        self._max_recent_resources = 9
        self._recent_resources = [] # List of resource names

        self._measurement_display_widgets = []

        self._acquisition_mode = 'Always'
        self._acquisition_ready = True
        self._user_paused = False
        self._measurement_state_source = None
        self._measurement_value_source = None
        self._measurement_value_op = '>'
        self._measurement_value_comp = 0

        self._widget_registry = {}

        self._init_widgets()

        self._config_file = config_file
        self._read_config()

    def _read_config(self):
        """Read the config file and override defaults."""
        self._logger.info(f'Reading config file {self._config_file}')
        config = configparser.ConfigParser()
        config.read(self._config_file)
        self._recent_resources = []
        if 'Recent Resources' in config:
            for i in range(self._max_recent_resources):
                key = f'recent{i+1}'
                if key in config['Recent Resources']:
                    self._recent_resources.append(config['Recent Resources'][key])
        self._refresh_menubar_device_recent_resources()

    def _write_config(self):
        """Write the config file."""
        config = configparser.ConfigParser()

        recent = {}
        for i in range(len(self._recent_resources)):
            key = f'recent{i+1}'
            if not self._recent_resources[i]:
                break
            recent[key] = self._recent_resources[i]
        config['Recent Resources'] = recent

        self._logger.debug(f'Writing config file {self._config_file}')
        with open(self._config_file, 'w') as configfile:
            config.write(configfile)

    def _init_widgets(self):
        """Initialize the top-level widgets."""
        self.setStyleSheet(QSS_THEME)

        ### Layout the widgets

        layouttopv = QVBoxLayout()
        layouttopv.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layouttopv)
        layouttopv.setSpacing(0)
        layouttopv.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        ### Create the menu bar

        self._menubar = QMenuBar()
        self._menubar.setStyleSheet('margin: 0px; padding: 0px;')

        self._menubar_device = self._menubar.addMenu('&Device')
        action = QAction('&Open with IP address...', self)
        action.setShortcut(QKeySequence('Ctrl+O'))
        action.triggered.connect(self._menu_do_open_ip)
        self._menubar_device.addAction(action)
        action = QAction('E&xit', self)
        action.triggered.connect(self._menu_do_exit)
        self._menubar_device.addAction(action)
        self._menubar_device.addSeparator()
        # Create empty actions for the recent resource list, to be filled in
        # later
        self._menubar_device_recent_actions = []
        for num in range(self._max_recent_resources):
            action = QAction(str(num), self)
            action.resource_number = num
            action.triggered.connect(self._menu_do_open_recent)
            action.setVisible(False)
            self._menubar_device.addAction(action)
            self._menubar_device_recent_actions.append(action)
        self._refresh_menubar_device_recent_resources()

        self._menubar_device = self._menubar.addMenu('&Plot')
        action = QAction('New &X/Y Plot', self)
        action.setShortcut(QKeySequence('Ctrl+P'))
        action.triggered.connect(self._menu_do_new_xy_plot)
        self._menubar_device.addAction(action)
        action = QAction('New &Histogram', self)
        action.setShortcut(QKeySequence('Ctrl+H'))
        action.triggered.connect(self._menu_do_new_histogram_plot)
        self._menubar_device.addAction(action)

        self._menubar_help = self._menubar.addMenu('&Help')
        action = QAction('&About...', self)
        action.triggered.connect(self._menu_do_about)
        self._menubar_help.addAction(action)

        layouttopv.addWidget(self._menubar)

        layoutv = QVBoxLayout()
        layoutv.setContentsMargins(11, 11, 11, 11)
        layouttopv.addLayout(layoutv)
        layouth = QHBoxLayout()
        layoutv.addLayout(layouth)

        frame = QGroupBox('Interval-Based Recording')
        self.setStyleSheet("""QGroupBox { min-width: 20em; max-width: 20em; }""")
        layoutv2 = QVBoxLayout(frame)
        layouth.addWidget(frame)

        layouth2 = QHBoxLayout()
        layoutv2.addLayout(layouth2)
        layouth2.addWidget(QLabel('Interval:'))
        input = QDoubleSpinBox()
        layouth2.addWidget(input)
        input.setAlignment(Qt.AlignmentFlag.AlignRight)
        input.setDecimals(2)
        input.setRange(0.01, 86400)
        input.setSuffix(' s')
        input.setValue(1)
        input.setSingleStep(0.5)
        input.editingFinished.connect(self._on_interval_changed)
        layouth2.addWidget(input)
        layouth2.addStretch()

        layouth2 = QHBoxLayout()
        layoutv2.addLayout(layouth2)
        layouth2.addWidget(QLabel('Acquire when:'))
        layouth2.addStretch()

        layoutg = QGridLayout()
        layoutv2.addLayout(layoutg)
        bg = QButtonGroup(layoutg)

        rb = QRadioButton('Always')
        rb.setStyleSheet('padding-left: 1.4em;') # Indent
        layoutg.addWidget(rb, 0, 0)
        bg.addButton(rb)
        rb.setChecked(True)
        rb.wid = 'Always'
        rb.toggled.connect(self._on_click_acquisition_mode)

        rb = QRadioButton('Instrument State:')
        rb.setStyleSheet('padding-left: 1.4em;') # Indent
        layoutg.addWidget(rb, 1, 0)
        bg.addButton(rb)
        rb.setChecked(False)
        rb.wid = 'State'
        rb.toggled.connect(self._on_click_acquisition_mode)
        layouth = QHBoxLayout()
        layoutg.addLayout(layouth, 1, 1)
        combo = QComboBox()
        combo.setEnabled(False)
        layouth.addWidget(combo)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.activated.connect(self._on_select_meas_state_source)
        self._widget_registry['InstrumentStateCombo'] = combo
        layouth.addStretch()

        rb = QRadioButton('Measurement Value:')
        rb.setStyleSheet('padding-left: 1.4em;') # Indent
        layoutg.addWidget(rb, 2, 0)
        bg.addButton(rb)
        rb.setChecked(False)
        rb.wid = 'Value'
        rb.toggled.connect(self._on_click_acquisition_mode)
        layouth = QHBoxLayout()
        layoutg.addLayout(layouth, 2, 1)
        combo = QComboBox()
        layouth.addWidget(combo)
        combo.setEnabled(False)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.activated.connect(self._on_select_meas_value_source)
        self._widget_registry['MeasurementValueSourceCombo'] = combo
        layouth.addStretch()

        layouth = QHBoxLayout()
        layoutg.addLayout(layouth, 3, 1)
        combo = QComboBox()
        combo.setEnabled(False)
        combo.addItem('Less than', userData='<')
        combo.addItem('Less than or equal to', userData='<=')
        combo.addItem('Greater than', userData='>')
        combo.addItem('Greater than or equal to', userData='<')
        combo.addItem('Equal to', userData='=')
        combo.addItem('Not equal to', userData='!=')
        layouth.addWidget(combo)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.activated.connect(self._on_select_meas_value_op)
        self._widget_registry['MeasurementValueOperatorCombo'] = combo
        layouth.addStretch()

        layouth = QHBoxLayout()
        layoutg.addLayout(layouth, 4, 1)
        input = QDoubleSpinBox()
        input.setEnabled(False)
        input.setAlignment(Qt.AlignmentFlag.AlignRight)
        layouth.addWidget(input)
        input.setRange(-1000000, 1000000)
        input.setDecimals(3)
        input.setSingleStep(1)
        input.editingFinished.connect(self._on_value_changed_meas_comp)
        self._widget_registry['MeasurementValueComp'] = input
        layouth.addStretch()

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        layoutv2.addWidget(divider)

        layouth2 = QHBoxLayout()
        layoutv2.addLayout(layouth2)
        button = QPushButton('\u23F5 Record')
        ss = """QPushButton { min-width: 4.5em; max-width: 4.5em;
                              min-height: 1.5em; max-height: 1.5em;
                              border-radius: 0.5em; border: 2px solid black;
                              font-weight: bold;
                              background-color: #80ff40; }
                QPushButton::pressed { border: 3px solid black; }"""
        button.setStyleSheet(ss)
        self._widget_registry['GoButton'] = button
        button.clicked.connect(self._on_click_go)
        layouth2.addWidget(button)
        button = QPushButton('\u23F8 Pause')
        ss = """QPushButton { min-width: 4.5em; max-width: 4.5em;
                              min-height: 1.5em; max-height: 1.5em;
                              border-radius: 0.5em; border: 2px solid black;
                              font-weight: bold;
                              background-color: #ff8080; }
                QPushButton::pressed { border: 3px solid black; }"""
        button.setStyleSheet(ss)
        self._widget_registry['PauseButton'] = button
        button.clicked.connect(self._on_click_pause)
        layouth2.addWidget(button)
        button = QPushButton('\u26A0 Erase All Data \u26A0')
        ss = """QPushButton { min-width: 7.5em; max-width: 7.5em;
                              min-height: 1.5em; max-height: 1.5em;
                              border-radius: 0.5em; border: 2px solid red;
                              background: black; color: red; }
                QPushButton::pressed { border: 3px solid red; }"""
        button.setStyleSheet(ss)
        layouth2.addStretch()
        layouth2.addWidget(button)
        button.clicked.connect(self._on_erase_all)

        layouth = QHBoxLayout()
        layoutv2.addLayout(layouth)
        layoutv3 = QVBoxLayout()
        layouth.addLayout(layoutv3)
        self._widget_measurement_started = QLabel('Started: N/A')
        layoutv3.addWidget(self._widget_measurement_started)
        self._widget_measurement_elapsed = QLabel('Elapsed: N/A')
        layoutv3.addWidget(self._widget_measurement_elapsed)
        self._widget_measurement_points = QLabel('# Data Points: 0')
        layoutv3.addWidget(self._widget_measurement_points)
        layouth.addStretch()
        layoutv3 = QVBoxLayout()
        layouth.addLayout(layoutv3)
        layoutv3.addStretch()
        button = QPushButton('Save CSV')
        ss = """QPushButton { min-width: 4.5em; max-width: 4.5em;
                              min-height: 1.5em; max-height: 1.5em;
                              border-radius: 0.5em; border: 2px solid black;
                              font-weight: bold;
                              background-color: #ffff80; }
                QPushButton::pressed { border: 3px solid black; }"""
        button.setStyleSheet(ss)
        self._widget_registry['SaveCSVButton'] = button
        button.clicked.connect(self._on_click_save_csv)
        layoutv3.addWidget(button)
        label = QLabel('')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layoutv3.addWidget(label)
        self._widget_registry['AcquisitionIndicator'] = label

        self._update_pause_go_buttons()
        self._update_acquisition_indicator()

        self._heartbeat_timer = QTimer(self.app)
        self._heartbeat_timer.timeout.connect(self._heartbeat_update)
        self._heartbeat_timer.setInterval(500)
        self._heartbeat_timer.start()

        self._measurement_timer = QTimer(self.app)
        self._measurement_timer.timeout.connect(self._update_measurements)
        self._measurement_timer.setInterval(1000)
        self._measurement_timer.start()

        self.show()

    def _on_interval_changed(self):
        """Handle a new value in the Measurement Interval input."""
        input = self.sender()
        self._measurement_timer.setInterval(int(input.value() * 1000))

    @asyncSlot()
    async def _on_erase_all(self):
        """Handle Erase All button."""
        async with self._measurement_lock:
            self._measurement_times = []
            for key in self._measurements:
                self._measurements[key] = []
            self._measurement_last_good = False
            for key in self._triggers:
                self._triggers[key] = []
            self._update_measurement_info_and_widgets()

    @asyncSlot()
    async def _on_click_save_csv(self):
        """Handle Save CSV button."""
        fn = await QAsyncFileDialog.getSaveFileName(
            self, caption='Save Measurements as CSV',
            filter='CSV (*.csv);;All (*.*)',
            selectedFilter='CSV (*.csv)',
            defaultSuffix='.csv')
        fn = fn[0]
        if not fn:
            return
        async with self._measurement_lock:
            if len(self._measurement_times) == 0:
                return
            with open(fn, 'w', newline='') as fp:
                csvw = csv.writer(fp, )
                header = ['Elapsed Time (s)', 'Absolute Time']
                meas_used_keys = []
                for key in self._measurements:
                    if np.all(np.isnan(self._measurements[key])):
                        continue
                    meas_used_keys.append(key)
                    m_name = self._measurement_names[key]
                    m_unit = self._measurement_units[key]
                    m_unit = m_unit.replace('\u2126', 'ohm')
                    m_unit = m_unit.replace('\u00B5', 'micro')
                    label = f'{m_name} ({m_unit})'
                    header.append(label)
                trig_used_keys = []
                for key in self._triggers:
                    if np.all(np.isnan(self._triggers[key])):
                        continue
                    trig_used_keys.append(key)
                    header.append(self._trigger_names[key])
                csvw.writerow(header)
                for idx, time_ in enumerate(self._measurement_times):
                    row = [time_ - self._measurement_times[0]]
                    timestr = time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(time_))
                    row.append(timestr)
                    for key in meas_used_keys:
                        meas = self._measurements[key][idx]
                        if np.isnan(meas):
                            row.append('')
                        else:
                            if isinstance(meas, bool):
                                meas = int(meas)
                            row.append(meas)
                    for key in trig_used_keys:
                        trig = self._triggers[key][idx]
                        if np.isnan(trig):
                            row.append('')
                        else:
                            if isinstance(trig, bool):
                                trig = int(trig)
                            row.append(trig)
                    csvw.writerow(row)

    @asyncSlotSender()
    async def _on_click_acquisition_mode(self, rb):
        """Handle Measurement Mode radio buttons."""
        if not rb.isChecked():
            return
        async with self._measurement_lock:
            async with self._resources_lock:
                self._acquisition_mode = rb.wid
                await self._check_acquisition_ready()
                await self._update_widgets()

    @asyncSlotSender()
    async def _on_select_meas_state_source(self, combo):
        """Handle Instrument State source selection."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._measurement_state_source = combo.itemData(combo.currentIndex())
                await self._check_acquisition_ready()
                await self._update_widgets()

    @asyncSlotSender()
    async def _on_select_meas_value_source(self, combo):
        """Handle Measurement Value source selection."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._measurement_value_source = combo.itemData(combo.currentIndex())
                await self._check_acquisition_ready()
                await self._update_widgets()

    @asyncSlotSender()
    async def _on_select_meas_value_op(self, combo):
        """Handle Measurement Value operator selection."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._measurement_value_op = combo.itemData(combo.currentIndex())
                await self._check_acquisition_ready()
                await self._update_widgets()

    @asyncSlotSender()
    async def _on_value_changed_meas_comp(self, input):
        """Handle Measurement Value comparison value changed."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._measurement_value_comp = input.value()
                await self._check_acquisition_ready()
                await self._update_widgets()

    @asyncSlot()
    async def _on_click_go(self):
        """Handle Go button."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._user_paused = False
                await self._update_widgets()

    @asyncSlot()
    async def _on_click_pause(self):
        """Handle Go button."""
        async with self._measurement_lock:
            async with self._resources_lock:
                self._user_paused = True
                await self._update_widgets()

    def _refresh_menubar_device_recent_resources(self):
        """Update the text in the Recent Resources actions."""
        for num in range(self._max_recent_resources):
            action = self._menubar_device_recent_actions[num]
            if num < len(self._recent_resources):
                resource_name = self._recent_resources[num]
                action.setText(f'&{num+1} {resource_name}')
                action.setVisible(True)
                if resource_name.split(' ')[0] in [x.name for x in self._open_resources]:
                    action.setEnabled(False)
                else:
                    action.setEnabled(True)
            else:
                action.setVisible(False)

    @asyncSlot()
    async def _menu_do_about(self):
        """Show the About box."""
        async with self._resources_lock:
            # Group the instruments by first three characters (e.g. SDL, SDP)
            supported = '\n'.join([', '.join(y) for y in
                                [list(x[1]) for x in
                                      itertools.groupby(device.SUPPORTED_INSTRUMENTS,
                                                        lambda x: x[:3])]])
            open = '\n'.join(
                [f'{x.inst.resource_name} - {x[1].model}, S/N {x[1].serial_number}, '
                f'FW {x.inst.firmware_version}' for x in self._open_resources])
        if open == '':
            open = 'None'
        msg = f"""Welcome to Instrument Conductor ({VERSION}), a uniform controller for \
Siglent benchtop instruments.

Supported instruments:
{supported}

Currently open resources:
{open}

Copyright 2023, Robert S. French"""
        QAsyncMessageBox.about(self, 'About', msg)

    @asyncSlot()
    async def _menu_do_open_ip(self):
        """Open a device based on an entered IP address."""
        ip_address = await AsyncIPAddressDialog.get_ip_address(
            self, 'Connect to Instrument Using IP Address')
        if not ip_address:
            return
        await self._open_ip(ip_address)

    @asyncSlotSender()
    async def _menu_do_open_recent(self, action):
        """Open a resource from the Recent Resource list."""
        # We don't bother to lock on the resource list here because there's
        # only a single access to it and nothing can interrupt in the middle.
        await self._open_resource(
            self._recent_resources[action.resource_number].split(' ')[0])

    @asyncSlot()
    async def _open_ip(self, ip_address):
        """Open a device based on the given IP address."""
        # Reformat into a standard form, removing any zeroes
        ip_address = '.'.join([('%d' % int(x)) for x in ip_address.split('.')])
        await self._open_resource(f'TCPIP::{ip_address}')

    async def _open_resource(self, resource_name):
        """Open a resource by name."""
        if resource_name in [x.name for x in self._open_resources]:
            QAsyncMessageBox.critical(self, 'Error',
                                      f'Resource "{resource_name}" is already open!')
            return

        # Create the device
        try:
            inst = await device.create_device(resource_name,
                                              existing_names=self.device_names)
        except device.NotConnected:
            QAsyncMessageBox.critical(self, 'Error',
                                      f'Failed to open "{resource_name}"')
            return
        except device.UnknownInstrumentType as ex:
            QAsyncMessageBox.critical(self, 'Error',
                                      f'Unknown instrument type "{ex.args[0]}"')
            return

        config_widget = None
        # inst.set_debug(True)
        await inst.connect()
        config_widget = inst.configure_widget(self,
                                              measurements_only=self._measurements_only)
        config_widget.show()
        await config_widget.refresh()
        if not config_widget.connected:
            return
        async with self._measurement_lock:
            async with self._resources_lock:
                self._open_resources.append(self.ResourceAttributes(
                    name=resource_name, inst=inst, config_widget=config_widget))
                # Update the recent resource list and put this resource on top
                recent_name = f'{resource_name} ({inst.model})'
                try:
                    idx = self._recent_resources.index(recent_name)
                except ValueError:
                    pass
                else:
                    del self._recent_resources[idx]
                self._recent_resources.insert(0, recent_name)
                self._recent_resources = self._recent_resources[
                    :self._max_recent_resources]
                self._refresh_menubar_device_recent_resources()
                self._write_config()

                # Update the measurement list with newly available measurements
                num_existing = len(self._measurement_times)
                measurements = config_widget.get_measurements()
                triggers = config_widget.get_triggers()
                for meas_key, meas in measurements.items():
                    name = meas['name']
                    key = (inst.long_name, meas_key)
                    if key not in self._measurements:
                        # We skip creation of new measurements if the key is already
                        # there. This happens if the instrument exists before, was
                        # deleted, and then is opened again.
                        self._measurements[key] = [math.nan] * num_existing
                        self._measurement_units[key] = meas['unit']
                        self._measurement_formats[key] = meas['format']
                        self._measurement_names[key] = f'{inst.name}: {name}'
                for trig_key, trig in triggers.items():
                    name = trig['name']
                    key = (inst.long_name, trig_key)
                    if key not in self._triggers:
                        # We skip creation of new triggers if the key is already there.
                        # This happens if the instrument exists before, was deleted, and
                        # then is opened again.
                        self._triggers[key] = [math.nan] * num_existing
                        self._trigger_names[key] = f'{inst.name}: {name}'
                for measurement_display_widget in self._measurement_display_widgets:
                    measurement_display_widget.measurements_changed()

                await self._update_widgets()
        await config_widget.start_measurements() # Start the internal measurement loop

    def _menu_do_exit(self):
        """Perform the menu exit command."""
        sys.exit(0)

    def _menu_do_new_xy_plot(self):
        """Perform the menu New XY Plot command."""
        w = PlotXYWindow(self)
        w.show()
        self._measurement_display_widgets.append(w)

    def _menu_do_new_histogram_plot(self):
        """Perform the menu New Histogram command."""
        w = PlotHistogramWindow(self)
        w.show()
        self._measurement_display_widgets.append(w)

    @staticmethod
    def _time_to_hms(t):
        m, s = divmod(t, 60)
        h, m = divmod(m, 60)
        return '%02d:%02d:%02d' % (h, m, s)

    @asyncSlot()
    async def _heartbeat_update(self):
        """Regular updates like the elapsed time and measurement display windows."""
        async with self._measurement_lock:
            self._update_measurement_info_and_widgets()

    def _update_measurement_info_and_widgets(self):
        """Update the Acquisition info and all display widgets."""
        if len(self._measurement_times) == 0:
            msg1 = 'Started: N/A'
            msg2 = 'Recorded span: N/A'
        else:
            msg1 = ('Started: ' +
                    time.strftime('%Y %b %d %H:%M:%S',
                                time.localtime(self._measurement_times[0])))
            msg2 = 'Recorded span: ' + self._time_to_hms(self._measurement_times[-1] -
                                                            self._measurement_times[0])
        self._widget_measurement_started.setText(msg1)
        self._widget_measurement_elapsed.setText(msg2)
        npts = len(self._measurement_times)
        self._widget_measurement_points.setText(f'# Data Points: {npts}')

        for measurement_display_widget in self._measurement_display_widgets:
            measurement_display_widget.update()

    async def _check_acquisition_ready(self):
        """Check to see if the current acquisition trigger is met.

        Should be called within both _resources_locak and _measurement_lock."""
        match self._acquisition_mode:
            case 'Always':
                self._acquisition_ready = True
                return
            case 'State':
                found_trigger = False
                for ra in self._open_resources:
                    if ra.config_widget is not None:
                        triggers = ra.config_widget.get_triggers()
                        for trigger_key, trigger in triggers.items():
                            found_trigger = True
                            key = (ra.inst.long_name, trigger_key)
                            if key == self._measurement_state_source:
                                self._acquisition_ready = trigger['val']
                                return
                if found_trigger:
                    assert False, 'State source no longer in open resources'
                else:
                    self._acquisition_ready = False # No triggers
                    return
            case 'Value':
                self._acquisition_ready = False
                src = self._measurement_value_source
                if src not in self._measurements:
                    return
                for ra in self._open_resources:
                    if ra.config_widget is not None:
                        if src[0] == ra.inst.long_name:
                            break
                else:
                    assert False, self._measurement_value_source
                meas = ra.config_widget.get_measurements()
                val = meas[src[1]]['val']
                if val is None:
                    return
                comp = self._measurement_value_comp
                match self._measurement_value_op:
                    case '<':
                        self._acquisition_ready = val < comp
                    case '<=':
                        self._acquisition_ready = val <= comp
                    case '>':
                        self._acquisition_ready = val > comp
                    case '>=':
                        self._acquisition_ready = val >= comp
                    case '=':
                        self._acquisition_ready = val == comp
                    case '!=':
                        self._acquisition_ready = val != comp
                    case _:
                        assert False, self._measurement_value_op
                return
            case _:
                assert False, self._acquisition_mode

    @asyncSlot()
    async def _update_measurements(self):
        """Query all instruments and update all measurements and display widgets."""
        # Although technically each measurement takes place at a different time,
        # it's important that we treat each measurement group as being at a single
        # time so we can match up measurements in X/Y plots and file saving.
        async with self._measurement_lock:
            async with self._resources_lock:
                # Check for the current trigger condition
                await self._check_acquisition_ready()
                self._update_acquisition_indicator()
                if len(self._open_resources) == 0:
                    return
                force_nan = False
                if not self._acquisition_ready or self._user_paused:
                    if self._measurement_last_good:
                        # We need to insert a fake set of NaNs so there will be a break
                        # in the line graphs when plotting
                        force_nan = True
                    else:
                        return
                cur_time = time.time()
                self._measurement_times.append(cur_time)
                # Now go through and read all the cached measurements
                meas_updated_keys = []
                trig_updated_keys = []
                for ra in self._open_resources:
                    if ra.config_widget is not None:
                        measurements = ra.config_widget.get_measurements()
                        for meas_key, meas in measurements.items():
                            name = meas['name']
                            key = (ra.inst.long_name, meas_key)
                            meas_updated_keys.append(key)
                            if key not in self._measurements:
                                self._measurements[key] = ([math.nan] *
                                                        len(self._measurement_times))
                                self._measurement_units[key] = meas['unit']
                                self._measurement_formats[key] = meas['format']
                                self._measurement_names[key] = f'{ra.inst.name}: {name}'
                            if force_nan:
                                val = math.nan
                            else:
                                val = meas['val']
                                if val is None:
                                    val = math.nan
                            self._measurements[key].append(val)
                            # The user can change the short name
                            self._measurement_names[key] = f'{ra.inst.name}: {name}'
                        triggers = ra.config_widget.get_triggers()
                        for trig_key, trig in triggers.items():
                            name = trig['name']
                            key = (ra.inst.long_name, trig_key)
                            trig_updated_keys.append(key)
                            if key not in self._triggers:
                                self._triggers[key] = ([math.nan] *
                                                    len(self._measurement_times))
                                self._trigger_names[key] = f'{ra.inst.name}: {name}'
                            if force_nan:
                                val = math.nan
                            else:
                                val = trig['val']
                                if val is None:
                                    val = math.nan
                            self._triggers[key].append(val)
                            # The user can change the short name
                            self._trigger_names[key] = f'{ra.inst.name}: {name}'
                if len(meas_updated_keys) > 0:
                    # As long as we updated at least one real instrument, then go through
                    # the measurement list and see which instruments we didn't update.
                    # This happens because those instruments were closed and no longer
                    # exist. But to keep everything happy all measurements need to be the
                    # same length, so we add on NaNs.
                    for key in self._measurements:
                        if key not in meas_updated_keys:
                            self._measurements[key].append(math.nan)
                if len(trig_updated_keys) > 0:
                    # As long as we updated at least one real instrument, then go through
                    # the trigger list and see which instruments we didn't update. This
                    # happens because those instruments were closed and no longer exist.
                    # But to keep everything happy all triggers need to be the same
                    # length, so we add on NaNs.
                    for key in self._triggers:
                        if key not in trig_updated_keys:
                            self._triggers[key].append(math.nan)
                self._measurement_last_good = not force_nan

    async def _update_widgets(self):
        """Update our widgets with current information.

        Should be called within both _resources_lock and _measurement_lock."""
        state_combo = self._widget_registry['InstrumentStateCombo']
        state_combo.clear()
        val_src_combo = self._widget_registry['MeasurementValueSourceCombo']
        val_src_combo.clear()
        val_op_combo = self._widget_registry['MeasurementValueOperatorCombo']
        val_comp = self._widget_registry['MeasurementValueComp']
        trigger_found = 0
        val_src_found = 0
        if len(self._open_resources) == 0:
            state_combo.setEnabled(False)
            val_src_combo.setEnabled(False)
            val_op_combo.setEnabled(False)
            val_comp.setEnabled(False)
            self._measurement_state_source = None
            self._measurement_value_source = None
            return
        trigger_index = 0
        measurement_index = 0
        for ra in self._open_resources:
            if ra.config_widget is not None:
                triggers = ra.config_widget.get_triggers()
                for trigger_key, trigger in triggers.items():
                    trig_name = trigger['name']
                    name = f'{ra.inst.name}: {trig_name}'
                    key = (ra.inst.long_name, trigger_key)
                    state_combo.addItem(name, userData=key)
                    if self._measurement_state_source is None:
                        self._measurement_state_source = key
                    if key == self._measurement_state_source:
                        trigger_found = trigger_index
                    trigger_index += 1
                measurements = ra.config_widget.get_measurements()
                for meas_key, measurement in measurements.items():
                    meas_name = measurement['name']
                    name = f'{ra.inst.name}: {meas_name}'
                    key = (ra.inst.long_name, meas_key)
                    val_src_combo.addItem(name, userData=key)
                    if self._measurement_value_source is None:
                        self._measurement_value_source = key
                    if key == self._measurement_value_source:
                        val_src_found = measurement_index
                    measurement_index += 1
        state_combo.setCurrentIndex(trigger_found)
        val_src_combo.setCurrentIndex(val_src_found)

        val_op_combo.setCurrentIndex(val_op_combo.findData(self._measurement_value_op))
        val_comp.setValue(self._measurement_value_comp)

        state_combo.setEnabled(True)
        val_src_combo.setEnabled(True)
        val_op_combo.setEnabled(True)
        val_comp.setEnabled(True)

        self._update_pause_go_buttons()
        self._update_acquisition_indicator()

    def _update_pause_go_buttons(self):
        """Update the Pause and Go buttons."""
        go_button = self._widget_registry['GoButton']
        pause_button = self._widget_registry['PauseButton']
        if self._user_paused:
            go_button.setEnabled(True)
            pause_button.setEnabled(False)
        else:
            go_button.setEnabled(False)
            pause_button.setEnabled(True)

    def _update_acquisition_indicator(self):
        """Update the acquisition indicator."""
        label = self._widget_registry['AcquisitionIndicator']
        if self._user_paused:
            color = '#000000'
            label.setText('Paused')
        elif self._acquisition_mode == 'Always' or self._acquisition_ready:
            color = '#b00000'
            label.setText('Acquiring')
        else:
            color = '#00b000'
            label.setText('Waiting')
        # border: 2px solid black;
        # background-color: #c0c0c0;
        ss = f"""min-width: 4em; max-width: 4em; min-height: 1.5em; max-height: 1.5em;
                 text-align: center;
                 font-weight: bold; font-size: 18px;
                 color: {color};"""
        label.setStyleSheet(ss)

    @property
    def device_names(self):
        """Return a list of all device names currently open."""
        return [x.inst.name for x in self._open_resources]

    async def device_renamed(self, config_widget):
        """Called when a device configuration window is renamed by the user."""
        async with self._measurement_lock:
            async with self._resources_lock: # For _update_widgets
                measurements = config_widget.get_measurements()
                triggers = config_widget.get_triggers()
                long_name = config_widget._inst.long_name
                inst_name = config_widget._inst.name
                for meas_key, meas in measurements.items():
                    name = meas['name']
                    key = (long_name, meas_key)
                    self._measurement_names[key] = f'{inst_name}: {name}'
                for trig_key, trig in triggers.items():
                    name = trig['name']
                    key = (long_name, trig_key)
                    self._trigger_names[key] = f'{inst_name}: {name}'

                await self._update_widgets()

                for widget in self._measurement_display_widgets:
                    widget.measurements_changed()

    @asyncClose
    async def closeEvent(self, event):
        """Handle the user closing the main window."""
        # Close all the sub-windows, allowing them to shut down peacefully
        # Closing a config window also removes it from the open resources list,
        # so we have to make a copy of the list before iterating.
        event.accept()
        sys.exit(0)
        async with self._resources_lock:
            for ra in self._open_resources[:]:
                ra.config_widget.close()
        for widget in self._measurement_display_widgets:
            widget.close()

    async def device_window_closed(self, inst):
        """Update internal state when one of the configuration widgets is closed."""
        async with self._resources_lock:
            async with self._measurement_lock:
                try:
                    idx = [x.name for x in self._open_resources].index(inst.resource_name)
                except ValueError:
                    # This can happen if the window is closed while the instrument is
                    # still being initialized. Since the resource isn't in our list
                    # yet, we can just ignore everything.
                    return
                del self._open_resources[idx]
                self._refresh_menubar_device_recent_resources()
        # for key in list(self._measurements): # Need list because we're modifying keys
        #     if key[0] == inst.long_name:
        #         del self._measurements[key]
        #         del self._measurement_names[key]
        #         del self._measurement_units[key]

                for measurement_display_widget in self._measurement_display_widgets:
                    measurement_display_widget.measurements_changed()

                if (self._measurement_state_source is not None and
                        self._measurement_state_source[0] == inst.long_name):
                    self._measurement_state_source = None
                if (self._measurement_value_source is not None and
                        self._measurement_value_source[0] == inst.long_name):
                    self._measurement_value_source = None

                await self._update_widgets()
