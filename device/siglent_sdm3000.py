################################################################################
# siglent_sdm3000.py
#
# This file is part of the inst_conductor software suite.
#
# It contains all code related to the Siglent SDM3000 series of programmable
# multimeters:
#   - SDM3045X
#   - SDM3055
#   - SDM3065X
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

################################################################################
# This module contains two basic sections. The internal instrument driver is
# specified by the InstrumentSiglentSDM3000 class. The GUI for the control
# widget is specified by the InstrumentSiglentSDM3000ConfigureWidget class.
#
# Hidden accelerator keys:
#   XXX
#
# Some general notes:
#   XXX
################################################################################

################################################################################
# Known bugs in the SDM3000:
#   XXX
################################################################################


import json
import re
import time

from PyQt6.QtWidgets import (QWidget,
                             QButtonGroup,
                             QCheckBox,
                             QFileDialog,
                             QGridLayout,
                             QGroupBox,
                             QHBoxLayout,
                             QLabel,
                             QLayout,
                             QMessageBox,
                             QPushButton,
                             QRadioButton,
                             QTableView,
                             QVBoxLayout)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut

import pyqtgraph as pg

from .config_widget_base import (ConfigureWidgetBase,
                                 DoubleSpinBoxDelegate,
                                 ListTableModel,
                                 MultiSpeedSpinBox,
                                 PrintableTextDialog)
from .device import Device4882


