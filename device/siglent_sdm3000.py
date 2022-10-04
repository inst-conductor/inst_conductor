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
#
# The SDM3000 series is weird in that when you request a "RANGE" value you get
# a floating point number like "+7.50000000E+02" but when you set the RANGE you
# have to use a string like "750V". On top of that we display the range in more
# pleasant characters, like using the ohm symbol instead of writing out the word
# "ohm". This means there are three versions of each RANGE parameter:
#   - What you get from "RANGE?"
#   - What you sent to "RANGE"
#   - What you display on the screen
# We standardize the _param_state variable to be the value written to the
# instrument for "RANGE" and convert elsewhere.
#
# Also when we query "FUNCTION?" and the mode is "DC Voltage" or "DC Current"
# we just get back "VOLT" or "CURR" instead of "VOLT:DC" or "CURR:DC". We
# convert the former to the latter for storage for internal consistency.
# However, despite what the documentation claims, 2-W and 4-W resistance is
# returned as "RESISTANCE" and "FRESISTANCE" instead of "RES" and "FRES". Again
# we convert these internally for consistency.
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
        'windows': 'QGroupBox { min-height: 10em; max-height: 11em; }',
        'linux': 'QGroupBox { min-height: 10.5em; max-height: 11.5em; }',
    },
    'RangeButtons': {
        'windows': 'QRadioButton { min-width: 3em; }',
        'linux': 'QRadioButton { min-width: 3em; }',
    }
}

_COLORS_FOR_PARAMSETS = ['red', 'green', 'blue', 'yellow']

# Widget names referenced below are stored in the self._widget_registry dictionaries.
# Widget descriptors can generally be anything permitted by a standard Python
# regular expression.

# This dictionary maps from the "overall" mode (shown in the left radio button group)
# to the set of widgets that should be shown or hidden.
#   ~   means hide
#   !   means set as not enabled (greyed out)
#       No prefix means show and enable
_SDM_OVERALL_MODES = {
    'DC Voltage':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'AC Voltage':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'DC Current':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'AC Current':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    '2-W Resistance': ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    '4-W Resistance': ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Continuity':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Diode':          ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Frequency':      ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Period':         ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Temperature':    ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
    'Capacitance':    ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!Impedance.*'),
}