class InstrumentSiglentSDM3000(Device4882):
    """Controller for SDM3000-series devices."""

    @classmethod
    def idn_mapping(cls):
        return {
            ('Siglent Technologies', 'SDM3045X'): InstrumentSiglentSDM3000,
            ('Siglent Technologies', 'SDM3055'):  InstrumentSiglentSDM3000,
            ('Siglent Technologies', 'SDM3065X'): InstrumentSiglentSDM3000
        }

    @classmethod
    def supported_instruments(cls):
        return (
            'SDM3045X',
            'SDM3055',
            'SDM3065X'
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing_names = kwargs['existing_names']
        super().init_names('SDM3000', 'SDM', existing_names)

    def connect(self, *args, **kwargs):
        """Connect to the instrument and set it to remote state."""
        super().connect(*args, **kwargs)
        idn = self.idn().split(',')
        if len(idn) != 4:
            assert ValueError
        (self._manufacturer,
         self._model,
         self._serial_number,
         self._firmware_version) = idn
        if self._manufacturer != 'Siglent Technologies':
            assert ValueError
        if not self._model.startswith('SDM'):
            assert ValueError
        self._long_name = f'{self._model} @ {self._resource_name}'
        # The mere act of doing any SCPI command puts the device in remote mode
        # so we don't have to do anything special here

    def disconnect(self, *args, **kwargs):
        """Disconnect from the instrument and turn off its remote state."""
        # There is no way to put the device back in local mode except by pressing the
        # LOCAL button on the front panel
        super().disconnect(*args, **kwargs)

    def configure_widget(self, main_window):
        """Return the configuration widget for this instrument."""
        return InstrumentSiglentSDM3000ConfigureWidget(main_window, self)


##########################################################################################
##########################################################################################
##########################################################################################


# Style sheets for different operating systems
_STYLE_SHEET = {
    'FrameMode': {
        'windows': 'QGroupBox { min-height: 10em; max-height: 10em; }',
        'linux': 'QGroupBox { min-height: 10.5em; max-height: 10.5em; }',
    },
    'MainParams': {
        'windows': """QGroupBox { min-width: 11em; max-width: 11em;
                                  min-height: 10em; max-height: 10em; }
                      QDoubleSpinBox { min-width: 5.5em; max-width: 5.5em; }
                   """,
        'linux': """QGroupBox { min-width: 12.5em; max-width: 12.5em;
                                min-height: 10.5em; max-height: 10.5em; }
                    QDoubleSpinBox { min-width: 5.5em; max-width: 5.5em; }
                 """,
    },
    'AuxParams': {
        'windows': """QGroupBox { min-width: 11em; max-width: 11em;
                                  min-height: 5em; max-height: 5em; }
                      QDoubleSpinBox { min-width: 5.5em; max-width: 5.5em; }
                   """,
        'linux': """QGroupBox { min-width: 12.5em; max-width: 12.5em;
                                min-height: 5em; max-height: 5em; }
                    QDoubleSpinBox { min-width: 5.5em; max-width: 5.5em; }
                 """,
    },
}

# Widget names referenced below are stored in the self._widget_registry dictionary.
# Widget descriptors can generally be anything permitted by a standard Python
# regular expression.

# This dictionary maps from the "overall" mode (shown in the left radio button group)
# to the set of widgets that should be shown or hidden.
#   !   means hide
#   ~   means set as not enabled (greyed out)
#       No prefix means show and enable
_SDM_OVERALL_MODES = {
    'Voltage':       ('!FrameRange.*',),
    'Current':       ('!FrameRange.*',),
    'Resistance':    ('!FrameRange.*',),
    'Continuity':    ('!FrameRange.*',),
    'Diode':         ('!FrameRange.*',),
    'Frequency':     ('!FrameRange.*',),
    'Period':        ('!FrameRange.*',),
    'Temperature':   ('!FrameRange.*',),
    'Capacitance':   ('!FrameRange.*',),
}

# This dictionary maps from the current overall mode (see above) and the current
# "sub mode" (if any, None otherwise) to a description of what to do
# in this combination.
#   'widgets'       The list of widgets to show/hide/grey out/enable.
#                   See above for the syntax.
#   'mode_name'     The string to place at the beginning of a SCPI command.
#   'params'        A list of parameters active in this mode. Each entry is
#                   constructed as follows:
#       0) The SCPI base command. The 'mode_name', if any, will be prepended to give,
#          e.g. ":VOLTAGE:IRANGE". If there two SCPI commands in a tuple, the second
#          is a boolean value that is always kept in sync with the first one. If the
#          first value is zero, the second value is False.
#       1) The type of the parameter. Options are '.Xf' for a float with a given
#          number of decimal places, 'd' for an integer, 'b' for a Boolean
#          (treated the same as 'd' for now), 's' for an arbitrary string,
#          and 'r' for a radio button.
#       2) A widget name telling which container widgets to enable (e.g.
#          the "*Label" boxes around the entry spinners).
#       3) A widget name telling which widget contains the actual value to read
#          or set. It is automatically enabled. For a 'r' radio button, this is
#          a regular expression describing a radio button group. All are set to
#          unchecked except for the appropriate selected one which is set to
#          checked.
#       4) For numerical widgets ('d' and 'f'), the minimum allowed value.
#       5) For numerical widgets ('d' and 'f'), the maximum allowed value.
# The "General" entry is a little special, since it doesn't pertain to a particular
# mode combination. It is used as an addon to all other modes.
_SDM_MODE_PARAMS = {  # noqa: E121,E501
    ('General'):
        {'widgets': ('~MainParametersLabel_.*', '~MainParameters_.*',
                     '~AuxParametersLabel_.*', '~AuxParameters_.*'),
         'mode_name': None,
         'params': (
            # For General only! The third param is True meaning to write it while
            # copying _param_state to the instrument.
            # SYST:REMOTE:STATE is undocumented! It locks the keyboard and
            # sets the remote access icon
            # (':SYST:REMOTE:STATE',            'b', False),
            (':FUNCTION',                     'r', False),
            (':TRIGGER:SOURCE',               's', True),
         )
        },
    ('Voltage', 'DC'):
        {'widgets': ('FrameRangeVoltage:DC'),
         'mode_name': 'VOLT:DC',
         'params': (
            ('RANGE',                    'r', None, 'Range_Voltage:DC_.*'),
          )
        },
    ('Voltage', 'AC'):
        {'widgets': ('FrameRangeVoltage:AC'),
         'mode_name': 'VOLT:AC',
         'params': (
            ('RANGE',                    'r', None, 'Range_Voltage:AC_.*'),
          )
        },
    ('Current', 'DC'):
        {'widgets': ('FrameRangeCurrent:DC'),
         'mode_name': 'CURR:DC',
         'params': (
            ('RANGE',                    'r', None, 'Range_Current:DC_.*'),
          )
        },
    ('Current', 'AC'):
        {'widgets': ('FrameRangeCurrent:AC'),
         'mode_name': 'CURR:AC',
         'params': (
            ('RANGE',                    'r', None, 'Range_Current:AC_.*'),
          )
        },
    ('Voltage', 'DC'):
        {'widgets': ('FrameRangeVoltage:DC'),
         'mode_name': 'VOLT:DC',
         'params': (
            ('RANGE',                    'r', None, 'Range_Voltage:DC_.*'),
          )
        },
    ('Resistance', '2W'):
        {'widgets': ('FrameRangeResistance:2W'),
         'mode_name': 'RES',
         'params': (
            ('RANGE',                    'r', None, 'Range_Resistance:2W_.*'),
          )
        },
    ('Resistance', '4W'):
        {'widgets': ('FrameRangeResistance:4W'),
         'mode_name': 'FRES',
         'params': (
            ('RANGE',                    'r', None, 'Range_Resistance:4W_.*'),
          )
        },

}


# This class encapsulates the main SDM configuration widget.

class InstrumentSiglentSDM3000ConfigureWidget(ConfigureWidgetBase):
    def __init__(self, *args, **kwargs):
        # The current state of all SCPI parameters. String values are always stored
        # in upper case!
        self._param_state = {}

        self._cur_overall_mode = None # e.g. Voltage, Current, Frequency
        self._cur_sub_mode = None # e.g. DC/AC, 2W/4W

        # Stored measurements and triggers
        self._cached_measurements = None
        self._cached_triggers = None

        # Needed to prevent recursive calls when setting a widget's value invokes
        # the callback handler for it.
        self._disable_callbacks = False

        # We need to call this later because some things called by __init__ rely
        # on the above variables being initialized.
        super().__init__(*args, **kwargs)

    ######################
    ### Public methods ###
    ######################

    # This reads instrument -> _param_state
    def refresh(self):
        """Read all parameters from the instrument and set our internal state to match."""
        self._param_state = {} # Start with a blank slate
        for mode, info in _SDM_MODE_PARAMS.items():
            for param_spec in info['params']:
                param0, param1 = self._scpi_cmds_from_param_info(info, param_spec)
                if param0 in self._param_state:
                    # Sub-modes often ask for the same data, no need to retrieve it twice
                    # And we will have already taken care of param1 the previous time
                    # as well
                    continue
                val = self._inst.query(f'{param0}?')
                param_type = param_spec[1][-1]
                match param_type:
                    case 'f': # Float
                        val = float(val)
                    case 'b' | 'd': # Boolean or Decimal
                        val = int(float(val))
                    case 's' | 'r': # String or radio button
                        val = val.strip('"').upper()
                    case _:
                        assert False, f'Unknown param_type {param_type}'
                self._param_state[param0] = val
                if param1 is not None:
                    # A Boolean flag associated with param0
                    # We let the flag override the previous value
                    val1 = int(float(self._inst.query(f'{param1}?')))
                    self._param_state[param1] = val1
                    if not val1 and self._param_state[param0] != 0:
                        if param_type == 'f':
                            self._param_state[param0] = 0.
                        else:
                            self._param_state[param0] = 0
                        self._inst.write(f'{param0} 0')

        # Set things like _cur_overall_mode and update widgets
        self._update_state_from_param_state()

    # This writes _param_state -> instrument (opposite of refresh)
    def update_instrument(self):
        """Update the instrument with the current _param_state."""
        set_params = set()
        first_list_mode_write = True
        for mode, info in _SDM_MODE_PARAMS.items():
            first_write = True
            for param_spec in info['params']:
                if param_spec[2] is False:
                    continue # The General False flag, all others are written
                param0, param1 = self._scpi_cmds_from_param_info(info, param_spec)
                if param0 in set_params:
                    # Sub-modes often ask for the same data, no need to retrieve it twice
                    continue
                set_params.add(param0)
                if first_write and info['mode_name']:
                    first_write = False
                    # We have to put the instrument in the correct mode before setting
                    # the parameters. Not necessary for "General" (mode_name None).
                    self._put_inst_in_mode(mode[0], mode[1])
                self._update_one_param_on_inst(param0, self._param_state[param0])
                if param1 is not None:
                    self._update_one_param_on_inst(param1, self._param_state[param1])

        self._update_state_from_param_state()
        self._put_inst_in_mode(self._cur_overall_mode, self._cur_const_mode)

    def update_measurements_and_triggers(self, read_inst=True):
        """Read current values, update control panel display, return the values."""
        measurements = {}
        triggers = {}

        # voltage = None
        # if read_inst:
        #     w = self._widget_registry['MeasureV']
        #         voltage = self._inst.measure_voltage()
        #     w.setText(f'{voltage:10.6f} V')
        # measurements['Voltage'] = {'name':   'Voltage',
        #                            'unit':   'V',
        #                            'format': '10.6f',
        #                            'val':    voltage}

        self._cached_measurements = measurements
        self._cached_triggers = triggers
        return measurements, triggers

    def get_measurements(self):
        """Return most recently cached measurements."""
        if self._cached_measurements is None:
            self.update_measurements_and_triggers()
        return self._cached_measurements

    def get_triggers(self):
        """Return most recently cached triggers."""
        if self._cached_triggers is None:
            self.update_measurements_and_triggers()
        return self._cached_triggers

    ############################################################################
    ### Setup Window Layout
    ############################################################################

    def _init_widgets(self):
        """Set up all the toplevel widgets."""
        toplevel_widget = self._toplevel_widget()

        ### Add to Device menu

        ### Add to View menu

        action = QAction('&Parameters #1', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+1'))
        action.setChecked(True)
        action.triggered.connect(self._menu_do_view_parameters_1)
        self._menubar_view.addAction(action)

        action = QAction('&Parameters #2', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+2'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_parameters_2)
        self._menubar_view.addAction(action)

        action = QAction('&Parameters #3', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+3'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_parameters_3)
        self._menubar_view.addAction(action)

        action = QAction('&Parameters #4', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+4'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_parameters_4)
        self._menubar_view.addAction(action)

        action = QAction('&Measurements #1', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+5'))
        action.setChecked(True)
        action.triggered.connect(self._menu_do_view_measurements_1)
        self._menubar_view.addAction(action)

        action = QAction('&Measurements #2', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+6'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_measurements_2)
        self._menubar_view.addAction(action)

        action = QAction('&Measurements #3', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+7'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_measurements_3)
        self._menubar_view.addAction(action)

        action = QAction('&Measurements #4', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+8'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_measurements_4)
        self._menubar_view.addAction(action)

        ### Add to Help menu

        action = QAction('&Keyboard Shortcuts...', self)
        action.triggered.connect(self._menu_do_keyboard_shortcuts)
        self._menubar_help.addAction(action)

        ### Set up configuration window widgets

        main_vert_layout = QVBoxLayout(toplevel_widget)
        main_vert_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        ###### ROWS 1-4 - Modes and Paramter Values ######

        for param_num in range(1, 5):
            w = QWidget()
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            w.setLayout(row_layout)
            main_vert_layout.addWidget(w)
            self._widget_registry[f'ParametersRow@{param_num}'] = w

            if param_num > 1:
                w.hide()

            ### ROWS 1-4, COLUMN 1 ###

            # Overall mode: Voltage, Current, Resistance, Frequency, etc.
            layouts = QVBoxLayout()
            row_layout.addLayout(layouts)
            frame = QGroupBox('Mode')
            self._widget_registry[f'FrameMode@{param_num}'] = frame
            frame.setStyleSheet(_STYLE_SHEET['FrameMode'][self._style_env])
            layouts.addWidget(frame)
            layouth = QHBoxLayout(frame)
            layoutv = QVBoxLayout()
            layoutv.setSpacing(0)
            layouth.addLayout(layoutv)
            bg = QButtonGroup(layouts)

            # Left column

            for mode, sub_modes in (('Voltage', ('DC', 'AC')),
                                    ('Current', ('DC', 'AC')),
                                    ('Resistance', ('2W', '4W')),
                                    ('Capacitance', ())):
                rb = QRadioButton(mode)
                layoutv.addWidget(rb)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = mode
                rb.toggled.connect(self._on_click_overall_mode)
                self._widget_registry[f'Overall_{mode}@{param_num}'] = rb
                if len(sub_modes):
                    layouth2 = QHBoxLayout()
                    bgh = QButtonGroup(layouth2)
                    layoutv.addLayout(layouth2)
                    for sub_mode_num, sub_mode in enumerate(sub_modes):
                        rb = QRadioButton(sub_mode)
                        if sub_mode_num == 0:
                            rb.setStyleSheet('padding-left: 1.4em;') # Indent
                        else:
                            rb.setStyleSheet('padding-left: 0.5em;'
                                             'padding-right: 0.5em') # Spacing
                        layouth2.addWidget(rb)
                        bgh.addButton(rb)
                        rb.button_group = bgh
                        rb.wid = f'{mode}:{sub_mode}@{param_num}'
                        rb.toggled.connect(self._on_click_sub_mode)
                        self._widget_registry[
                            f'Sub_Mode_{mode}_{sub_mode}@{param_num}'] = rb

            layoutv.addStretch()

            # Right column

            layoutv = QVBoxLayout()
            layoutv.setSpacing(8)
            layouth.addLayout(layoutv)

            for mode in ('Continuity',
                         'Diode',
                         'Frequency',
                         'Period',
                         'Temperature'):
                rb = QRadioButton(mode)
                layoutv.addWidget(rb)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = mode
                rb.toggled.connect(self._on_click_overall_mode)
                self._widget_registry[f'Overall_{mode}@{param_num}'] = rb

            layoutv.addStretch()

            ### ROWS 1-4, COLUMN 2 ###

            # V/I/R Range selections and Aux Parameters
            layouts = QVBoxLayout()
            layouts.setSpacing(0)
            row_layout.addLayout(layouts)

            # V/I/R Range selections
            for row_num, (mode, ranges) in enumerate(
                (('Voltage:DC', ('200mV', '2V', '20V', '200V', '1000V')),
                 ('Voltage:AC', ('200mV', '2V', '20V', '200V', '750V')),
                 ('Current:DC', ('200uA', '2mA', '20mA', '200mA', '2A', '10A')),
                 ('Current:AC', ('200uA', '2mA', '20mA', '200mA', '2A', '10A')),
                 ('Resistance:2W', ('200Ohm', '2kOhm', '20kOhm', '200kOhm', '2MOhm',
                                    '10MOhm', '100MOhm')),
                 ('Resistance:4W', ('200Ohm', '2kOhm', '20kOhm', '200kOhm', '2MOhm',
                                    '10MOhm', '100MOhm')))):
                frame = QGroupBox(f'Range')
                self._widget_registry[f'FrameRange{mode}@{param_num}'] = frame
                layouts.addWidget(frame)
                layout = QGridLayout(frame)
                layout.setSpacing(0)
                w = QCheckBox('Auto')
                layout.addWidget(rb, 0, 0)
                self._widget_registry[f'Range_{mode}_Auto@{param_num}'] = w
                w.clicked.connect(self._on_click_range_auto)
                bg = QButtonGroup(layout)
                for range_num, range_name in enumerate(ranges):
                    rb = QRadioButton(range_name)
                    bg.addButton(rb)
                    rb.button_group = bg
                    rb.wid = f'{range_name}{param_num}'
                    rb.toggled.connect(self._on_click_range)
                    row_num, col_num = divmod(range_num, 1)
                    layout.addWidget(rb, row_num+1, col_num)
                    self._widget_registry[f'Range_{mode}_{range_name}@{param_num}'] = rb

        # ###### ROW 6 - MEASUREMENTS ######

        # w = QWidget()
        # row_layout = QHBoxLayout()
        # row_layout.setContentsMargins(0, 0, 0, 0)
        # w.setLayout(row_layout)
        # main_vert_layout.addWidget(w)
        # self._widget_registry['MeasurementsRow'] = w

        # # Main measurements widget
        # container = QWidget()
        # container.setStyleSheet('background: black;')
        # row_layout.addStretch()
        # row_layout.addWidget(container)

        # ss = """font-size: 30px; font-weight: bold; font-family: "Courier New";
        #         min-width: 6.5em; color: yellow;
        #      """
        # ss2 = """font-size: 15px; font-weight: bold; font-family: "Courier New";
        #          min-width: 6.5em; color: yellow;
        #      """
        # ss3 = """font-size: 30px; font-weight: bold; font-family: "Courier New";
        #          min-width: 6.5em; color: red;
        #      """
        # ss4 = """font-size: 15px; font-weight: bold; font-family: "Courier New";
        #          min-width: 6.5em; color: red;
        #      """
        # layout = QGridLayout(container)
        # w = QLabel('---   V')
        # w.setAlignment(Qt.AlignmentFlag.AlignRight)
        # w.setStyleSheet(ss)
        # layout.addWidget(w, 0, 0)
        # self._widget_registry['MeasureV'] = w
        # row_layout.addStretch()


    ############################################################################
    ### Action and Callback Handlers
    ############################################################################

    def _menu_do_about(self):
        """Show the About box."""
        supported = ', '.join(self._inst.supported_instruments())
        msg = f"""Siglent SDM3000-series instrument interface.

Copyright 2022, Robert S. French.

Supported instruments: {supported}.

Connected to {self._inst.resource_name}
    {self._inst.model}
    S/N {self._inst.serial_number}
    FW {self._inst.firmware_version}"""

        QMessageBox.about(self, 'About', msg)

    def _menu_do_keyboard_shortcuts(self):
        """Show the Keyboard Shortcuts."""
        msg = """XXX TBD
"""
        QMessageBox.about(self, 'Keyboard Shortcuts', msg)

    def _menu_do_save_configuration(self):
        """Save the current configuration to a file."""
        fn = QFileDialog.getSaveFileName(self, caption='Save Configuration',
                                         filter='All (*.*);;SDM Configuration (*.sdmcfg)',
                                         initialFilter='SDM Configuration (*.sdmcfg)')
        fn = fn[0]
        if not fn:
            return
        ps = self._param_state.copy()
        with open(fn, 'w') as fp:
            json.dump(ps, fp, sort_keys=True, indent=4)

    def _menu_do_load_configuration(self):
        """Load the current configuration from a file."""
        fn = QFileDialog.getOpenFileName(self, caption='Load Configuration',
                                         filter='All (*.*);;SDM Configuration (*.sdmcfg)',
                                         initialFilter='SDM Configuration (*.sdmcfg)')
        fn = fn[0]
        if not fn:
            return
        with open(fn, 'r') as fp:
            ps = json.load(fp)
        # Retrieve the List mode parameters
        self._param_state = ps
        # Clean up the param state. We don't want to start with the load or short on.
        self.update_instrument()

    def _menu_do_reset_device(self):
        """Reset the instrument and then reload the state."""
        # A reset takes around 6.75 seconds, so we wait up to 10s to be safe.
        self.setEnabled(False)
        self.repaint()
        self._inst.write('*RST', timeout=10000)
        self.refresh()
        self.setEnabled(True)

    def _menu_do_view_parameters_1(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry['ParametersRow@1'].show()
        else:
            self._widget_registry['ParametersRow@1'].hide()

    def _menu_do_view_parameters_2(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry['ParametersRow@2'].show()
        else:
            self._widget_registry['ParametersRow@2'].hide()

    def _menu_do_view_parameters_3(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry['ParametersRow@3'].show()
        else:
            self._widget_registry['ParametersRow@3'].hide()

    def _menu_do_view_parameters_4(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry['ParametersRow@4'].show()
        else:
            self._widget_registry['ParametersRow@4'].hide()

    def _menu_do_view_measurements_1(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry['MeasurementsRow@1'].show()
        else:
            self._widget_registry['MeasurementsRow@1'].hide()

    def _menu_do_view_measurements_2(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry['MeasurementsRow@2'].show()
        else:
            self._widget_registry['MeasurementsRow@2'].hide()

    def _menu_do_view_measurements_3(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry['MeasurementsRow@3'].show()
        else:
            self._widget_registry['MeasurementsRow@3'].hide()

    def _menu_do_view_measurements_4(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry['MeasurementsRow@4'].show()
        else:
            self._widget_registry['MeasurementsRow@4'].hide()

    def _on_click_overall_mode(self):
        """Handle clicking on an Overall Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        # self._cur_overall_mode = rb.wid
        # self._cur_dynamic_mode = None
        # new_param_state = {}
        # new_param_state[':EXT:MODE'] = 'INT'  # Overridden by 'Ext' below
        # # Special handling for each button
        # match self._cur_overall_mode:
        #     case 'Basic':
        #         self._cur_const_mode = self._param_state[':FUNCTION'].title()
        #         if self._cur_const_mode in ('Led', 'OCP', 'OPP'):
        #             # LED is weird in that the instrument treats it as a BASIC mode
        #             # but there's no CV/CC/CP/CR choice.
        #             # We lose information going from OCP/OPP back to Basic because
        #             # we don't know which basic mode we were in before!
        #             self._cur_const_mode = 'Voltage' # For lack of anything else to do
        #         # Force update since this does more than set a parameter - it switches
        #         # modes
        #         self._param_state[':FUNCTION'] = None
        #         new_param_state[':FUNCTION'] = self._cur_const_mode.upper()
        #         self._param_state[':FUNCTION:MODE'] = 'BASIC'
        #     case 'Dynamic':
        #         self._cur_const_mode = (
        #             self._param_state[':FUNCTION:TRANSIENT'].title())
        #         # Dynamic also has sub-modes - Continuous, Pulse, Toggle
        #         param_info = self._cur_mode_param_info(null_dynamic_mode_ok=True)
        #         mode_name = param_info['mode_name']
        #         self._cur_dynamic_mode = (
        #             self._param_state[f':{mode_name}:TRANSIENT:MODE'].title())
        #         # Force update since this does more than set a parameter - it switches
        #         # modes
        #         self._param_state[':FUNCTION:TRANSIENT'] = None
        #         new_param_state[':FUNCTION:TRANSIENT'] = self._cur_const_mode.upper()
        #         self._param_state[':FUNCTION:MODE'] = 'TRAN'
        #     case 'LED':
        #         # Force update since this does more than set a parameter - it switches
        #         # modes
        #         self._param_state[':FUNCTION'] = None
        #         new_param_state[':FUNCTION'] = 'LED' # LED is consider a BASIC mode
        #         self._param_state[':FUNCTION:MODE'] = 'BASIC'
        #         self._cur_const_mode = None
        #     case 'Battery':
        #         # This is not a parameter with a state - it's just a command to switch
        #         # modes. The normal :FUNCTION tells us we're in the Battery mode, but it
        #         # doesn't allow us to SWITCH TO the Battery mode!
        #         self._inst.write(':BATTERY:FUNC')
        #         self._param_state[':FUNCTION:MODE'] = 'BATTERY'
        #         self._cur_const_mode = self._param_state[':BATTERY:MODE'].title()
        #     case 'OCPT':
        #         # This is not a parameter with a state - it's just a command to switch
        #         # modes. The normal :FUNCTION tells us we're in OCP mode, but it
        #         # doesn't allow us to SWITCH TO the OCP mode!
        #         self._inst.write(':OCP:FUNC')
        #         self._param_state[':FUNCTION:MODE'] = 'OCP'
        #         self._cur_const_mode = None
        #     case 'OPPT':
        #         # This is not a parameter with a state - it's just a command to switch
        #         # modes. The normal :FUNCTION tells us we're in OPP mode, but it
        #         # doesn't allow us to SWITCH TO the OPP mode!
        #         self._inst.write(':OPP:FUNC')
        #         self._param_state[':FUNCTION:MODE'] = 'OPP'
        #         self._cur_const_mode = None
        #     case 'Ext \u26A0':
        #         # EXTI and EXTV are really two different modes, but we treat them
        #         # as one for consistency. Unfortunately that means when the user switches
        #         # to "EXT" mode, you don't know whether they actually want V or I,
        #         # so we just assume V.
        #         self._cur_const_mode = 'Voltage'
        #         new_param_state[':EXT:MODE'] = 'EXTV'
        #     case 'List':
        #         # This is not a parameter with a state - it's just a command to switch
        #         # modes. The normal :FUNCTION tells us we're in List mode, but it
        #         # doesn't allow us to SWITCH TO the List mode!
        #         self._inst.write(':LIST:STATE:ON')
        #         self._param_state[':FUNCTION:MODE'] = 'LIST'
        #         self._cur_const_mode = self._param_state[':LIST:MODE'].title()
        #     case 'Program':
        #         # This is not a parameter with a state - it's just a command to switch
        #         # modes. The normal :FUNCTION tells us we're in List mode, but it
        #         # doesn't allow us to SWITCH TO the List mode!
        #         self._inst.write(':PROGRAM:STATE:ON')
        #         self._param_state[':FUNCTION:MODE'] = 'PROGRAM'
        #         self._cur_const_mode = None

        # self._update_param_state_and_inst(new_param_state)
        # self._update_widgets()

    def _on_click_sub_mode(self):
        """Handling clicking on the DC/AC Voltage buttons."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return

    def _on_click_range(self):
        """Handle clicking on a V/I/R range button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        info = self._cur_mode_param_info()
        mode_name = info['mode_name']
        val = rb.wid
        trans = self._transient_string()
        if val.endswith('V'):
            new_param_state = {f':{mode_name}{trans}:VRANGE': val.strip('V')}
        else:
            new_param_state = {f':{mode_name}{trans}:IRANGE': val.strip('A')}
        self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_click_range_auto(self):
        """Handle clicking on a V/I/R Auto range checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        # if not rb.isChecked():
        #     return
        # info = self._cur_mode_param_info()
        # mode_name = info['mode_name']
        # val = rb.wid
        # trans = self._transient_string()
        # if val.endswith('V'):
        #     new_param_state = {f':{mode_name}{trans}:VRANGE': val.strip('V')}
        # else:
        #     new_param_state = {f':{mode_name}{trans}:IRANGE': val.strip('A')}
        # self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_value_change(self):
        """Handle clicking on any input value edit box."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        input = self.sender()
        param_name, scpi = input.wid
        scpi_cmd_state = None
        scpi_state = None
        if isinstance(scpi, tuple):
            scpi, scpi_state = scpi
        info = self._cur_mode_param_info()
        mode_name = info['mode_name']
        if scpi[0] == ':':
            mode_name = ''  # Global setting
            scpi_cmd = scpi
            if scpi_state is not None:
                scpi_cmd_state = scpi_state
        else:
            mode_name = f':{mode_name}'
            scpi_cmd = f'{mode_name}:{scpi}'
            if scpi_state is not None:
                scpi_cmd_state = f'{mode_name}:{scpi_state}'
        val = input.value()
        if input.decimals() > 0:
            val = float(input.value())
        else:
            val = int(val)
        new_param_state = {scpi_cmd: val}
        # Check for special case of associated boolean flag. In these cases if the
        # value is zero, we set the boolean to False. If the value is non-zero, we
        # set the boolean to True. This makes zero be the "deactivated" sentinal.
        if scpi_cmd_state in self._param_state:
            new_param_state[scpi_cmd_state] = int(val != 0)
        # Check for the special case of a slew parameter. The rise and fall slew
        # values are tied together. In 5A mode, both must be in the range
        # 0.001-0.009 or 0.010-0.500. In 30A mode, both must be in the range
        # 0.001-0.099 or 0.100-2.500. If one of the inputs goes outside of its
        # current range, the other field needs to be changed.
        if 'SLEW' in scpi_cmd:
            if mode_name == '':
                trans = ''
            else:
                trans = self._transient_string()
            irange = self._param_state[f'{mode_name}{trans}:IRANGE']
            if input.registry_name.endswith('SlewPos'):
                other_name = input.registry_name.replace('SlewPos', 'SlewNeg')
                other_scpi = scpi.replace('POSITIVE', 'NEGATIVE')
            else:
                other_name = input.registry_name.replace('SlewNeg', 'SlewPos')
                other_scpi = scpi.replace('NEGATIVE', 'POSITIVE')
            other_widget = self._widget_registry[other_name]
            orig_other_val = other_val = other_widget.value()
            if irange == '5':
                if 0.001 <= val <= 0.009:
                    if not (0.001 <= other_val <= 0.009):
                        other_val = 0.009
                elif not (0.010 <= other_val <= 0.500):
                    other_val = 0.010
            else:
                if 0.001 <= val <= 0.099:
                    if not (0.001 <= other_val <= 0.099):
                        other_val = 0.099
                elif not (0.100 <= other_val <= 2.500):
                    other_val = 0.100
            if orig_other_val != other_val:
                scpi_cmd = f'{mode_name}:{other_scpi}'
                new_param_state[scpi_cmd] = other_val
        self._update_param_state_and_inst(new_param_state)
        if scpi_cmd == ':LIST:STEP':
            # When we change the number of steps, we might need to read in more
            # rows from the instrument
            self._update_list_mode_from_instrument(new_rows_only=True)
        self._update_widgets()

    def _on_click_trigger_source(self):
        """Handle clicking on a trigger source button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        new_param_state = {':TRIGGER:SOURCE': rb.mode.upper()}
        self._update_param_state_and_inst(new_param_state)
        self._update_trigger_buttons()

    ################################
    ### Internal helper routines ###
    ################################

    def _scpi_cmds_from_param_info(self, param_info, param_spec):
        """Create a SCPI command from a param_info structure."""
        mode_name = param_info['mode_name']
        if mode_name is None: # General parameters
            mode_name = ''
        else:
            mode_name = f':{mode_name}:'
        if isinstance(param_spec[0], (tuple, list)):
            ps1, ps2 = param_spec[0]
            if ps1[0] == ':':
                mode_name = ''
            return f'{mode_name}{ps1}', f'{mode_name}{ps2}'
        ps1 = param_spec[0]
        if ps1[0] == ':':
            mode_name = ''
        return f'{mode_name}{ps1}', None

    def _put_inst_in_mode(self, overall_mode, sub_mode):
        """Place the SDM in the given overall mode."""
        overall_mode = overall_mode.upper()
        if sub_mode is not None:
            sub_mode = sub_mode.upper()
        match overall_mode:
            case ('VOLTAGE', 'CURRENT'):
                self._inst.write(f':FUNCTION "{overall_mode}:{sub_mode}"')
            case 'RESISTANCE':
                if sub_mode == '2W':
                    self._inst.write(f':FUNCTION "RESISTANCE"')
                else:
                    self._inst.write(f':FUNCTION "FRESISTANCE"')
            case _:
                self._inst.write(f':FUNCTION "{overall_mode}"')

    def _update_state_from_param_state(self):
        """Update all internal state and widgets based on the current _param_state."""
        mode = self._param_state[':FUNCTION']
        # Convert the uppercase SDM-specific name to the name we use in the GUI
        self._cur_sub_mode = None
        match mode:
            case 'VOLT:DC':
                self._cur_overall_mode = 'Voltage'
                self._cur_sub_mode = 'DC'
            case 'VOLT:AC':
                self._cur_overall_mode = 'VOLTAGE'
                self._cur_sub_mode = 'AC'
            case 'CURR:DC':
                self._cur_overall_mode = 'Current'
                self._cur_sub_mode = 'DC'
            case 'CURR:AC':
                self._cur_overall_mode = 'Current'
                self._cur_sub_mode = 'AC'
            case 'RES':
                self._cur_overall_mode = 'Resistance'
                self._cur_sub_mode = '2W'
            case 'FRES':
                self._cur_overall_mode = 'Resistance'
                self._cur_sub_mode = '4W'
            case _:
                assert False, mode

        # Now update all the widgets and their values with the new info
        self._update_widgets()

    def _show_or_disable_widgets(self, widget_list):
        """Show/enable or hide/disable widgets based on regular expressions."""
        for widget_re in widget_list:
            if widget_re[0] == '~':
                # Hide unused widgets
                widget_re = widget_re[1:]
                for trial_widget in self._widget_registry:
                    if re.fullmatch(widget_re, trial_widget):
                        self._widget_registry[trial_widget].hide()
            elif widget_re[0] == '!':
                # Disable (and grey out) unused widgets
                widget_re = widget_re[1:]
                for trial_widget in self._widget_registry:
                    if re.fullmatch(widget_re, trial_widget):
                        widget = self._widget_registry[trial_widget]
                        widget.setEnabled(False)
                        if isinstance(widget, QRadioButton):
                            # For disabled radio buttons we remove ALL selections so it
                            # doesn't look confusing
                            widget.button_group.setExclusive(False)
                            widget.setChecked(False)
                            widget.button_group.setExclusive(True)
            else:
                # Enable/show everything else
                for trial_widget in self._widget_registry:
                    if re.fullmatch(widget_re, trial_widget):
                        self._widget_registry[trial_widget].setEnabled(True)
                        self._widget_registry[trial_widget].show()

    def _update_widgets(self, minmax_ok=True):
        """Update all parameter widgets with the current _param_state values."""
        if self._cur_overall_mode is None:
            return

        # We need to do this because various set* calls below trigger the callbacks,
        # which then call this routine again in the middle of it already doing its
        # work.
        self._disable_callbacks = True

        param_info = self._cur_mode_param_info()
        mode_name = param_info['mode_name']

        # We start by setting the proper radio button selections for the "Overall Mode"
        for widget_name, widget in self._widget_registry.items():
            if widget_name.startswith('Overall_'):
                widget.setChecked(widget_name.endswith(self._cur_overall_mode))

        # First we go through the widgets for the Dynamic sub-modes and the Constant
        # Modes and enable or disable them as appropriate based on the Overall Mode.
        self._show_or_disable_widgets(_SDM_OVERALL_MODES[self._cur_overall_mode])

        # Now we enable or disable widgets by first scanning through the "General"
        # widget list and then the widget list specific to this overall mode (if any).
        self._show_or_disable_widgets(_SDM_MODE_PARAMS['General']['widgets'])
        if param_info['widgets'] is not None:
            self._show_or_disable_widgets(param_info['widgets'])

        # Now we go through the details for each parameter and fill in the widget
        # value and set the widget parameters, as appropriate. We do the General
        # parameters first and then the parameters for the current mode.
        new_param_state = {}
        for phase in range(2):
            if phase == 0:
                params = _SDM_MODE_PARAMS['General']['params']
                mode_name = None
            else:
                params = param_info['params']
                mode_name = param_info['mode_name']
            for scpi_cmd, param_full_type, *rest in params:
                if isinstance(scpi_cmd, (tuple, list)):
                    # Ignore the boolean flag
                    scpi_cmd = scpi_cmd[0]
                param_type = param_full_type[-1]

                # Parse out the label and main widget REs and the min/max values
                match len(rest):
                    case 1:
                        # For General, these parameters don't have associated widgets
                        assert rest[0] in (False, True)
                        widget_label = None
                        widget_main = None
                    case 2:
                        # Just a label and main widget, no value range
                        widget_label, widget_main = rest
                    case 4:
                        # A label and main widget with min/max value
                        widget_label, widget_main, min_val, max_val = rest
                        trans = self._transient_string()
                        if min_val in ('C', 'V', 'P'):
                            min_val = 0
                        elif min_val == 'S':
                            min_val = 0.001
                        elif isinstance(min_val, str) and min_val.startswith('W:'):
                            min_val = 0
                        if isinstance(max_val, str):
                            match max_val[0]:
                                case 'C': # Based on current range selection (5A, 30A)
                                    # Don't need to check for mode_name being None
                                    # because that will never happen for C/V/P/S
                                    max_val = self._param_state[
                                        f':{mode_name}{trans}:IRANGE']
                                    max_val = float(max_val)
                                case 'V': # Based on voltage range selection (36V, 150V)
                                    max_val = self._param_state[
                                        f':{mode_name}{trans}:VRANGE']
                                    max_val = float(max_val)
                                case 'S': # Slew range depends on IRANGE
                                    if self._param_state[
                                            f':{mode_name}{trans}:IRANGE'] == '5':
                                        max_val = 0.5
                                    else:
                                        max_val = 2.5
                                case 'W':
                                    max_val = 1000000000
                    case _:
                        assert False, f'Unknown widget parameters {rest}'

                if widget_label is not None:
                    self._widget_registry[widget_label].show()
                    self._widget_registry[widget_label].setEnabled(True)

                if widget_main is not None:
                    full_scpi_cmd = scpi_cmd
                    if mode_name is not None and scpi_cmd[0] != ':':
                        full_scpi_cmd = f':{mode_name}:{scpi_cmd}'
                    val = self._param_state[full_scpi_cmd]

                    if param_type in ('d', 'f', 'b'):
                        widget = self._widget_registry[widget_main]
                        widget.setEnabled(True)
                        widget.show()
                    if param_type in ('d', 'f'):
                        widget.setMaximum(max_val)
                        widget.setMinimum(min_val)

                    match param_type:
                        case 'b': # Boolean - used for checkboxes
                            widget.setChecked(val)
                        case 'd': # Decimal
                            widget.setDecimals(0)
                            widget.setValue(val)
                            # It's possible that setting the minimum or maximum caused
                            # the value to change, which means we need to update our
                            # state.
                            if val != int(float(widget.value())):
                                widget_val = float(widget.value())
                                new_param_state[full_scpi_cmd] = widget_val
                        case 'f': # Floating point
                            assert param_full_type[0] == '.'
                            dec = int(param_full_type[1:-1])
                            dec10 = 10 ** dec
                            widget.setDecimals(dec)
                            widget.setValue(val)
                            # It's possible that setting the minimum or maximum caused
                            # the value to change, which means we need to update our
                            # state. Note floating point comparison isn't precise so we
                            # only look to the precision of the number of decimals.
                            if int(val*dec10+.5) != int(widget.value()*dec10+.5):
                                widget_val = float(widget.value())
                                new_param_state[full_scpi_cmd] = widget_val
                        case 'r': # Radio button
                            # In this case only the widget_main is an RE
                            for trial_widget in self._widget_registry:
                                if re.fullmatch(widget_main, trial_widget):
                                    widget = self._widget_registry[trial_widget]
                                    widget.setEnabled(True)
                                    checked = (trial_widget.upper()
                                               .endswith('_'+str(val).upper()))
                                    widget.setChecked(checked)
                        case _:
                            assert False, f'Unknown param type {param_type}'

        self._update_param_state_and_inst(new_param_state)

        status_msg = None
        if status_msg is None:
            self._statusbar.clearMessage()
        else:
            self._statusbar.showMessage(status_msg)

        self._disable_callbacks = False

    def _cur_mode_param_info(self, null_dynamic_mode_ok=False):
        """Get the parameter info structure for the current mode."""
        key = (self._cur_overall_mode, self._cur_sub_mode)
        return _SDM_MODE_PARAMS[key]

    def _update_param_state_and_inst(self, new_param_state):
        """Update the internal state and instrument based on partial param_state."""
        for key, data in new_param_state.items():
            if data != self._param_state[key]:
                self._update_one_param_on_inst(key, data)
                self._param_state[key] = data

    def _update_one_param_on_inst(self, key, data):
        """Update the value for a single parameter on the instrument."""
        fmt_data = data
        if isinstance(data, bool):
            fmt_data = '1' if True else '0'
        elif isinstance(data, float):
            fmt_data = '%.6f' % data
        elif isinstance(data, int):
            fmt_data = int(data)
        elif isinstance(data, str):
            # This is needed because there are a few places when the instrument
            # is case-sensitive to the SCPI argument! For example,
            # "TRIGGER:SOURCE Bus" must be "BUS"
            fmt_data = data.upper()
        else:
            assert False
        self._inst.write(f'{key} {fmt_data}')