# This dictionary maps from the current overall mode (see above) to a description of
# what to do in this combination.
#   'widgets'       The list of widgets to show/hide/grey out/enable.
#                   See above for the syntax.
#   'mode_name'     The string to place at the beginning of a SCPI command.
#   'params'        A list of parameters active in this mode. Each entry is
#                   constructed as follows:
#       0) The SCPI base command. The 'mode_name', if any, will be prepended to give,
#          e.g. ":VOLTAGE:RANGE". If there two SCPI commands in a tuple, the second
#          is a boolean value that is always kept in sync with the first one. If the
#          first value is zero, the second value is False.
#       1) The type of the parameter. Options are '.Xf' for a float with a given
#          number of decimal places, 'd' for an integer, 'b' for a Boolean
#          (treated the same as 'd' for now), 's' for an arbitrary string,
#          and 'r' for a radio button, with the variants 'rv', 'ri', 'rr', and 'rc'
#          for radio buttons that convert RANGE values, and 'rs' that converts
#          NPLC speeds.
#       2) A widget name telling which container widgets to enable.
#       3) A widget name telling which widget contains the actual value to read
#          or set. It is automatically enabled. For a 'r' radio button, this is
#          a regular expression describing a radio button group. All are set to
#          unchecked except for the appropriate selected one which is set to
#          checked.
#       4) For numerical widgets ('d' and 'f'), the minimum allowed value.
#       5) For numerical widgets ('d' and 'f'), the maximum allowed value.
# The "Global" entry is special, since it doesn't pertain to a particular paramset.
# It is used for parameters that do not depend on which paramset is active.
# Likewise the 'ParamSet' entry is applied for each paramset (it's like global but
# local to a paramset).
_SDM_MODE_PARAMS = {  # noqa: E121,E501
    ('Global'):
        {'widgets': (),
         'mode_name': None,
         'params': (
            # For General only! The third param is True meaning to write it while
            # copying _param_state to the instrument.
            (':TRIGGER:SOURCE',               's', True),
         )
        },
    ('ParamSet'):
        {'widgets': (),
         'mode_name': None,
         'params': (
            # For General only! The third param is True meaning to write it while
            # copying _param_state to the instrument.
            (':FUNCTION',                     'r', False),
         )
        },
    ('DC Voltage'):
        {'widgets': ('FrameRange_Voltage:DC', 'FrameParam1'),
         'mode_name': 'VOLT:DC',
         'params': (
            ('RANGE',                    'rv', None, 'Range_Voltage:DC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Voltage:DC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('FILTER:STATE',              'b', None, 'DCFilter'),
            ('IMP',                       'r', 'ImpedanceLabel', 'Impedance_.*'),
          )
        },
    ('AC Voltage'):
        {'widgets': ('FrameRange_Voltage:AC', 'FrameParam1'),
         'mode_name': 'VOLT:AC',
         'params': (
            ('RANGE',                    'rv', None, 'Range_Voltage:AC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Voltage:AC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
          )
        },
    ('DC Current'):
        {'widgets': ('FrameRange_Current:DC', 'FrameParam1'),
         'mode_name': 'CURR:DC',
         'params': (
            ('RANGE',                    'ri', None, 'Range_Current:DC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Current:DC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('FILTER:STATE',              'b', None, 'DCFilter'),
          )
        },
    ('AC Current'):
        {'widgets': ('FrameRange_Current:AC', 'FrameParam1'),
         'mode_name': 'CURR:AC',
         'params': (
            ('RANGE',                    'ri', None, 'Range_Current:AC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Current:AC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
          )
        },
    ('2-W Resistance'):
        {'widgets': ('FrameRange_Resistance:2W', 'FrameParam1'),
         'mode_name': 'RES',
         'params': (
            ('RANGE',                    'rr', None, 'Range_Resistance:2W_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Resistance:2W_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
          )
        },
    ('4-W Resistance'):
        {'widgets': ('FrameRange_Resistance:4W', 'FrameParam1'),
         'mode_name': 'FRES',
         'params': (
            ('RANGE',                    'rr', None, 'Range_Resistance:4W_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Resistance:4W_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
          )
        },
    ('Capacitance'):
        {'widgets': ('FrameRange_Capacitance',),
         'mode_name': 'CAP',
         'params': (
            ('RANGE',                    'rc', None, 'Range_Capacitance_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Capacitance_Auto'),
          )
        },
    ('Continuity'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'CONT',
         'params': (
          )
        },
    ('Diode'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'DIOD',
         'params': (
          )
        },
    ('Frequency'):
        {'widgets': ('FrameRange_Frequency:Voltage',),
         'mode_name': 'FREQ',
         'params': (
            ('VOLT:RANGE',                    'rv', None, 'Range_Frequency:Voltage_RB_.*'),
            ('VOLT:RANGE:AUTO',                'b', None, 'Range_Frequency:Voltage_Auto'),
          )
        },
    ('Period'):
        {'widgets': ('FrameRange_Period:Voltage',),
         'mode_name': 'PER',
         'params': (
            ('VOLT:RANGE',                    'rv', None, 'Range_Period:Voltage_RB_.*'),
            ('VOLT:RANGE:AUTO',                'b', None, 'Range_Period:Voltage_Auto'),
          )
        },
    ('Temperature'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'TEMP',
         'params': (
          )
        },
}


# This class encapsulates the main SDM configuration widget.

class InstrumentSiglentSDM3000ConfigureWidget(ConfigureWidgetBase):
    # The number of possible paramsets. If this is set to something other than
    # 4, the View menu will need to be updated.
    _NUM_PARAMSET = 4

    def __init__(self, *args, **kwargs):
        self._debug = True

        # Override the widget registry to be paramset-specific.
        self._widget_registry = [{} for i in range(self._NUM_PARAMSET+1)]

        # The current state of all SCPI parameters. String values are always stored
        # in upper case! Entry 0 is for global values and the current instrument state,
        # and 1-N are for stored paramsets.
        self._param_state = [{} for i in range(self._NUM_PARAMSET+1)]

        # Stored measurements and triggers
        self._last_measurement_param_state = {}
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
        # Start with a blank slate
        self._param_state = [{} for i in range(self._NUM_PARAMSET+1)]
        for mode, info in _SDM_MODE_PARAMS.items():
            # Loaded paramset entries go in index 1
            idx = 0 if mode == 'Global' else 1
            for param_spec in info['params']:
                param0 = self._scpi_cmds_from_param_info(info, param_spec)
                if param0 in self._param_state[idx]:
                    # Modes often ask for the same data, no need to retrieve it twice
                    continue
                val = self._inst.query(f'{param0}?', timeout=10000)
                param_type = param_spec[1]
                if param_type[0] == '.': # Handle .3f
                    param_type = param_type[-1]
                match param_type:
                    case 'f': # Float
                        val = float(val)
                    case 'b' | 'd': # Boolean or Decimal
                        val = int(float(val))
                    case 's' | 'r': # String or radio button
                        # The SDM3000 wraps function strings in double qoutes for some
                        # reason
                        val = val.strip('"').upper()
                    case 'rv': # Voltage range
                        val = self._range_v_scpi_read_to_scpi_write(val)
                    case 'ri': # Current range
                        val = self._range_i_scpi_read_to_scpi_write(val)
                    case 'rr': # Resistance range
                        val = self._range_r_scpi_read_to_scpi_write(val)
                    case 'rc': # Capacitance range
                        val = self._range_c_scpi_read_to_scpi_write(val)
                    case 'rs': # Speed
                        val = self._speed_scpi_read_to_scpi_write(val)
                    case _:
                        assert False, f'Unknown param_type {param_type}'
                self._param_state[idx][param0] = val

        # Copy paramset 1 -> 2-N for lack of anything better to do
        for i in range(2, self._NUM_PARAMSET+1):
            self._param_state[i] = self._param_state[1].copy()

        if self._debug:
            print('** REFRESH / PARAMSET')
            for i, param_state in enumerate(self._param_state):
                print(f'{i}: {self._param_state[i]}')

        # Since everything has changed, update all the widgets
        self._update_all_widgets()

    # This writes _param_state -> instrument (opposite of refresh)
    def update_instrument(self):
        """Update the instrument with the current _param_state for paramset 1."""
        assert False# XXX
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

        self._update_state_from_param_state(1)
        self._put_inst_in_mode(self._cur_overall_mode)

    def update_measurements_and_triggers(self, read_inst=True):
        """Read current values, update control panel display, return the values."""
        if self._debug:
            print('** MEASUREMENTS / PARAMSET')
            for i, param_state in enumerate(self._param_state):
                print(f'{i}: {self._param_state[i]}')

        triggers = {}

        # We start off with all the measurements empty, and then fill them in as we
        # do each paramset.
        measurements = {
            'DC Voltage':       {'name':   'DC Voltage',
                                 'unit':   'V',
                                 'format': '10.6f',
                                 'val':    None},
            'AC Voltage':       {'name':   'AC Voltage',
                                 'unit':   'V',
                                 'format': '10.6f',
                                 'val':    None},
            'DC Current':       {'name':   'DC Current',
                                 'unit':   'A',
                                 'format': '10.6f',
                                 'val':    None},
            'AC Current':       {'name':   'AC Current',
                                 'unit':   'A',
                                 'format': '10.6f',
                                 'val':    None},
            '2-W Resistance':   {'name':   '2-W Resistance',
                                 'unit':   '\u2126',
                                 'format': '13.6f',
                                 'val':    None},
            '4-W Resistance':   {'name':   '4-W Resistance',
                                 'unit':   '\u2126',
                                 'format': '13.6f',
                                 'val':    None},
            'Capacitance':      {'name':   'Capacitance', # XXX
                                 'unit':   'F',
                                 'format': '13.6f',
                                 'val':    None},
            'Frequency':        {'name':   'Frequency', # XXX
                                 'unit':   'Hz',
                                 'format': '13.6f',
                                 'val':    None},
            'Period':           {'name':   'Period', # XXX
                                 'unit':   'ms',
                                 'format': '13.6f',
                                 'val':    None},
            'Temperature':      {'name':   'Temperature', # XXX
                                 'unit':   'K',
                                 'format': '13.6f',
                                 'val':    None},
            'Continuity':       {'name':   'Continuity', # XXX
                                 'unit':   '\u2126',
                                 'format': '13.6f',
                                 'val':    None},
            'Diode':            {'name':   'Diode', # XXX
                                 'unit':   'K',
                                 'format': '13.6f',
                                 'val':    None},
        }

        for paramset_num in range(1, self._NUM_PARAMSET+1):
            if (paramset_num != 1 and
                not self._widget_registry[paramset_num]['Enable'].isChecked()):
                continue
            ltd_param_state = self._limited_param_state(paramset_num)
            for key, val in ltd_param_state.items():
                if self._last_measurement_param_state.get(key, None) != val:
                    self._update_one_param_on_inst(key, val)
            self._last_measurement_param_state = ltd_param_state
            val = float(self._inst.query('READ?', timeout=10000))
            if abs(val) == 9.9e37:
                val = None
            mode = self._scpi_to_mode(self._param_state[paramset_num][':FUNCTION'])
            measurements[mode]['val'] = val
            if val is None:
                text = 'Overload'
            else:
                text = ('%' + measurements[mode]['format']) % val
                text += ' ' + measurements[mode]['unit']
            self._widget_registry[paramset_num]['Measurement'].setText(text)

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

        # If this changes, the menu needs to be updated
        # assert self._NUM_PARAMSET == 4 XXX
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

        ###### ROWS 1-4 - Modes and Parameter Values ######

        for paramset_num in range(1, self._NUM_PARAMSET+1):
            w = QWidget()
            self._widget_registry[paramset_num]['ParametersRow'] = w
            main_vert_layout.addWidget(w)
            ps_row_layout = QHBoxLayout()
            ps_row_layout.setContentsMargins(0, 0, 0, 0)
            w.setLayout(ps_row_layout)
            # Color stripe on the left
            color_w = QWidget()
            color = _COLORS_FOR_PARAMSETS[paramset_num-1]
            color_w.setStyleSheet(
                f'background: {color}; min-width: 0.5em; max-width: 0.5em')
            ps_row_layout.addWidget(color_w)
            # Vert layout for enable button and parameter frames
            vert_layout = QVBoxLayout()
            ps_row_layout.addLayout(vert_layout)

            if paramset_num > 1:
                # Start out with only the first paramset visible
                w.hide()

            ### ROWS 1-4, Enable button ###

            if paramset_num != 1:
                w = QCheckBox(f'Enable #{paramset_num}')
                vert_layout.addWidget(w)
                self._widget_registry[paramset_num]['Enable'] = w

            ### ROWS 1-4, COLUMN 1 ###

            row_layout = QHBoxLayout()
            vert_layout.addLayout(row_layout)

            # Overall mode: DC Voltage, AC Current, 2-W Resistance, Frequency, etc.
            layouts = QVBoxLayout()
            row_layout.addLayout(layouts)
            frame = QGroupBox('Mode')
            self._widget_registry[paramset_num][f'FrameMode'] = frame
            frame.setStyleSheet(_STYLE_SHEET['FrameMode'][self._style_env])
            layouts.addWidget(frame)
            bg = QButtonGroup(layouts)
            layouth = QHBoxLayout(frame)

            for columns in (('DC Voltage',
                             'AC Voltage',
                             'DC Current',
                             'AC Current',
                             '2-W Resistance',
                             '4-W Resistance'),
                            ('Capacitance',
                             'Continuity',
                             'Diode',
                             'Frequency',
                             'Period',
                             'Temperature')):
                layoutv = QVBoxLayout()
                layoutv.setSpacing(10)
                layouth.addLayout(layoutv)
                for mode in columns:
                    rb = QRadioButton(mode)
                    layoutv.addWidget(rb)
                    bg.addButton(rb)
                    rb.button_group = bg
                    rb.wid = (paramset_num, mode)
                    rb.toggled.connect(self._on_click_overall_mode)
                    self._widget_registry[paramset_num][f'Overall_{mode}'] = rb

            layouts.addStretch()

            ### ROWS 1-4, COLUMN 2 ###

            layouts = QVBoxLayout()
            layouts.setSpacing(0)
            row_layout.addLayout(layouts)

            # V/I/R/C Range selections
            for row_num, (mode, ranges) in enumerate(
                (('Voltage:DC', ('200 mV', '2 V', '20 V', '200 V', '1000 V')),
                 ('Voltage:AC', ('200 mV', '2 V', '20 V', '200 V', '750 V')),
                 ('Current:DC', ('200 \u00B5A', '2 mA', '20 mA', '200 mA', '2 A',
                                 '10 A')),
                 ('Current:AC', ('200 \u00B5A', '2 mA', '20 mA', '200 mA', '2 A',
                                 '10 A')),
                 ('Resistance:2W', ('200 \u2126', '2 k\u2126', '20 k\u2126',
                                    '200 k\u2126', '2 M\u2126', '10 M\u2126',
                                    '100 M\u2126')),
                 ('Resistance:4W', ('200 \u2126', '2 k\u2126', '20 k\u2126',
                                    '200 k\u2126', '2 M\u2126', '10 M\u2126',
                                    '100 M\u2126')),
                 ('Capacitance', ('2 nF', '20 nF', '200 nF', '2 \u00B5F',
                                  '20 \u00B5F', '200 \u00B5F', '10000 \u00B5F')),
                 ('Frequency:Voltage', ('200 mV', '2 V', '20 V', '200 V',
                                        '750 V')),
                 ('Period:Voltage', ('200 mV', '2 V', '20 V', '200 V',
                                     '750 V')))):
                frame = QGroupBox(f'Range')
                self._widget_registry[paramset_num][f'FrameRange_{mode}'] = frame
                layouts.addWidget(frame)
                layout = QGridLayout(frame)
                layout.setSpacing(0)
                w = QCheckBox('Auto')
                w.wid = paramset_num
                layout.addWidget(w, 0, 0)
                self._widget_registry[paramset_num][f'Range_{mode}_Auto'] = w
                w.clicked.connect(self._on_click_range_auto)
                bg = QButtonGroup(layout)
                for range_num, range_name in enumerate(ranges):
                    rb = QRadioButton(range_name)
                    rb.setStyleSheet(_STYLE_SHEET['RangeButtons'][self._style_env])
                    bg.addButton(rb)
                    rb.button_group = bg
                    rb.wid = (paramset_num, range_name)
                    rb.toggled.connect(self._on_click_range)
                    row_num, col_num = divmod(range_num, 4)
                    layout.addWidget(rb, row_num+1, col_num)
                    self._widget_registry[paramset_num][
                        f'Range_{mode}_RB_{range_name}'] = rb

            # Speed selection
            frame = QGroupBox(f'Acquisition Parameters')
            self._widget_registry[paramset_num][f'FrameParam1'] = frame
            layouts.addWidget(frame)
            layoutv2 = QVBoxLayout(frame)
            layouth2 = QHBoxLayout()
            layoutv2.addLayout(layouth2)
            w = QLabel('Speed:')
            layouth2.addWidget(w)
            self._widget_registry[paramset_num]['SpeedLabel'] = w
            bg = QButtonGroup(layouth2)
            for speed in ('Slow', 'Medium', 'Fast'):
                rb = QRadioButton(speed)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = (paramset_num, speed)
                rb.toggled.connect(self._on_click_speed)
                layouth2.addWidget(rb)
                self._widget_registry[paramset_num][f'Speed_{speed}'] = rb
            layouth2.addStretch()

            # DC Filter selection
            layouth2 = QHBoxLayout()
            layoutv2.addLayout(layouth2)
            w = QCheckBox('DC Filter')
            w.wid = paramset_num
            layouth2.addWidget(w)
            w.toggled.connect(self._on_click_dcfilter)
            self._widget_registry[paramset_num]['DCFilter'] = w

            # Impedance selection
            layouth2.addStretch()
            w = QLabel('Impedance:')
            layouth2.addWidget(w)
            self._widget_registry[paramset_num]['ImpedanceLabel'] = w
            bg = QButtonGroup(layouth2)
            for imp in ('10M', '10G'):
                rb = QRadioButton(imp)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = (paramset_num, imp)
                rb.toggled.connect(self._on_click_impedance)
                layouth2.addWidget(rb)
                self._widget_registry[paramset_num][f'Impedance_{imp}'] = rb
            layouth2.addStretch()

            layouts.addStretch()

            ### ROWS 1-4, COLUMN 3 ###

            layouts = QVBoxLayout()
            layouts.setSpacing(0)
            row_layout.addLayout(layouts)

            # Relative measurement
            frame = QGroupBox(f'Relative To')
            self._widget_registry[paramset_num]['FrameRelative'] = frame
            layouts.addWidget(frame)
            layoutv2 = QVBoxLayout(frame)

            # Relative measurement mode on
            w = QCheckBox('Relative Mode On')
            w.wid = paramset_num
            layoutv2.addWidget(w)
            w.toggled.connect(self._on_click_rel_mode_on)
            self._widget_registry[paramset_num]['RelModeOn'] = w

            layouth2 = QHBoxLayout()
            label = QLabel('Relative Value:')
            layouth2.addWidget(label)
            input = MultiSpeedSpinBox(1.)
            input.wid = paramset_num
            input.setAlignment(Qt.AlignmentFlag.AlignRight)
            input.setDecimals(3)
            input.setAccelerated(True)
            input.editingFinished.connect(self._on_value_change_rel_mode)
            layouth2.addWidget(input)
            label.sizePolicy().setRetainSizeWhenHidden(True)
            input.sizePolicy().setRetainSizeWhenHidden(True)
            layoutv2.addLayout(layouth2)
            self._widget_registry[paramset_num]['RelModeVal'] = input

            # Relative value source
            layouth2 = QHBoxLayout()
            layoutv2.addLayout(layouth2)
            w = QLabel('Value source:')
            layouth2.addWidget(w)
            self._widget_registry[paramset_num]['RelModeSourceLabel'] = w
            bg = QButtonGroup(layouth2)
            for imp in ('Manual', 'Last', 'Last 10'):
                rb = QRadioButton(imp)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = (paramset_num, imp)
                rb.toggled.connect(self._on_click_rel_mode_source)
                layouth2.addWidget(rb)
                self._widget_registry[paramset_num][f'RelModeSource_{imp}'] = rb
            layouth2.addStretch()

            layouts.addStretch()

        ###### ROWS 5-8 - MEASUREMENTS ######

        for paramset_num in range(1, self._NUM_PARAMSET+1):
            w = QWidget()
            self._widget_registry[paramset_num]['MeasurementsRow'] = w
            main_vert_layout.addWidget(w)
            ps_row_layout = QHBoxLayout()
            ps_row_layout.setContentsMargins(0, 0, 0, 0)
            w.setLayout(ps_row_layout)
            # Color stripe on the left
            color_w = QWidget()
            color = _COLORS_FOR_PARAMSETS[paramset_num-1]
            color_w.setStyleSheet(
                f'background: {color}; min-width: 0.5em; max-width: 0.5em')
            ps_row_layout.addWidget(color_w)

            if paramset_num > 1:
                # Start out with only the first paramset visible
                w.hide()

            # Main measurements widget
            container = QWidget()
            container.setStyleSheet('background: black;')
            ps_row_layout.addStretch()
            ps_row_layout.addWidget(container)

            ss = """font-size: 30px; font-weight: bold; font-family: "Courier New";
                    min-width: 6.5em; color: yellow;
                """
            layout = QGridLayout(container)
            w = QLabel('---   V')
            w.setAlignment(Qt.AlignmentFlag.AlignRight)
            w.setStyleSheet(ss)
            layout.addWidget(w, 0, 0)
            self._widget_registry[paramset_num]['Measurement'] = w
            ps_row_layout.addStretch()


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
            self._widget_registry[1]['ParametersRow'].show()
        else:
            self._widget_registry[1]['ParametersRow'].hide()

    def _menu_do_view_parameters_2(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry[2]['ParametersRow'].show()
        else:
            self._widget_registry[2]['ParametersRow'].hide()

    def _menu_do_view_parameters_3(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry[3]['ParametersRow'].show()
        else:
            self._widget_registry[3]['ParametersRow'].hide()

    def _menu_do_view_parameters_4(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry[4]['ParametersRow'].show()
        else:
            self._widget_registry[4]['ParametersRow'].hide()

    def _menu_do_view_measurements_1(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry[1]['MeasurementsRow'].show()
        else:
            self._widget_registry[1]['MeasurementsRow'].hide()

    def _menu_do_view_measurements_2(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry[2]['MeasurementsRow'].show()
        else:
            self._widget_registry[2]['MeasurementsRow'].hide()

    def _menu_do_view_measurements_3(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry[3]['MeasurementsRow'].show()
        else:
            self._widget_registry[3]['MeasurementsRow'].hide()

    def _menu_do_view_measurements_4(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry[4]['MeasurementsRow'].show()
        else:
            self._widget_registry[4]['MeasurementsRow'].hide()

    def _on_click_overall_mode(self):
        """Handle clicking on an Overall Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        paramset_num, mode = rb.wid
        self._param_state[paramset_num][':FUNCTION'] = self._mode_to_scpi(mode)
        if self._debug:
            print(self._param_state[paramset_num])
        self._update_widgets(paramset_num)

    def _on_click_range(self):
        """Handle clicking on a V/I/R/C range button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        info = self._cur_mode_param_info(paramset_num)
        mode_name = info['mode_name']
        match mode_name:
            case 'FREQ':
                mode_name = 'FREQ:VOLT'
                val = self._range_v_disp_to_scpi_write(val)
            case 'VOLT:DC' | 'VOLT:AC':
                val = self._range_v_disp_to_scpi_write(val)
            case 'CURR:DC' | 'CURR:AC':
                val = self._range_i_disp_to_scpi_write(val)
            case 'RES' | 'FRES:AC':
                val = self._range_r_disp_to_scpi_write(val)
            case 'CAP':
                val = self._range_c_disp_to_scpi_write(val)
        self._param_state[paramset_num][f':{mode_name}:RANGE'] = val
        self._update_widgets(paramset_num)

    def _on_click_range_auto(self):
        """Handle clicking on a V/I/R/C Auto range checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        cb = self.sender()
        val = cb.isChecked()
        paramset_num = cb.wid
        info = self._cur_mode_param_info(paramset_num)
        mode_name = info['mode_name']
        if mode_name == 'FREQ':
            mode_name = 'FREQ:VOLT'
        self._param_state[paramset_num][f':{mode_name}:RANGE:AUTO'] = val
        self._update_widgets(paramset_num)

    def _on_click_speed(self):
        """Handle clicking on a speed button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        info = self._cur_mode_param_info(paramset_num)
        mode_name = info['mode_name']
        val = self._speed_disp_to_scpi_write(val)
        self._param_state[paramset_num][f':{mode_name}:NPLC'] = val
        self._update_widgets(paramset_num)

    def _on_click_dcfilter(self):
        """Handle clicking on the DC Filter checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        cb = self.sender()
        paramset_num = cb.wid
        val = cb.isChecked()
        info = self._cur_mode_param_info(paramset_num)
        mode_name = info['mode_name']
        self._param_state[paramset_num][f':{mode_name}:FILTER:STATE'] = val
        self._update_widgets(paramset_num)

    def _on_click_impedance(self):
        """Handle clicking on an impedance button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        info = self._cur_mode_param_info(paramset_num)
        mode_name = info['mode_name']
        self._param_state[paramset_num][f':{mode_name}:IMP'] = val
        self._update_widgets(paramset_num)

    def _on_click_rel_mode_on(self):
        """Handle clicking on the relative mode on checkbox."""
        pass

    def _on_click_rel_mode_source(self):
        """Handle clicking on a relative mode source radio button."""
        pass

    def _on_value_change_rel_mode(self):
        """Handling entering a value for the relative mode."""
        pass

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
        self._update_param_state_and_inst(new_param_state)
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
        ps1 = param_spec[0]
        if ps1[0] == ':':
            mode_name = ''
        return f'{mode_name}{ps1}'

    def _mode_to_scpi(self, mode):
        """Return the SCPI argument to put the instrument in the mode."""
        mode = mode.upper()
        match mode:
            # For Voltage and Current, we include the ":DC" even though it isn't
            # strictly necessary to agree with the global param lists
            case 'DC VOLTAGE':
                return 'VOLT:DC'
            case 'AC VOLTAGE':
                return 'VOLT:AC'
            case 'DC CURRENT':
                return 'CURR:DC'
            case 'AC CURRENT':
                return 'CURR:AC'
            case '2-W RESISTANCE':
                return 'RES'
            case '4-W RESISTANCE':
                return 'FRES'
            case 'CAPACITANCE':
                return 'CAP'
            case 'CONTINUITY':
                return 'CONT'
            case 'DIODE':
                return 'DIOD'
            case 'FREQUENCY':
                return 'FREQ'
            case 'PERIOD':
                return 'PER'
            case 'TEMPERATURE':
                return 'TEMP'
            case _:
                assert False, mode

    def _put_inst_in_mode(self, mode):
        """Place the SDM in the given mode."""
        param = self._mode_to_scpi(mode)
        self._inst.write(f':FUNCTION "{param}"')

    def _scpi_to_mode(self, param):
        """Return the internal mode given the SCPI param."""
        # Convert the uppercase SDM-specific name to the name we use in the GUI
        match param:
            # For Voltage and Current, the SDM doesn't include the ":DC"
            case 'VOLTAGE' | 'VOLT' | 'VOLTAGE:DC' | 'VOLT:DC':
                return 'DC Voltage'
            case 'VOLTAGE:AC' | 'VOLT:AC':
                return 'AC Voltage'
            case 'CURRENT' | 'CURR' | 'CURRENT:DC' | 'CURR:DC':
                return 'DC Current'
            case 'CURRENT:AC' | 'CURR:AC':
                return 'AC Current'
            case 'RESISTANCE' | 'RES':
                return '2-W Resistance'
            case 'FRESISTANCE' | 'FRES':
                return '4-W Resistance'
            case 'CAPACITANCE' | 'CAP':
                return 'Capacitance'
            case 'CONTINUITY' | 'CONT':
                return 'Continuity'
            case 'DIODE' | 'DIOD':
                return 'Diode'
            case 'FREQUENCY' | 'FREQ':
                return 'Frequency'
            case 'PERIOD' | 'PER':
                return 'Period'
            case 'TEMPERATURE' | 'TEMP':
                return 'Temperature'
            case _:
                assert False, param

    # RANGE parameter conversions

    _RANGE_V_SCPI_READ_TO_WRITE = {
           0.2:  '200MV',
           2.0:    '2V',
          20.0:   '20V',
         200.0:  '200V',
         750.0:  '750V',
        1000.0: '1000V'
    }
    _RANGE_V_SCPI_WRITE_TO_DISP = {
         '200MV': '200 mV',
           '2V':    '2 V',
          '20V':   '20 V',
         '200V':  '200 V',
         '750V':  '750 V',
        '1000V': '1000 V'
    }
    _RANGE_V_DISP_TO_SCPI_WRITE = {value: key for key, value in
                                   _RANGE_V_SCPI_WRITE_TO_DISP.items()}

    _RANGE_I_SCPI_READ_TO_WRITE = {
         0.0002: '200UA',
         0.002:    '2MA',
         0.02:    '20MA',
         0.2:    '200MA',
         2.0:      '2A',
        10.0:     '10A'
    }
    _RANGE_I_SCPI_WRITE_TO_DISP = {
        '200UA': '200 \u00B5A',
          '2MA':   '2 mA',
         '20MA':  '20 mA',
        '200MA': '200 mA',
         '2A':     '2 A',
        '10A':    '10 A'
    }
    _RANGE_I_DISP_TO_SCPI_WRITE = {value: key for key, value in
                                   _RANGE_I_SCPI_WRITE_TO_DISP.items()}

    _RANGE_R_SCPI_READ_TO_WRITE = {
              200.0: '200OHM',
             2000.0:   '2KOHM',
            20000.0:  '20KOHM',
           200000.0: '200KOHM',
          2000000.0:   '2MOHM',
         10000000.0:  '10MOHM',
        100000000.0: '100MOHM'
    }
    _RANGE_R_SCPI_WRITE_TO_DISP = {
         '200OHM': '200 \u2126',
          '2KOHM':   '2 k\u2126',
         '20KOHM':  '20 k\u2126',
        '200KOHM': '200 k\u2126',
          '2MOHM':   '2 M\u2126',
         '10MOHM':  '10 M\u2126',
        '100MOHM': '100 M\u2126'
    }
    _RANGE_R_DISP_TO_SCPI_WRITE = {value: key for key, value in
                                   _RANGE_R_SCPI_WRITE_TO_DISP.items()}

    _RANGE_C_SCPI_READ_TO_WRITE = {
        0.000000002: '2NF',
        0.000000020: '20NF',
        0.000000200: '200NF',
        0.000002000: '2UF',
        0.000020000: '20UF',
        0.000200000: '200UF',
        0.010000000: '10000UF'
    }
    _RANGE_C_SCPI_WRITE_TO_DISP = {
            '2NF':     '2 nF',
           '20NF':    '20 nF',
          '200NF':   '200 nF',
            '2UF':     '2 \u00B5F',
           '20UF':    '20 \u00B5F',
          '200UF':   '200 \u00B5F',
        '10000UF': '10000 \u00B5F'
    }
    _RANGE_C_DISP_TO_SCPI_WRITE = {value: key for key, value in
                                   _RANGE_C_SCPI_WRITE_TO_DISP.items()}

    def _range_v_scpi_read_to_scpi_write(self, param):
        """Convert a Voltage SCPI read range to a Voltage write range."""
        return self._RANGE_V_SCPI_READ_TO_WRITE[float(param)]

    def _range_v_scpi_write_to_disp(self, range):
        """Convert a Voltage SCPI write range to a display range."""
        return self._RANGE_V_SCPI_WRITE_TO_DISP[range]

    def _range_v_disp_to_scpi_write(self, range):
        """Convert a Voltage display range to a SCPI write range."""
        return self._RANGE_V_DISP_TO_SCPI_WRITE[range]

    def _range_i_scpi_read_to_scpi_write(self, param):
        """Convert a Current SCPI read range to a SCPI write range."""
        return self._RANGE_I_SCPI_READ_TO_WRITE[float(param)]

    def _range_i_scpi_write_to_disp(self, range):
        """Convert a Current SCPI write range to a display range."""
        return self._RANGE_I_SCPI_WRITE_TO_DISP[range]

    def _range_i_disp_to_scpi_write(self, range):
        """Convert a Current display range to a SCPI write range."""
        return self._RANGE_I_DISP_TO_SCPI_WRITE[range]

    def _range_r_scpi_read_to_scpi_write(self, param):
        """Convert a Resistance SCPI read range to a SCPI write range."""
        return self._RANGE_R_SCPI_READ_TO_WRITE[float(param)]

    def _range_r_scpi_write_to_disp(self, range):
        """Convert a Resistance SCPI write range to a display range."""
        return self._RANGE_R_SCPI_WRITE_TO_DISP[range]

    def _range_r_disp_to_scpi_write(self, range):
        """Convert a Resistance display range to a SCPI write range."""
        return self._RANGE_R_DISP_TO_SCPI_WRITE[range]

    def _range_c_scpi_read_to_scpi_write(self, param):
        """Convert a Capacitance SCPI read range to a SCPI write range."""
        return self._RANGE_C_SCPI_READ_TO_WRITE[float(param)]

    def _range_c_scpi_write_to_disp(self, range):
        """Convert a Capacitance SCPI write range to a display range."""
        return self._RANGE_C_SCPI_WRITE_TO_DISP[range]

    def _range_c_disp_to_scpi_write(self, range):
        """Convert a Capacitance display range to a SCPI write range."""
        return self._RANGE_C_DISP_TO_SCPI_WRITE[range]

    def _speed_scpi_read_to_scpi_write(self, param):
        """Convert a Speed SCPI read to a SCPI write."""
        return float(param)

    def _speed_scpi_write_to_disp(self, param):
        """Convert a Speed SCPI write to a display."""
        match param:
            case 10.:
                return 'Slow'
            case 1.:
                return 'Medium'
            case 0.3:
                return 'Fast'
            case _:
                assert False, param

    def _speed_disp_to_scpi_write(self, param):
        """Convert a Speed display to a SCPI write."""
        match param:
            case 'Slow':
                return 10.
            case 'Medium':
                return 1.
            case 'Fast':
                return 0.3
            case _:
                assert False, param

    def _limited_param_state(self, paramset_num):
        """Create a param_state with only the commands necessary for this paramset."""
        ps = self._param_state[paramset_num]
        new_ps = {}
        # Normalize the SCPI mode - needed for VOLT:DC
        scpi_mode = self._mode_to_scpi(self._scpi_to_mode(ps[':FUNCTION']))
        new_ps[':FUNCTION'] = f'"{scpi_mode}"'
        param_info = self._cur_mode_param_info(paramset_num)
        for param in param_info['params']:
            param_scpi = param[0]
            if param_scpi.endswith('RANGE'):
                # Only include the RANGE when we're not in RANGE:AUTO mode
                if ps[f':{scpi_mode}:{param_scpi}:AUTO']:
                    continue
            new_ps[f':{scpi_mode}:{param_scpi}'] = ps[f':{scpi_mode}:{param_scpi}']

        return new_ps

    def _show_or_disable_widgets(self, paramset_num, widget_list):
        """Show/enable or hide/disable widgets based on regular expressions."""
        for widget_re in widget_list:
            if widget_re[0] == '~':
                # Hide unused widgets
                widget_re = widget_re[1:]
                for trial_widget in self._widget_registry[paramset_num]:
                    if re.fullmatch(widget_re, trial_widget):
                        self._widget_registry[paramset_num][trial_widget].hide()
            elif widget_re[0] == '!':
                # Disable (and grey out) unused widgets
                widget_re = widget_re[1:]
                for trial_widget in self._widget_registry[paramset_num]:
                    if re.fullmatch(widget_re, trial_widget):
                        widget = self._widget_registry[paramset_num][trial_widget]
                        widget.show()
                        widget.setEnabled(False)
                        if isinstance(widget, QRadioButton):
                            # For disabled radio buttons we remove ALL selections so it
                            # doesn't look confusing
                            widget.button_group.setExclusive(False)
                            widget.setChecked(False)
                            widget.button_group.setExclusive(True)
            else:
                # Enable/show everything else
                for trial_widget in self._widget_registry[paramset_num]:
                    if re.fullmatch(widget_re, trial_widget):
                        self._widget_registry[paramset_num][trial_widget].setEnabled(True)
                        self._widget_registry[paramset_num][trial_widget].show()

    def _update_all_widgets(self):
        """Update all paramset widgets with the current _param_state values."""
        for paramset_num in range(self._NUM_PARAMSET+1):
            self._update_widgets(paramset_num)

    def _update_widgets(self, paramset_num):
        """Update all parameter widgets with the current _param_state values."""
        # We need to do this because various set* calls below trigger the callbacks,
        # which then call this routine again in the middle of it already doing its
        # work.
        self._disable_callbacks = True

        if paramset_num == 0:
            # Global
            # Now we enable or disable widgets by first scanning through the "General"
            # widget list and then the widget list specific to this overall mode (if any).
            self._show_or_disable_widgets(paramset_num,
                                          _SDM_MODE_PARAMS['Global']['widgets'])
        else:
            # Each paramset
            # We start by setting the proper radio button selections for the "Overall Mode"
            param_info = self._cur_mode_param_info(paramset_num)
            cur_mode = self._scpi_to_mode(self._param_state[paramset_num][':FUNCTION'])
            for widget_name, widget in self._widget_registry[paramset_num].items():
                if widget_name.startswith('Overall_'):
                    widget.setChecked(widget_name.endswith(cur_mode))
            self._show_or_disable_widgets(paramset_num, _SDM_OVERALL_MODES[cur_mode])
            if param_info['widgets'] is not None:
                self._show_or_disable_widgets(paramset_num, param_info['widgets'])

        # Now we go through the details for each parameter and fill in the widget
        # value and set the widget parameters, as appropriate.
        new_param_state = {}
        if paramset_num == 0:
            params = _SDM_MODE_PARAMS['Global']['params']
            mode_name = None
        else:
            params = param_info['params']
            mode_name = param_info['mode_name']
        for scpi_cmd, param_full_type, *rest in params:
            if isinstance(scpi_cmd, (tuple, list)):
                # Ignore the boolean flag
                scpi_cmd = scpi_cmd[0]
            param_type = param_full_type
            if param_type[0] == '.':
                param_type = param_type[-1]

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
                case _:
                    assert False, f'Unknown widget parameters {rest}'

            if widget_label is not None:
                # We have to do a hack here for the Impedance radio buttons, because
                # they can only be enabled when the RANGE:AUTO is off and the manual
                # range is set to 200mV or 2V.
                if widget_label.startswith('Impedance'):
                    if (self._param_state[paramset_num][':VOLT:DC:RANGE:AUTO'] or
                        self._param_state[paramset_num][':VOLT:DC:RANGE'] not in
                            ('200MV', '2V')):
                        continue
                self._widget_registry[paramset_num][widget_label].show()
                self._widget_registry[paramset_num][widget_label].setEnabled(True)

            if widget_main is not None:
                full_scpi_cmd = scpi_cmd
                if mode_name is not None and scpi_cmd[0] != ':':
                    full_scpi_cmd = f':{mode_name}:{scpi_cmd}'
                val = self._param_state[paramset_num][full_scpi_cmd]

                if param_type in ('d', 'f', 'b'):
                    widget = self._widget_registry[paramset_num][widget_main]
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
                    case 'r' | 'rv' | 'ri' | 'rr' | 'rc' | 'rs': # Radio button
                        if param_type == 'rv':
                            val = self._range_v_scpi_write_to_disp(val)
                        elif param_type == 'ri':
                            val = self._range_i_scpi_write_to_disp(val)
                        elif param_type == 'rr':
                            val = self._range_r_scpi_write_to_disp(val)
                        elif param_type == 'rc':
                            val = self._range_c_scpi_write_to_disp(val)
                        elif param_type == 'rs':
                            val = self._speed_scpi_write_to_disp(val)
                        # In this case only the widget_main is an RE
                        for trial_widget in self._widget_registry[paramset_num]:
                            if re.fullmatch(widget_main, trial_widget):
                                widget = self._widget_registry[paramset_num][trial_widget]
                                widget.setEnabled(True)
                                checked = (trial_widget.upper()
                                           .endswith('_'+str(val).upper()))
                                widget.setChecked(checked)
                    case _:
                        assert False, f'Unknown param type {param_type}'

        self._update_param_state_and_inst(paramset_num, new_param_state)

        status_msg = None
        if status_msg is None:
            self._statusbar.clearMessage()
        else:
            self._statusbar.showMessage(status_msg)

        self._disable_callbacks = False

    def _cur_mode_param_info(self, paramset_num):
        """Get the parameter info structure for the current mode."""
        cur_mode = self._scpi_to_mode(self._param_state[paramset_num][':FUNCTION'])
        return _SDM_MODE_PARAMS[cur_mode]

    def _update_param_state_and_inst(self, paramset_num, new_param_state):
        """Update the internal state and instrument based on partial param_state."""
        for key, data in new_param_state.items():
            if data != self._param_state[key]:
                # self._update_one_param_on_inst(key, data)  XXX
                self._param_state[paramset_num][key] = data

    def _update_one_param_on_inst(self, key, data):
        """Update the value for a single parameter on the instrument."""
        fmt_data = data
        if isinstance(data, bool):
            fmt_data = '1' if data else '0'
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


# [SENSe:]CURRent:{AC|DC}:NULL[:STATe]
# [SENSe:]CURRent:{AC|DC}:NULL:VALue  -12 A to 12 A
# [SENSe:]CURRent[:DC]:NPLC 0.3 | 1 | 10  FAST/MIDDLE/SLOW

# All parameters are shared between frequency and period measurements.
# [SENSe:]{FREQuency|PERiod}:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]{FREQuency|PERiod}:NULL:VALue {<value>| minimum | maximum | default } -1.2E6 to +1.2E6
# [SENSe:]{FREQuency|PERiod}:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]{FREQuency|PERiod}:RANGe:LOWer {<filter>|MIN|MAX|DEF}

# [SENSe:]{RESistance|FRESistance}:NULL[:STATe]
# [SENSe:]{RESistance|FRESistance}:NULL:VALue             -120 MOHM to 120 MOHM
# [SENSe:]{RESistance|FRESistance}:NULL:VALue:AUTO

# [SENSe:]TEMPerature:NULL[:STATe]
# [SENSe:]TEMPerature:NULL:VALue  -1.0E15 TO +1.0E15
# [SENSe:]TEMPerature:NULL:VALue:AUTO
# [SENSe:]TEMPerature:TRANsducer?
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer:LIST?
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer     PT100(RTD)/{BITS90|EITS90|JITS90|KITS90|NIT
# S90|RITS90|SITS90|TITS90}(THER)
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer:POINt?

# [SENSe:]VOLTage:{AC|DC}:NULL[:STATe]
# [SENSe:]VOLTage:{AC|DC}:NULL:VALue      -1200 TO +1,200 V
# [SENSe:]VOLTage:{AC|DC}:NULL:VALue:AUTO
# [SENSe:]VOLTage[:DC]:IMPedance

# [SENSe:]CAPacitance:NULL[:STATe]
# [SENSe:]CAPacitance:NULL:VALue          -12 to +12 mF
# [SENSe:]CAPacitance:NULL:VALue:AUTO

# [SENSe:]CONTinuity:THReshold:VALue      0~2000 OHM
