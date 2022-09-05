################################################################################
# siglent_sdl1000.py
#
# This file is part of the inst_conductor software suite.
#
# It contains all code related to the Siglent SDL1000 series of programmable
# DC electronic loads:
#   - SDL1020X
#   - SDL1020X-E
#   - SDL1030X
#   - SDL1030X-E
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
# specified by the InstrumentSiglentSDL1000 class. The GUI for the control
# widget is specified by the InstrumentSiglentSDL1000ConfigureWidget class.
#
# Hidden accelerator keys:
#   Alt+L       Load ON/OFF
#   Alt+T       Trigger
#
# Some general notes:
#
# ** SIGLENT SCPI DOCUMENTATION
#
# There are errors and omissions in the "Siglent Programming Guide: SDL1000X
# Programmable DC Electronic Load" document (PG0801X-C01A dated 2019). In particular
# there are two SCPI commands that we need that are undocumented:
#
# - SYST:REMOTE:STATE locks the instrument keyboard and shows the remote control
#   icon.
# - :FUNCTION:MODE[?] which is described below
#
# Figuring out what mode the instrument is in and setting it to a new mode is
# a confusing mismash of operations. Here are the details.
#
# :FUNCTION:MODE? is an undocumented SCPI command that is ONLY useful for queries. You
#   cannot use it to set the mode! It returns one of:
#       BASIC, TRAN, BATTERY, OCP, OPP, LIST, PROGRAM
#   Note that LED is considered a BASIC mode.
#
# :FUNCTION <X> is used to set the "constant X" mode while in the BASIC mode. If the
#   instrument is not currently in the BASIC mode, this places it in the BASIC mode.
#   There is no way to go into the BASIC mode without also specifying the "constant"
#   mode. Examples:
#       :FUNCTION VOLTAGE
#       :FUNCTION CURRENT
#       :FUNCTION POWER
#       :FUNCTION RESISTANCE
#   LED mode is considered a BASIC mode, so to put the instrument in LED mode,
#   execute:
#       :FUNCTION LED
#   It can also be used to query the current "constant X" mode:
#       :FUNCTION?
#
# :FUNCTION:TRANSIENT <X> does the same thing as :FUNCTION but for the TRANSIENT
#   (Dynamic) mode. It can both query the current "constant X" mode and put the
#   instrument into the Dynamic mode.
#
# To place the instrument in other modes, you use specific commands:
#   :BATTERY:FUNC
#   :OCP:FUNC
#   :OPP:FUNC
#   :LIST:STATE:ON
#   :PROGRAM:STATE:ON
################################################################################

################################################################################
# Known bugs in the SDL1000:
#
# - If you are in List mode and you trigger during the final step, wait for the
#   step to complete, then trigger again to restart the sequence, it will continue
#   with the step past the end of the current # of steps (which is inactive)
#   before returning to step 0. We don't try to fix this problem, but we do
#   display a warning dialog when it's about to occur.
# - In Battery mode with Constant Current selected, the SCPI command to update
#   the desired current value will cause the value to change on the display
#   (and in the SDL's memory), but won't actually affect the currently running
#   test. This isn't true for Constant Power or Constant Resistance. Changes to
#   those values take effect immediately. But none of these can be changed from
#   the panel. So we disable all changes while the test is running.
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

from .device import Device4882
from .config_widget_base import (ConfigureWidgetBase,
                                 DoubleSpinBoxDelegate,
                                 ListTableModel,
                                 MultiSpeedSpinBox,
                                 PrintableTextDialog)


class InstrumentSiglentSDL1000(Device4882):
    """Controller for SDL1000-series devices."""

    @classmethod
    def idn_mapping(cls):
        return {
            ('Siglent Technologies', 'SDL1020X'):   InstrumentSiglentSDL1000,
            ('Siglent Technologies', 'SDL1020X-E'): InstrumentSiglentSDL1000,
            ('Siglent Technologies', 'SDL1030X'):   InstrumentSiglentSDL1000,
            ('Siglent Technologies', 'SDL1030X-E'): InstrumentSiglentSDL1000
        }

    @classmethod
    def supported_instruments(cls):
        return (
            'SDL1020X',
            'SDL1020X-E',
            'SDL1030X',
            'SDL1030X-E'
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        existing_names = kwargs['existing_names']
        super().init_names('SDL1000', 'SDL', existing_names)

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
        if not self._model.startswith('SDL'):
            assert ValueError
        self._long_name = f'{self._model} @ {self._resource_name}'
        self._max_power = 300 if self._model in ('SDL1030X-E', 'SDL1030X') else 200
        self.write(':SYST:REMOTE:STATE 1') # Lock the keyboard

    def disconnect(self, *args, **kwargs):
        """Disconnect from the instrument and turn off its remote state."""
        self.write(':SYST:REMOTE:STATE 0')
        super().disconnect(*args, **kwargs)

    def configure_widget(self, main_window):
        """Return the configuration widget for this instrument."""
        return InstrumentSiglentSDL1000ConfigureWidget(main_window, self)

    def set_input_state(self, val):
        """Turn the load on or off."""
        self._validator_1(val)
        self.write(f':INPUT:STATE {val}')

    def measure_voltage(self):
        """Return the measured voltage as a float."""
        return float(self.query('MEAS:VOLT?'))

    def measure_current(self):
        """Return the measured current as a float."""
        return float(self.query('MEAS:CURR?'))

    def measure_power(self):
        """Return the measured power as a float."""
        return float(self.query('MEAS:POW?'))

    def measure_trise(self):
        """Return the measured Trise as a float."""
        return float(self.query('TIME:TEST:RISE?'))

    def measure_tfall(self):
        """Return the measured Tfall as a float."""
        return float(self.query('TIME:TEST:FALL?'))

    def measure_resistance(self):
        """Return the measured resistance as a float."""
        return float(self.query('MEAS:RES?'))

    def measure_battery_time(self):
        """Return the battery discharge time (in seconds) as a float."""
        return float(self.query(':BATTERY:DISCHA:TIMER?'))

    def measure_battery_capacity(self):
        """Return the battery discharge capacity (in Ah) as a float."""
        return float(self.query(':BATTERY:DISCHA:CAP?')) / 1000

    def measure_battery_add_capacity(self):
        """Return the battery discharge additional capacity (in Ah) as a float."""
        return float(self.query(':BATTERY:ADDCAP?')) / 1000

    def measure_vcpr(self):
        """Return measured Voltage, Current, Power, and Resistance."""
        return (self.measure_voltage(),
                self.measure_current(),
                self.measure_power(),
                self.measure_resistance())


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
    'TriggerButton': {
        'windows': """QPushButton {
                          min-width: 2.9em; max-width: 2.9em;
                          min-height: 1.5em; max-height: 1.5em;
                          border-radius: 0.75em; border: 4px solid black;
                          font-weight: bold; font-size: 18px;
                          background: #ffff80; }
                      QPushButton:pressed { border: 6px solid black; }""",
        'linux': """QPushButton {
                        min-width: 3.2em; max-width: 3.2em;
                        min-height: 1.5em; max-height: 1.5em;
                        border-radius: 0.75em; border: 4px solid black;
                        font-weight: bold; font-size: 18px;
                        background: #ffff80; }
                    QPushButton:pressed { border: 6px solid black; }""",
    },
    'ListTable': {
        'windows': """QTableView { min-width: 18em; max-width: 18em;
                                   min-height: 11em; max-height: 11em; }""",
        'linux': """QTableView { min-width: 18em; max-width: 18em;
                                 min-height: 10em; max-height: 10em; }""",
    },
    'ListPlot': {
        'windows': (365, 178),
        'linux': (485, 172),
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
# Basically, the Dynamic modes (continuous, pulse, trigger) are only available in
# Dynamic mode, and the Constant X modes are only available in Basic, Dynamic,
# Battery (except CV), and List modes.
_SDL_OVERALL_MODES = {
    'Basic':      ('!Dynamic_Mode_.*', 'Const_.*', '~ListRow'),
    'Dynamic':    ('Dynamic_Mode_.*',  'Const_.*', '~ListRow'),
    'LED':        ('!Dynamic_Mode_.*', '!Const_.*', '~ListRow'),
    'Battery':    ('!Dynamic_Mode_.*', 'Const_.*', '!Const_Voltage', '~ListRow'),
    'List':       ('!Dynamic_Mode_.*', 'Const_.*', 'ListRow'),
    'Program':    ('!Dynamic_Mode_.*', '!Const_.*',
                   '!Range_Current_.*', '!Range_Voltage_.*', '~ListRow'),
    'OCPT':       ('!Dynamic_Mode_.*', '!Const_.*', '~ListRow'),
    'OPPT':       ('!Dynamic_Mode_.*', '!Const_.*', '~ListRow'),
    'Ext \u26A0': ('!Dynamic_Mode_.*', 'Const_.*', '!Const_Power', '!Const_Resistance',
                   '~ListRow'),
}

# This dictionary maps from the current overall mode (see above) and the current
# "Constant X" mode (if any, None otherwise) to a description of what to do
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
#           For 4 and 5, the min/max can be a constant number or a special
#           character. 'C' means the limits of the CURRENT RANGE. 'V' means the
#           limits of the VOLTAGE RANGE. 'P' means the limits of power based
#           on the SDL model number (200W or 300W). 'S' means the limits of current
#           slew based on the current IRANGE. It can also be 'W:<widget_name>'
#           which means to retrieve the value of that widget; this is useful for
#           min/max pairs.
# The "General" entry is a little special, since it doesn't pertain to a particular
# mode combination. It is used as an addon to all other modes.
_SDL_MODE_PARAMS = {  # noqa: E121,E501
    ('General'):
        {'widgets': ('~MainParametersLabel_.*', '~MainParameters_.*',
                     '~AuxParametersLabel_.*', '~AuxParameters_.*',
                     '~EnableTRise', '~EnableTFall',
                     '~MeasureBatt.*', '~ClearAddCap'),
         'mode_name': None,
         'params': (
            # For General only! The third param is True meaning to write it while
            # copying _param_state to the instrument.
            # SYST:REMOTE:STATE is undocumented! It locks the keyboard and
            # sets the remote access icon
            (':SYST:REMOTE:STATE',            'b', False),
            (':INPUT:STATE',                  'b', False),
            (':SHORT:STATE',                  'b', False),
            (':FUNCTION',                     'r', False),
            (':FUNCTION:TRANSIENT',           'r', False),
            # FUNCtion:MODE is undocumented! Possible return values are:
            #   BASIC, TRAN, BATTERY, OCP, OPP, LIST, PROGRAM
            (':FUNCTION:MODE',                's', False),
            (':BATTERY:MODE',                 's', True),
            (':LIST:MODE',                    's', True),
            (':TRIGGER:SOURCE',               's', True),
            (':SENSE:AVERAGE:COUNT',          'd', 'GlobalParametersLabel_AvgCount', 'GlobalParameters_AvgCount', 6, 14),
            (':SYSTEM:SENSE:STATE',           'b', None, 'ExternalVoltageSense'),
            (':SYSTEM:IMONITOR:STATE',        'b', None, 'EnableIMonitor'),
            (':SYSTEM:VMONITOR:STATE',        'b', None, 'EnableVMonitor'),
            (':EXT:INPUT:STATE',              'b', None, 'ExternalInputState'),
            (':EXT:MODE',                     's', True),
            (':TIME:TEST:STATE',              'b', True),
            (':VOLTAGE:LEVEL:ON',           '.3f', None, 'GlobalParameters_BreakoverV', 0, 150),
            (':VOLTAGE:LATCH:STATE',          'b', None, 'BreakoverVoltageLatch'),
            ((':CURRENT:PROTECTION:LEVEL',
              ':CURRENT:PROTECTION:STATE'), '.3f', 'GlobalParametersLabel_CurrentProtL', 'GlobalParameters_CurrentProtL', 0, 30),
            (':CURRENT:PROTECTION:DELAY',   '.3f', 'GlobalParametersLabel_CurrentProtD', 'GlobalParameters_CurrentProtD', 0, 60),
            ((':POWER:PROTECTION:LEVEL',
              ':POWER:PROTECTION:STATE'),   '.2f', 'GlobalParametersLabel_PowerProtL', 'GlobalParameters_PowerProtL', 0, 'P'),
            (':POWER:PROTECTION:DELAY',     '.3f', 'GlobalParametersLabel_PowerProtD', 'GlobalParameters_PowerProtD', 0, 60),
         )
        },
    ('Basic', 'Voltage'):
        {'widgets': ('EnableTRise', 'EnableTFall'),
         'mode_name': 'VOLTAGE',
         'params': (
            ('IRANGE',                    'r', None, 'Range_Current_.*'),
            ('VRANGE',                    'r', None, 'Range_Voltage_.*'),
            ('LEVEL:IMMEDIATE',         '.3f', 'MainParametersLabel_Voltage', 'MainParameters_Voltage', 0, 'V'),
            (':TIME:TEST:VOLTAGE:LOW',  '.3f', 'MainParametersLabel_TimeVLow', 'MainParameters_TimeVLow', 0, 'W:MainParameters_TimeVHigh'),
            (':TIME:TEST:VOLTAGE:HIGH', '.3f', 'MainParametersLabel_TimeVHigh', 'MainParameters_TimeVHigh', 'W:MainParameters_TimeVLow', 150),
          )
        },
    ('Basic', 'Current'):
        {'widgets': ('EnableTRise', 'EnableTFall'),
         'mode_name': 'CURRENT',
         'params': (
            ('IRANGE',            'r', None, 'Range_Current_.*'),
            ('VRANGE',            'r', None, 'Range_Voltage_.*'),
            ('LEVEL:IMMEDIATE', '.3f', 'MainParametersLabel_Current', 'MainParameters_Current', 0, 'C'),
            (':TIME:TEST:VOLTAGE:LOW',  '.3f', 'MainParametersLabel_TimeVLow', 'MainParameters_TimeVLow', 0, 'W:MainParameters_TimeVHigh'),
            (':TIME:TEST:VOLTAGE:HIGH', '.3f', 'MainParametersLabel_TimeVHigh', 'MainParameters_TimeVHigh', 'W:MainParameters_TimeVLow', 150),
            ('SLEW:POSITIVE',   '.3f', 'AuxParametersLabel_BSlewPos', 'AuxParameters_BSlewPos', 'S', 'S'),
            ('SLEW:NEGATIVE',   '.3f', 'AuxParametersLabel_BSlewNeg', 'AuxParameters_BSlewNeg', 'S', 'S'),
          )
        },
    ('Basic', 'Power'):
        {'widgets': ('EnableTRise', 'EnableTFall'),
         'mode_name': 'POWER',
         'params': (
            ('IRANGE',            'r', None, 'Range_Current_.*'),
            ('VRANGE',            'r', None, 'Range_Voltage_.*'),
            ('LEVEL:IMMEDIATE', '.3f', 'MainParametersLabel_Power', 'MainParameters_Power', 0, 'P'),
            (':TIME:TEST:VOLTAGE:LOW',  '.3f', 'MainParametersLabel_TimeVLow', 'MainParameters_TimeVLow', 0, 'W:MainParameters_TimeVHigh'),
            (':TIME:TEST:VOLTAGE:HIGH', '.3f', 'MainParametersLabel_TimeVHigh', 'MainParameters_TimeVHigh', 'W:MainParameters_TimeVLow', 150),
          )
        },
    ('Basic', 'Resistance'):
        {'widgets': ('EnableTRise', 'EnableTFall'),
         'mode_name': 'RESISTANCE',
         'params': (
            ('IRANGE',            'r', None, 'Range_Current_.*'),
            ('VRANGE',            'r', None, 'Range_Voltage_.*'),
            ('LEVEL:IMMEDIATE', '.3f', 'MainParametersLabel_Resistance', 'MainParameters_Resistance', 0.030, 10000),
            (':TIME:TEST:VOLTAGE:LOW',  '.3f', 'MainParametersLabel_TimeVLow', 'MainParameters_TimeVLow', 0, 'W:MainParameters_TimeVHigh'),
            (':TIME:TEST:VOLTAGE:HIGH', '.3f', 'MainParametersLabel_TimeVHigh', 'MainParameters_TimeVHigh', 'W:MainParameters_TimeVLow', 150),
          )
        },
    ('LED', None): # This behaves like a Basic mode
        {'widgets': None,
         'mode_name': 'LED',
         'params': (
            ('IRANGE',    'r', None, 'Range_Current_.*'),
            ('VRANGE',    'r', None, 'Range_Voltage_.*'),
            ('VOLTAGE', '.3f', 'MainParametersLabel_LEDV', 'MainParameters_LEDV', 0.010, 'V'),
            ('CURRENT', '.3f', 'MainParametersLabel_LEDC', 'MainParameters_LEDC', 0, 'C'),
            ('RCONF',   '.2f', 'MainParametersLabel_LEDR', 'MainParameters_LEDR', 0.01, 1),
          )
        },
    ('Battery', 'Current'):
        {'widgets': ('MeasureBatt.*', 'ClearAddCap'),
         'mode_name': 'BATTERY',
         'params': (
            ('IRANGE',            'r', None, 'Range_Current_.*'),
            ('VRANGE',            'r', None, 'Range_Voltage_.*'),
            ('LEVEL',           '.3f', 'MainParametersLabel_BattC', 'MainParameters_BattC', 0, 'C'),
            (('VOLTAGE',
              'VOLTAGE:STATE'), '.3f', 'MainParametersLabel_BattVStop', 'MainParameters_BattVStop', 0, 'V'),
            (('CAP',
              'CAP:STATE'),       'd', 'MainParametersLabel_BattCapStop', 'MainParameters_BattCapStop', 0, 999999),
            (('TIMER',
              'TIMER:STATE'),     'd', 'MainParametersLabel_BattTimeStop', 'MainParameters_BattTimeStop', 0, 86400),
          )
        },
    ('Battery', 'Power'):
        {'widgets': ('MeasureBatt.*', 'ClearAddCap'),
         'mode_name': 'BATTERY',
         'params': (
            ('IRANGE',            'r', None, 'Range_Current_.*'),
            ('VRANGE',            'r', None, 'Range_Voltage_.*'),
            ('LEVEL',           '.3f', 'MainParametersLabel_BattP', 'MainParameters_BattP', 0, 'P'),
            (('VOLTAGE',
              'VOLTAGE:STATE'), '.3f', 'MainParametersLabel_BattVStop', 'MainParameters_BattVStop', 0, 'V'),
            (('CAP',
              'CAP:STATE'),       'd', 'MainParametersLabel_BattCapStop', 'MainParameters_BattCapStop', 0, 999999),
            (('TIMER',
              'TIMER:STATE'),     'd', 'MainParametersLabel_BattTimeStop', 'MainParameters_BattTimeStop', 0, 86400),
          )
        },
    ('Battery', 'Resistance'):
        {'widgets': ('MeasureBatt.*', 'ClearAddCap'),
         'mode_name': 'BATTERY',
         'params': (
            ('IRANGE',          'r', None, 'Range_Current_.*'),
            ('VRANGE',          'r', None, 'Range_Voltage_.*'),
            ('LEVEL',           '.3f', 'MainParametersLabel_BattR', 'MainParameters_BattR', 0.030, 10000),
            (('VOLTAGE',
              'VOLTAGE:STATE'), '.3f', 'MainParametersLabel_BattVStop', 'MainParameters_BattVStop', 0, 'V'),
            (('CAP',
              'CAP:STATE'),       'd', 'MainParametersLabel_BattCapStop', 'MainParameters_BattCapStop', 0, 999999),
            (('TIMER',
              'TIMER:STATE'),     'd', 'MainParametersLabel_BattTimeStop', 'MainParameters_BattTimeStop', 0, 86400),
          )
        },
    ('Dynamic', 'Voltage', 'Continuous'):
        {'widgets': None,
         'mode_name': 'VOLTAGE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelV', 'MainParameters_ALevelV', 0, 'V'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelV', 'MainParameters_BLevelV', 0, 'V'),
            ('TRANSIENT:AWIDTH', '.3f', 'MainParametersLabel_AWidth',  'MainParameters_AWidth', 1, 999),
            ('TRANSIENT:BWIDTH', '.3f', 'MainParametersLabel_BWidth',  'MainParameters_BWidth', 1, 999),
          )
        },
    ('Dynamic', 'Voltage', 'Pulse'):
        {'widgets': None,
         'mode_name': 'VOLTAGE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelV', 'MainParameters_ALevelV', 0, 'V'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelV', 'MainParameters_BLevelV', 0, 'V'),
            ('TRANSIENT:BWIDTH', '.3f', 'MainParametersLabel_Width',   'MainParameters_Width', 1, 999),
          )
        },
    ('Dynamic', 'Voltage', 'Toggle'):
        {'widgets': None,
         'mode_name': 'VOLTAGE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelV', 'MainParameters_ALevelV', 0, 'V'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelV', 'MainParameters_BLevelV', 0, 'V'),
          )
        },
    ('Dynamic', 'Current', 'Continuous'):
        {'widgets': None,
         'mode_name': 'CURRENT',
         'params': (
            ('TRANSIENT:IRANGE',          'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',          'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',            'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL',        '.3f', 'MainParametersLabel_ALevelC', 'MainParameters_ALevelC', 0, 'C'),
            ('TRANSIENT:BLEVEL',        '.3f', 'MainParametersLabel_BLevelC', 'MainParameters_BLevelC', 0, 'C'),
            ('TRANSIENT:AWIDTH',        '.6f', 'MainParametersLabel_AWidth',  'MainParameters_AWidth', 0.000020, 999),
            ('TRANSIENT:BWIDTH',        '.6f', 'MainParametersLabel_BWidth',  'MainParameters_BWidth', 0.000020, 999),
            ('TRANSIENT:SLEW:POSITIVE', '.3f', 'AuxParametersLabel_TSlewPos', 'AuxParameters_TSlewPos', 'S', 'S'),
            ('TRANSIENT:SLEW:NEGATIVE', '.3f', 'AuxParametersLabel_TSlewNeg', 'AuxParameters_TSlewNeg', 'S', 'S'),
          )
        },
    ('Dynamic', 'Current', 'Pulse'):
        {'widgets': None,
         'mode_name': 'CURRENT',
         'params': (
            ('TRANSIENT:IRANGE',          'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',          'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',            'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL',        '.3f', 'MainParametersLabel_ALevelC', 'MainParameters_ALevelC', 0, 'C'),
            ('TRANSIENT:BLEVEL',        '.3f', 'MainParametersLabel_BLevelC', 'MainParameters_BLevelC', 0, 'C'),
            ('TRANSIENT:BWIDTH',        '.6f', 'MainParametersLabel_Width',   'MainParameters_Width', 0.000020, 999),
            ('TRANSIENT:SLEW:POSITIVE', '.3f', 'AuxParametersLabel_TSlewPos', 'AuxParameters_TSlewPos', 'S', 'S'),
            ('TRANSIENT:SLEW:NEGATIVE', '.3f', 'AuxParametersLabel_TSlewNeg', 'AuxParameters_TSlewNeg', 'S', 'S'),
          )
        },
    ('Dynamic', 'Current', 'Toggle'):
        {'widgets': None,
         'mode_name': 'CURRENT',
         'params': (
            ('TRANSIENT:IRANGE',          'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',          'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',            'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL',        '.3f', 'MainParametersLabel_ALevelC', 'MainParameters_ALevelC', 0, 'C'),
            ('TRANSIENT:BLEVEL',        '.3f', 'MainParametersLabel_BLevelC', 'MainParameters_BLevelC', 0, 'C'),
            ('TRANSIENT:SLEW:POSITIVE', '.3f', 'AuxParametersLabel_TSlewPos', 'AuxParameters_TSlewPos', 'S', 'S'),
            ('TRANSIENT:SLEW:NEGATIVE', '.3f', 'AuxParametersLabel_TSlewNeg', 'AuxParameters_TSlewNeg', 'S', 'S'),
          )
        },
    ('Dynamic', 'Power', 'Continuous'):
        {'widgets': None,
         'mode_name': 'POWER',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelP', 'MainParameters_ALevelP', 0, 'P'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelP', 'MainParameters_BLevelP', 0, 'P'),
            ('TRANSIENT:AWIDTH', '.6f', 'MainParametersLabel_AWidth',  'MainParameters_AWidth', 0.000040, 999),
            ('TRANSIENT:BWIDTH', '.6f', 'MainParametersLabel_BWidth',  'MainParameters_BWidth', 0.000040, 999),
          )
        },
    ('Dynamic', 'Power', 'Pulse'):
        {'widgets': None,
         'mode_name': 'POWER',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelP', 'MainParameters_ALevelP', 0, 'P'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelP', 'MainParameters_BLevelP', 0, 'P'),
            ('TRANSIENT:BWIDTH', '.6f', 'MainParametersLabel_Width',   'MainParameters_Width', 0.000040, 999),
          )
        },
    ('Dynamic', 'Power', 'Toggle'):
        {'widgets': None,
         'mode_name': 'POWER',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelP', 'MainParameters_ALevelP', 0, 'P'),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelP', 'MainParameters_BLevelP', 0, 'P'),
          )
        },
    ('Dynamic', 'Resistance', 'Continuous'):
        {'widgets': None,
         'mode_name': 'RESISTANCE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelR', 'MainParameters_ALevelR', 0.030, 10000),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelR', 'MainParameters_BLevelR', 0.030, 10000),
            ('TRANSIENT:AWIDTH', '.3f', 'MainParametersLabel_AWidth',  'MainParameters_AWidth', 0.001, 999),
            ('TRANSIENT:BWIDTH', '.3f', 'MainParametersLabel_BWidth',  'MainParameters_BWidth', 0.001, 999),
          )
        },
    ('Dynamic', 'Resistance', 'Pulse'):
        {'widgets': None,
         'mode_name': 'RESISTANCE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelR', 'MainParameters_ALevelR', 0.030, 10000),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelR', 'MainParameters_BLevelR', 0.030, 10000),
            ('TRANSIENT:BWIDTH', '.3f', 'MainParametersLabel_Width',   'MainParameters_Width', 0.001, 999),
          )
        },
    ('Dynamic', 'Resistance', 'Toggle'):
        {'widgets': None,
         'mode_name': 'RESISTANCE',
         'params': (
            ('TRANSIENT:IRANGE',   'r', None, 'Range_Current_.*'),
            ('TRANSIENT:VRANGE',   'r', None, 'Range_Voltage_.*'),
            ('TRANSIENT:MODE',     'r', None, 'Dynamic_Mode_.*'),
            ('TRANSIENT:ALEVEL', '.3f', 'MainParametersLabel_ALevelR', 'MainParameters_ALevelR', 0.030, 10000),
            ('TRANSIENT:BLEVEL', '.3f', 'MainParametersLabel_BLevelR', 'MainParameters_BLevelR', 0.030, 10000),
          )
        },
    ('OCPT', None):
        {'widgets': None,
         'mode_name': 'OCP',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('VOLTAGE',    '.3f', 'MainParametersLabel_OCPV', 'MainParameters_OCPV', 0, 'V'),
            ('START',      '.3f', 'MainParametersLabel_OCPStart', 'MainParameters_OCPStart', 0, 'W:MainParameters_OCPEnd'),
            ('END',        '.3f', 'MainParametersLabel_OCPEnd', 'MainParameters_OCPEnd', 'W:MainParameters_OCPStart', 'C'),
            ('STEP',       '.3f', 'MainParametersLabel_OCPStep', 'MainParameters_OCPStep', 0, 'C'),
            ('STEP:DELAY', '.3f', 'MainParametersLabel_OCPDelay', 'MainParameters_OCPDelay', 0.001, 999),
            ('MIN',        '.3f', 'AuxParametersLabel_OCPMIN', 'AuxParameters_OCPMIN', 0, 'W:AuxParameters_OCPMAX'),
            ('MAX',        '.3f', 'AuxParametersLabel_OCPMAX', 'AuxParameters_OCPMAX', 'W:AuxParameters_OCPMIN', 'C'),
          )
        },
    ('OPPT', None):
        {'widgets': None,
         'mode_name': 'OPP',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('VOLTAGE',    '.3f', 'MainParametersLabel_OPPV', 'MainParameters_OPPV', 0, 'V'),
            ('START',      '.2f', 'MainParametersLabel_OPPStart', 'MainParameters_OPPStart', 0, 'W:MainParameters_OPPEnd'),
            ('END',        '.2f', 'MainParametersLabel_OPPEnd', 'MainParameters_OPPEnd', 'W:MainParameters_OPPStart', 'P'),
            ('STEP',       '.2f', 'MainParametersLabel_OPPStep', 'MainParameters_OPPStep', 0, 'P'),
            ('STEP:DELAY', '.3f', 'MainParametersLabel_OPPDelay', 'MainParameters_OPPDelay', 0.001, 999),
            ('MIN',        '.3f', 'AuxParametersLabel_OPPMIN', 'AuxParameters_OPPMIN', 0, 'W:AuxParameters_OPPMAX'),
            ('MAX',        '.3f', 'AuxParametersLabel_OPPMAX', 'AuxParameters_OPPMAX', 'W:AuxParameters_OPPMIN', 'P'),
          )
        },
    ('Ext \u26A0', 'Voltage'):
        {'widgets': None,
         'mode_name': 'EXT',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
          )
        },
    ('Ext \u26A0', 'Current'):
        {'widgets': None,
         'mode_name': 'EXT',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
          )
        },
    ('List', 'Voltage'):
        {'widgets': None,
         'mode_name': 'LIST',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('STEP',         'd', 'MainParametersLabel_ListSteps', 'MainParameters_ListSteps', 1, 100),
            ('COUNT',        'd', 'MainParametersLabel_ListCount', 'MainParameters_ListCount', 0, 255),
          )
        },
    ('List', 'Current'):
        {'widgets': None,
         'mode_name': 'LIST',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('STEP',         'd', 'MainParametersLabel_ListSteps', 'MainParameters_ListSteps', 1, 100),
            ('COUNT',        'd', 'MainParametersLabel_ListCount', 'MainParameters_ListCount', 0, 255),
          )
        },
    ('List', 'Power'):
        {'widgets': None,
         'mode_name': 'LIST',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('STEP',         'd', 'MainParametersLabel_ListSteps', 'MainParameters_ListSteps', 1, 100),
            ('COUNT',        'd', 'MainParametersLabel_ListCount', 'MainParameters_ListCount', 0, 255),
          )
        },
    ('List', 'Resistance'):
        {'widgets': None,
         'mode_name': 'LIST',
         'params': (
            ('IRANGE',       'r', None, 'Range_Current_.*'),
            ('VRANGE',       'r', None, 'Range_Voltage_.*'),
            ('STEP',         'd', 'MainParametersLabel_ListSteps', 'MainParameters_ListSteps', 1, 100),
            ('COUNT',        'd', 'MainParametersLabel_ListCount', 'MainParameters_ListCount', 0, 255),
          )
        },
    ('Program', None):
        {'widgets': None,
         'mode_name': 'LIST',
         'params': (
          )
        },
}


# This class encapsulates the main SDL configuration widget.

class InstrumentSiglentSDL1000ConfigureWidget(ConfigureWidgetBase):
    def __init__(self, *args, **kwargs):
        # The current state of all SCPI parameters. String values are always stored
        # in upper case!
        self._param_state = {}

        self._cur_overall_mode = None # e.g. Basic, Dynamic, LED
        self._cur_const_mode = None   # e.g. Voltage, Current, Power, Resistance
        self._cur_dynamic_mode = None # e.g. Continuous, Pulse, Toggle

        # List mode parameters
        self._list_mode_levels = None
        self._list_mode_widths = None
        self._list_mode_slews = None

        # We have to fake the progress of the steps in List mode because there is no
        # SCPI command to find out what step we are currently on, so we do it by
        # looking at the elapsed time and hope the instrument and the computer stay
        # roughly synchronized. But if they fall out of sync there's no way to get
        # them back in sync except starting the List sequence over.

        # The time the most recent List step was started
        self._list_mode_running = False
        self._list_mode_cur_step_start_time = None
        self._list_mode_cur_step_num = None
        self._list_mode_stopping = False

        # Stored measurements and triggers
        self._cached_measurements = None
        self._cached_triggers = None

        # Used to enable or disable measurement of parameters to speed up
        # data acquisition.
        self._enable_measurement_v = True
        self._enable_measurement_c = True
        self._enable_measurement_p = True
        self._enable_measurement_r = True
        self._enable_measurement_trise = False
        self._enable_measurement_tfall = False

        # Needed to prevent recursive calls when setting a widget's value invokes
        # the callback handler for it.
        self._disable_callbacks = False

        # The time the LOAD was turned on and off. Used for battery discharge logging.
        self._load_on_time = None
        self._load_off_time = None
        self._show_batt_report = False
        self._reset_batt_log()

        # We need to call this later because some things called by __init__ rely
        # on the above variables being initialized.
        super().__init__(*args, **kwargs)

        # Timer used to follow along with List mode
        self._list_mode_timer = QTimer(self._main_window.app)
        self._list_mode_timer.timeout.connect(self._update_heartbeat)
        self._list_mode_timer.setInterval(250)
        self._list_mode_timer.start()

    ######################
    ### Public methods ###
    ######################

    # This reads instrument -> _param_state
    def refresh(self):
        """Read all parameters from the instrument and set our internal state to match."""
        self._param_state = {} # Start with a blank slate
        for mode, info in _SDL_MODE_PARAMS.items():
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
                        val = val.upper()
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

        # Special read of the List Mode parameters
        self._update_list_mode_from_instrument()

        if self._param_state[':TRIGGER:SOURCE'] == 'MANUAL':
            # No point in using the SDL's panel when the button isn't available
            new_param_state = {':TRIGGER:SOURCE': 'BUS'}
            self._update_param_state_and_inst(new_param_state)

        # Set things like _cur_overall_mode and _cur_const_mode and update widgets
        self._update_state_from_param_state()

    # This writes _param_state -> instrument (opposite of refresh)
    def update_instrument(self):
        """Update the instrument with the current _param_state.

        This is tricker than it should be, because if you send a configuration
        command to the SDL for a mode it's not currently in, it crashes!"""
        set_params = set()
        first_list_mode_write = True
        for mode, info in _SDL_MODE_PARAMS.items():
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
            if info['mode_name'] == 'LIST' and first_list_mode_write:
                first_list_mode_write = False
                # Special write of the List Mode parameters
                steps = self._param_state[':LIST:STEP']
                for i in range(1, steps+1):
                    self._inst.write(f':LIST:LEVEL {i},{self._list_mode_levels[i-1]:.3f}')
                    self._inst.write(f':LIST:WIDTH {i},{self._list_mode_widths[i-1]:.3f}')
                    self._inst.write(f':LIST:SLEW {i},{self._list_mode_slews[i-1]:.3f}')

        self._update_state_from_param_state()
        self._put_inst_in_mode(self._cur_overall_mode, self._cur_const_mode)

    def update_measurements_and_triggers(self, read_inst=True):
        """Read current values, update control panel display, return the values."""
        # Update the load on/off state in case we hit a protection limit
        input_state = 0
        if read_inst:
            input_state = int(self._inst.query(':INPUT:STATE?'))
            if self._param_state[':INPUT:STATE'] != input_state:
                # No need to update the instrument, since it changed the state for us
                self._update_load_state(input_state, update_inst=False)

        measurements = {}
        triggers = {}

        triggers['LoadOn'] = {'name': 'Load On',
                              'val':  bool(input_state)}
        triggers['ListRunning'] = {'name': 'List Mode Running',
                                   'val':  bool(self._list_mode_running)}

        voltage = None
        if read_inst:
            w = self._widget_registry['MeasureV']
            if self._enable_measurement_v:
                # Voltage is available regardless of the input state
                voltage = self._inst.measure_voltage()
                w.setText(f'{voltage:10.6f} V')
            else:
                w.setText('---   V')
        measurements['Voltage'] = {'name':   'Voltage',
                                   'unit':   'V',
                                   'format': '10.6f',
                                   'val':    voltage}

        current = None
        if read_inst:
            w = self._widget_registry['MeasureC']
            if self._enable_measurement_c:
                # Current is only available when the load is on
                if not input_state:
                    w.setText('N/A   A')
                else:
                    current = self._inst.measure_current()
                    w.setText(f'{current:10.6f} A')
            else:
                w.setText('---   A')
        measurements['Current'] = {'name':   'Current',
                                   'unit':   'A',
                                   'format': '10.6f',
                                   'val':    current}

        power = None
        if read_inst:
            w = self._widget_registry['MeasureP']
            if self._enable_measurement_p:
                # Power is only available when the load is on
                if not input_state:
                    w.setText('N/A   W')
                else:
                    power = self._inst.measure_power()
                    w.setText(f'{power:10.6f} W')
            else:
                w.setText('---   W')
        measurements['Power'] = {'name':   'Power',
                                 'unit':   'W',
                                 'format': '10.6f',
                                 'val':    power}

        resistance = None
        if read_inst:
            w = self._widget_registry['MeasureR']
            if self._enable_measurement_r:
                # Resistance is only available when the load is on
                if not input_state:
                    w.setText('N/A   \u2126')
                else:
                    resistance = self._inst.measure_resistance()
                    if resistance < 10:
                        fmt = '%8.6f'
                    elif resistance < 100:
                        fmt = '%8.5f'
                    elif resistance < 1000:
                        fmt = '%8.4f'
                    elif resistance < 10000:
                        fmt = '%8.3f'
                    elif resistance < 100000:
                        fmt = '%8.2f'
                    else:
                        fmt = '%8.1f'
                    w.setText(f'{fmt} \u2126' % resistance)
            else:
                w.setText('---   \u2126')
        measurements['Resistance'] = {'name':   'Resistance',
                                      'unit':   '\u2126',
                                      'format': '13.6f',
                                      'val':    resistance}

        trise = None
        if read_inst:
            w = self._widget_registry['MeasureTRise']
            if self._enable_measurement_trise:
                # Trise is only available when the load is on
                if not input_state:
                    w.setText('TRise:   N/A   s')
                else:
                    trise = self._inst.measure_trise()
                    w.setText(f'TRise: {trise:7.3f} s')
            else:
                w.setText('TRise:   ---   s')
        measurements['TRise'] = {'name':   'TRise',
                                 'unit':   's',
                                 'format': '7.3f',
                                 'val':    trise}

        tfall = None
        if read_inst:
            w = self._widget_registry['MeasureTFall']
            if self._enable_measurement_tfall:
                # Tfall is only available when the load is on
                if not input_state:
                    w.setText('TFall:   N/A   s')
                else:
                    tfall = self._inst.measure_tfall()
                    w.setText(f'TFall: {tfall:7.3f} s')
            else:
                w.setText('TFall:   ---   s')
        measurements['TFall'] = {'name':   'TFall',
                                 'unit':   's',
                                 'format': '7.3f',
                                 'val':    tfall}

        disch_time = None
        disch_cap = None
        add_cap = None
        total_cap = None
        if read_inst:
            if self._cur_overall_mode == 'Battery':
                # Battery measurements are available regardless of load state
                if self._batt_log_initial_voltage is None:
                    self._batt_log_initial_voltage = voltage
                disch_time = self._inst.measure_battery_time()
                m, s = divmod(disch_time, 60)
                h, m = divmod(m, 60)
                w = self._widget_registry['MeasureBattTime']
                w.setText(f'{int(h):02d}:{int(m):02d}:{int(s):02}')

                w = self._widget_registry['MeasureBattCap']
                disch_cap = self._inst.measure_battery_capacity()
                w.setText(f'{disch_cap:7.3f} Ah')

                w = self._widget_registry['MeasureBattAddCap']
                add_cap = self._inst.measure_battery_add_capacity()
                w.setText(f'Addl Cap: {add_cap:7.3f} Ah')

                # When the LOAD is OFF, we have already updated the ADDCAP to include the
                # current test results, so we don't want to add it in a second time
                if input_state:
                    total_cap = disch_cap+add_cap
                else:
                    total_cap = add_cap
                w = self._widget_registry['MeasureBattTotalCap']
                w.setText(f'Total Cap: {total_cap:7.3f} Ah')
        measurements['Discharge Time'] = {'name':   'Batt Dischg Time',
                                          'unit':   's',
                                          'format': '8d',
                                          'val':    disch_time}
        measurements['Capacity'] =       {'name':   'Batt Capacity', # noqa: E222
                                          'unit':   'Ah',
                                          'format': '7.3f',
                                          'val':    disch_cap}
        measurements['Addl Capacity'] =  {'name':   'Batt Addl Cap', # noqa: E222
                                          'unit':   'Ah',
                                          'format': '7.3f',
                                          'val':    add_cap}
        measurements['Total Capacity'] = {'name':   'Batt Total Cap',
                                          'unit':   'Ah',
                                          'format': '7.3f',
                                          'val':    total_cap}

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

        self._menubar_device.addSeparator()
        action = QAction('Show last &battery report...', self)
        action.setShortcut(QKeySequence('Ctrl+B'))
        action.triggered.connect(self._menu_do_device_batt_report)
        self._menubar_device.addAction(action)

        ### Add to View menu

        action = QAction('&Parameters', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+1'))
        action.setChecked(True)
        action.triggered.connect(self._menu_do_view_parameters)
        self._menubar_view.addAction(action)
        action = QAction('&Global Parameters', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+2'))
        action.setChecked(False)
        action.triggered.connect(self._menu_do_view_global_parameters)
        self._menubar_view.addAction(action)
        action = QAction('&Load and Trigger', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+3'))
        action.setChecked(True)
        action.triggered.connect(self._menu_do_view_load_trigger)
        self._menubar_view.addAction(action)
        action = QAction('&Measurements', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+4'))
        action.setChecked(True)
        action.triggered.connect(self._menu_do_view_measurements)
        self._menubar_view.addAction(action)

        ### Set up configuration window widgets

        main_vert_layout = QVBoxLayout(toplevel_widget)
        main_vert_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        ###### ROW 1 - Modes and Paramter Values ######

        w = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row_layout)
        main_vert_layout.addWidget(w)
        self._widget_registry['ParametersRow'] = w

        ### ROW 1, COLUMN 1 ###

        # Overall mode: Basic, Dynamic, LED, Battery, List, Program, OCPT, OPPT
        layouts = QVBoxLayout()
        row_layout.addLayout(layouts)
        frame = QGroupBox('Mode')
        self._widget_registry['FrameMode'] = frame
        frame.setStyleSheet(_STYLE_SHEET['FrameMode'][self._style_env])
        layouts.addWidget(frame)
        layouth = QHBoxLayout(frame)
        layoutv = QVBoxLayout()
        layoutv.setSpacing(0)
        layouth.addLayout(layoutv)
        bg = QButtonGroup(layouts)
        # Left column
        for mode in ('Basic', 'LED', 'Battery', 'OCPT', 'OPPT', 'Ext \u26A0'):
            rb = QRadioButton(mode)
            layoutv.addWidget(rb)
            bg.addButton(rb)
            rb.button_group = bg
            rb.wid = mode
            rb.toggled.connect(self._on_click_overall_mode)
            self._widget_registry['Overall_'+mode] = rb
        layoutv.addStretch()
        # Right column
        layoutv = QVBoxLayout()
        layoutv.setSpacing(2)
        layouth.addLayout(layoutv)
        for mode in ('Dynamic', 'List'): # , 'Program'):
            rb = QRadioButton(mode)
            layoutv.addWidget(rb)
            bg.addButton(rb)
            rb.button_group = bg
            rb.wid = mode
            rb.toggled.connect(self._on_click_overall_mode)
            self._widget_registry['Overall_'+mode] = rb
            if mode == 'Dynamic':
                bg2 = QButtonGroup(layouts)
                for mode in ('Continuous', 'Pulse', 'Toggle'):
                    rb = QRadioButton(mode)
                    rb.setStyleSheet('padding-left: 1.4em;') # Indent
                    layoutv.addWidget(rb)
                    bg2.addButton(rb)
                    rb.button_group = bg
                    rb.wid = mode
                    rb.toggled.connect(self._on_click_dynamic_mode)
                    self._widget_registry['Dynamic_Mode_'+mode] = rb
        layoutv.addStretch()

        ### ROW 1, COLUMN 2 ###

        # Mode radio buttons: CV, CC, CP, CR
        frame = QGroupBox('Constant')
        self._widget_registry['FrameConstant'] = frame
        frame.setStyleSheet(_STYLE_SHEET['FrameMode'][self._style_env])
        row_layout.addWidget(frame)
        layoutv = QVBoxLayout(frame)
        bg = QButtonGroup(layouts)
        for mode in ('Voltage', 'Current', 'Power', 'Resistance'):
            rb = QRadioButton(mode)
            bg.addButton(rb)
            rb.button_group = bg
            rb.wid = mode
            rb.sizePolicy().setRetainSizeWhenHidden(True)
            rb.toggled.connect(self._on_click_const_mode)
            self._widget_registry['Const_'+mode] = rb
            layoutv.addWidget(rb)

        ### ROW 1, COLUMN 3 ###

        # Main Parameters
        frame = self._init_widgets_value_box(
            'Main Parameters', (
                ('Voltage',     'Voltage',      'V',      'LEVEL:IMMEDIATE'),
                ('Current',     'Current',      'A',      'LEVEL:IMMEDIATE'),
                ('Power',       'Power',        'W',      'LEVEL:IMMEDIATE'),
                ('Resistance',  'Resistance',   '\u2126', 'LEVEL:IMMEDIATE'),
                ('A Level',     'ALevelV',      'V',      'TRANSIENT:ALEVEL'),
                ('B Level',     'BLevelV',      'V',      'TRANSIENT:BLEVEL'),
                ('A Level',     'ALevelC',      'A',      'TRANSIENT:ALEVEL'),
                ('B Level',     'BLevelC',      'A',      'TRANSIENT:BLEVEL'),
                ('A Level',     'ALevelP',      'W',      'TRANSIENT:ALEVEL'),
                ('B Level',     'BLevelP',      'W',      'TRANSIENT:BLEVEL'),
                ('A Level',     'ALevelR',      '\u2126', 'TRANSIENT:ALEVEL'),
                ('B Level',     'BLevelR',      '\u2126', 'TRANSIENT:BLEVEL'),
                ('A Width',     'AWidth',       's',      'TRANSIENT:AWIDTH'),
                ('B Width',     'BWidth',       's',      'TRANSIENT:BWIDTH'),
                ('Width',       'Width',        's',      'TRANSIENT:BWIDTH'),
                ('Vo',          'LEDV',         'V',      'VOLTAGE'),
                ('Io',          'LEDC',         'A',      'CURRENT'),
                ('Rco',         'LEDR',         None,     'RCONF'),
                ('Time VLow',   'TimeVLow',     'V',      ':TIME:TEST:VOLTAGE:LOW'),
                ('Time VHi',    'TimeVHigh',    'V',      ':TIME:TEST:VOLTAGE:HIGH'),
                ('Current',     'BattC',        'A',      'LEVEL'),
                ('Power',       'BattP',        'W',      'LEVEL'),
                ('Resistance',  'BattR',        '\u2126', 'LEVEL'),
                ('*V Stop',     'BattVStop',    'V',      ('VOLTAGE',
                                                           'VOLTAGE:STATE')),
                ('*Cap Stop',   'BattCapStop',  'mAh',    ('CAP',
                                                           'CAP:STATE')),
                ('*Time Stop',  'BattTimeStop', 's',      ('TIMER',
                                                           'TIMER:STATE')),
                ('Von',         'OCPV',         'V',      'VOLTAGE'),
                ('I Start',     'OCPStart',     'A',      'START'),
                ('I End',       'OCPEnd',       'A',      'END'),
                ('I Step',      'OCPStep',      'A',      'STEP'),
                ('Step Delay',  'OCPDelay',     's',      'STEP:DELAY'),
                ('Prot V',      'OPPV',         'V',      'VOLTAGE'),
                ('P Start',     'OPPStart',     'W',      'START'),
                ('P End',       'OPPEnd',       'W',      'END'),
                ('P Step',      'OPPStep',      'W',      'STEP'),
                ('Step Delay',  'OPPDelay',     's',      'STEP:DELAY'),
                ('# Steps',     'ListSteps',    None,     'STEP'),
                ('@Run Count',  'ListCount',    None,     'COUNT')))
        frame.setStyleSheet(_STYLE_SHEET['MainParams'][self._style_env])
        row_layout.addWidget(frame)

        ### ROW 1, COLUMN 4 ###

        # V/I/R Range selections and Aux Parameters
        layouts = QVBoxLayout()
        layouts.setSpacing(0)
        row_layout.addLayout(layouts)

        # V/I/R Range selections
        frame = QGroupBox('Range')
        self._widget_registry['FrameRange'] = frame
        layouts.addWidget(frame)
        layout = QGridLayout(frame)
        layout.setSpacing(0)
        for row_num, (mode, ranges) in enumerate((('Voltage', ('36V', '150V')),
                                                  ('Current', ('5A', '30A')))):
            layout.addWidget(QLabel(mode+':'), row_num, 0)
            bg = QButtonGroup(layout)
            for col_num, range_name in enumerate(ranges):
                rb = QRadioButton(range_name)
                bg.addButton(rb)
                rb.button_group = bg
                rb.wid = range_name
                rb.toggled.connect(self._on_click_range)
                if len(ranges) == 1:
                    layout.addWidget(rb, row_num, col_num+1, 1, 2)
                else:
                    layout.addWidget(rb, row_num, col_num+1)
                self._widget_registry['Range_'+mode+'_'+range_name.strip('VA')] = rb

        # Aux Parameters
        frame = self._init_widgets_value_box(
            'Aux Parameters', (
                ('Slew (rise)', 'BSlewPos', 'A/\u00B5s', 'SLEW:POSITIVE'),
                ('Slew (fall)', 'BSlewNeg', 'A/\u00B5s', 'SLEW:NEGATIVE'),
                ('Slew (rise)', 'TSlewPos', 'A/\u00B5s', 'TRANSIENT:SLEW:POSITIVE'),
                ('Slew (fall)', 'TSlewNeg', 'A/\u00B5s', 'TRANSIENT:SLEW:NEGATIVE'),
                ('I Min',       'OCPMIN',   'A',         'MIN'),
                ('I Max',       'OCPMAX',   'A',         'MAX'),
                ('P Min',       'OPPMIN',   'W',         'MIN'),
                ('P Max',       'OPPMAX',   'W',         'MAX')))
        frame.setStyleSheet(_STYLE_SHEET['AuxParams'][self._style_env])
        layouts.addWidget(frame)

        ###################

        ###### ROW 2 - DEVICE PARAMETERS ######

        w = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row_layout)
        main_vert_layout.addWidget(w)
        self._widget_registry['GlobalParametersRow'] = w
        w.hide()

        ### ROW 2, COLUMN 1 ###

        frame = QGroupBox('Global Parameters')
        row_layout.addWidget(frame)
        layouth = QHBoxLayout(frame)

        # Left column
        layoutv = QVBoxLayout()
        layouth.addLayout(layoutv)
        w = QCheckBox('External Voltage Sense')
        self._widget_registry['ExternalVoltageSense'] = w
        w.clicked.connect(self._on_click_ext_voltage_sense)
        layoutv.addWidget(w)
        w = QCheckBox('External Current Monitor')
        self._widget_registry['EnableIMonitor'] = w
        w.clicked.connect(self._on_click_imonitor)
        layoutv.addWidget(w)
        w = QCheckBox('External Voltage Monitor')
        self._widget_registry['EnableVMonitor'] = w
        w.clicked.connect(self._on_click_vmonitor)
        layoutv.addWidget(w)
        w = QCheckBox('External Load On/Off \u26A0')
        self._widget_registry['ExternalInputState'] = w
        w.clicked.connect(self._on_click_ext_input_state)
        layoutv.addWidget(w)
        layoutv.addStretch()
        layouth.addStretch()

        # Middle column
        layoutv = QVBoxLayout()
        layouth.addLayout(layoutv)
        _ = self._init_widgets_value_box(
            'Global Parameters', (
                ('*Current Protection Level', 'CurrentProtL', 'A', (':CURRENT:PROTECTION:LEVEL',
                                                                    ':CURRENT:PROTECTION:STATE')),
                ('Current Protection Delay',  'CurrentProtD', 's', ':CURRENT:PROTECTION:DELAY'),
                ('*Power Protection Level',   'PowerProtL',   'W', (':POWER:PROTECTION:LEVEL',
                                                                    ':POWER:PROTECTION:STATE')),
                ('Power Protection Delay',    'PowerProtD',   's', ':POWER:PROTECTION:DELAY'),
            ), layout=layoutv)
        ss = """QDoubleSpinBox { min-width: 5em; max-width: 5em; }
             """
        frame.setStyleSheet(ss)
        layoutv.addStretch()
        layouth.addStretch()

        # Right column
        layoutv = QVBoxLayout()
        layouth.addLayout(layoutv)
        _ = self._init_widgets_value_box(
            'Global Parameters', (
                ('Average Count',             'AvgCount',     None, ':SENSE:AVERAGE:COUNT'),
                ('Breakover Voltage',         'BreakoverV',    'V', ':VOLTAGE:LEVEL:ON'),
            ), layout=layoutv)
        w = QCheckBox('Breakover Voltage Latch')
        self._widget_registry['BreakoverVoltageLatch'] = w
        w.clicked.connect(self._on_click_breakover_voltage_latch)
        layoutv.addWidget(w)
        layoutv.addStretch()
        layouth.addStretch()

        ###################

        ###### ROW 3 - LIST MODE ######

        w = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row_layout)
        main_vert_layout.addWidget(w)
        self._widget_registry['ListRow'] = w

        table = QTableView(alternatingRowColors=True)
        table.setModel(ListTableModel(self._on_list_table_change))
        row_layout.addWidget(table)
        table.setStyleSheet(_STYLE_SHEET['ListTable'][self._style_env])
        table.verticalHeader().setMinimumWidth(30)
        self._widget_registry['ListTable'] = table

        pw = pg.plot()
        # Disable zoom and pan
        pw.plotItem.setMouseEnabled(x=False, y=False)
        pw.plotItem.setMenuEnabled(False)
        self._list_mode_level_plot = pw.plot([], pen=0)
        self._list_mode_step_plot = pw.plot([], pen=1)
        size = _STYLE_SHEET['ListPlot'][self._style_env]
        pw.setMaximumSize(size[0], size[1]) # XXX Warning magic constants!
        row_layout.addWidget(pw)
        self._widget_registry['ListPlot'] = pw

        row_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        ###################

        ###### ROW 4 - PROGRAM MODE ######

        # w = QCheckBox('Stop on fail')
        # self._widget_registry['StopOnFail'] = w
        # w.clicked.connect(self._on_click_stop_on_fail)
        # layoutv.addWidget(w)

        ###### ROW 5 - SHORT/LOAD/TRIGGER ######

        w = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row_layout)
        main_vert_layout.addWidget(w)
        self._widget_registry['TriggerRow'] = w

        ###### ROW 5, COLUMN 1 - SHORT ######

        layoutv = QVBoxLayout()
        layoutv.setSpacing(0)
        row_layout.addLayout(layoutv)

        w = QPushButton('') # SHORT ON/OFF
        w.setEnabled(False) # Default to disabled since checkbox is unchecked
        w.clicked.connect(self._on_click_short_on_off)
        layoutv.addWidget(w)
        self._widget_registry['ShortONOFF'] = w
        layouth = QHBoxLayout()
        layoutv.addLayout(layouth)
        layouth.addStretch()
        w = QCheckBox('Enable short operation') # Enable short
        w.setChecked(False)
        w.clicked.connect(self._on_click_short_enable)
        layouth.addWidget(w)
        self._widget_registry['ShortONOFFEnable'] = w
        self._update_short_onoff_button(False) # Sets the style sheet
        layouth.addStretch()

        row_layout.addStretch()

        ###### ROW 5, COLUMN 2 - LOAD ######

        w = QPushButton('') # LOAD ON/OFF
        w.clicked.connect(self._on_click_load_on_off)
        shortcut = QShortcut(QKeySequence('Alt+L'), self)
        shortcut.activated.connect(self._on_click_load_on_off)
        row_layout.addWidget(w)
        self._widget_registry['LoadONOFF'] = w
        self._update_load_onoff_button(False) # Sets the style sheet

        row_layout.addStretch()

        ###### ROW 5, COLUMN 3 - TRIGGER ######

        layoutv = QVBoxLayout()
        layoutv.setSpacing(0)
        row_layout.addLayout(layoutv)
        bg = QButtonGroup(layoutv)
        rb = QRadioButton('SDL Panel')
        rb.mode = 'Manual'
        bg.addButton(rb)
        rb.button_group = bg
        rb.clicked.connect(self._on_click_trigger_source)
        layoutv.addWidget(rb)
        self._widget_registry['Trigger_Man'] = rb
        rb = QRadioButton('TRIG\u25CE \u279c')
        rb.setChecked(True)
        rb.mode = 'Bus'
        bg.addButton(rb)
        rb.button_group = bg
        rb.clicked.connect(self._on_click_trigger_source)
        layoutv.addWidget(rb)
        self._widget_registry['Trigger_Bus'] = rb
        rb = QRadioButton('External')
        rb.mode = 'External'
        bg.addButton(rb)
        rb.button_group = bg
        rb.clicked.connect(self._on_click_trigger_source)
        layoutv.addWidget(rb)
        self._widget_registry['Trigger_Ext'] = rb

        w = QPushButton('TRIG\u25CE')
        w.clicked.connect(self._on_click_trigger)
        shortcut = QShortcut(QKeySequence('Alt+T'), self)
        shortcut.activated.connect(self._on_click_trigger)
        w.setStyleSheet(_STYLE_SHEET['TriggerButton'][self._style_env])
        row_layout.addWidget(w)
        self._widget_registry['Trigger'] = w

        ###################

        ###### ROW 6 - MEASUREMENTS ######

        w = QWidget()
        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row_layout)
        main_vert_layout.addWidget(w)
        self._widget_registry['MeasurementsRow'] = w

        # Enable measurements, reset battery log button
        layoutv = QVBoxLayout()
        # layoutv.setSpacing(0)
        row_layout.addLayout(layoutv)
        layoutv.addWidget(QLabel('Enable measurements:'))
        layoutg = QGridLayout()
        layoutv.addLayout(layoutg)

        cb = QCheckBox('Voltage')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(True)
        cb.mode = 'V'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 0, 0)
        self._widget_registry['EnableV'] = cb

        cb = QCheckBox('Current')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(True)
        cb.mode = 'C'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 0, 1)
        self._widget_registry['EnableC'] = cb

        cb = QCheckBox('Power')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(True)
        cb.mode = 'P'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 1, 0)
        self._widget_registry['EnableP'] = cb

        cb = QCheckBox('Resistance')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(True)
        cb.mode = 'R'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 1, 1)
        self._widget_registry['EnableR'] = cb

        cb = QCheckBox('TRise')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(False)
        cb.mode = 'TR'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 2, 0)
        self._widget_registry['EnableTRise'] = cb

        cb = QCheckBox('TFall')
        cb.setStyleSheet('padding-left: 0.5em;') # Indent
        cb.setChecked(False)
        cb.mode = 'TF'
        cb.clicked.connect(self._on_click_enable_measurements)
        layoutg.addWidget(cb, 2, 1)
        self._widget_registry['EnableTFall'] = cb

        layoutv.addStretch()
        pb = QPushButton('Reset Addl Cap && Test Log')
        pb.clicked.connect(self._on_click_reset_batt_test)
        layoutv.addWidget(pb)
        self._widget_registry['ClearAddCap'] = pb
        layoutv.addStretch()

        row_layout.addStretch()

        # Main measurements widget
        container = QWidget()
        container.setStyleSheet('background: black;')
        row_layout.addStretch()
        row_layout.addWidget(container)

        ss = """font-size: 30px; font-weight: bold; font-family: "Courier New";
                min-width: 6.5em; color: yellow;
             """
        ss2 = """font-size: 15px; font-weight: bold; font-family: "Courier New";
                 min-width: 6.5em; color: yellow;
             """
        ss3 = """font-size: 30px; font-weight: bold; font-family: "Courier New";
                 min-width: 6.5em; color: red;
             """
        ss4 = """font-size: 15px; font-weight: bold; font-family: "Courier New";
                 min-width: 6.5em; color: red;
             """
        layout = QGridLayout(container)
        w = QLabel('---   V')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss)
        layout.addWidget(w, 0, 0)
        self._widget_registry['MeasureV'] = w
        w = QLabel('---   A')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss)
        layout.addWidget(w, 0, 1)
        self._widget_registry['MeasureC'] = w
        w = QLabel('---   W')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss)
        layout.addWidget(w, 1, 0)
        self._widget_registry['MeasureP'] = w
        w = QLabel('---   \u2126')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss)
        layout.addWidget(w, 1, 1)
        self._widget_registry['MeasureR'] = w

        w = QLabel('TRise:   ---   s')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss2)
        layout.addWidget(w, 2, 0)
        self._widget_registry['MeasureTRise'] = w
        w = QLabel('TFall:   ---   s')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss2)
        layout.addWidget(w, 2, 1)
        self._widget_registry['MeasureTFall'] = w

        w = QLabel('00:00:00')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss3)
        layout.addWidget(w, 3, 0)
        self._widget_registry['MeasureBattTime'] = w
        w = QLabel('---  mAh')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss3)
        layout.addWidget(w, 3, 1)
        self._widget_registry['MeasureBattCap'] = w
        w = QLabel('Addl Cap:    --- mAh')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss4)
        layout.addWidget(w, 4, 0)
        self._widget_registry['MeasureBattAddCap'] = w
        w = QLabel('Total Cap:    --- mAh')
        w.setAlignment(Qt.AlignmentFlag.AlignRight)
        w.setStyleSheet(ss4)
        layout.addWidget(w, 4, 1)
        self._widget_registry['MeasureBattTotalCap'] = w
        row_layout.addStretch()

    # Our general philosophy is to create all of the possible input widgets for all
    # parameters and all units, and then hide the ones we don't need.
    # The details structure contains a list of:
    #   - The string to display as the input label
    #   - The name of the parameter as used in the _SDL_MODE_PARAMS dictionary
    #   - The unit to display in the input edit field
    #   - The SCPI parameter name, which gets added as an attribute on the widget
    #     - If the SCPI parameter is a tuple (SCPI1, SCPI2), then the first is the
    #       main parameter, and the second is the associated "STATE" parameter that
    #       is set to 0 or 1 depending on whether the main parameter is zero or
    #       non-zero.
    #     - If the SCPI parameter starts with ":" then the current mode is not
    #       prepended during widget update.
    def _init_widgets_value_box(self, title, details, layout=None):
        """Set up spinboxes for one frame."""
        # Value for most modes
        widget_prefix = title.replace(' ', '')
        if layout is None:
            frame = QGroupBox(title)
            layoutv = QVBoxLayout(frame)
        else:
            frame = None
            layoutv = layout
        for (display, param_name, unit, scpi) in details:
            special_text = None
            if display[0] == '*':
                # Special indicator that "0" means "Disabled"
                special_text = 'Disabled'
                display = display[1:]
            if display[0] == '@':
                # Special indicator that "0" means "Infinite"
                special_text = 'Infinite'
                display = display[1:]
            layouth = QHBoxLayout()
            label = QLabel(display+':')
            layouth.addWidget(label)
            input = MultiSpeedSpinBox(1.)
            input.wid = (param_name, scpi)
            if special_text:
                input.setSpecialValueText(special_text)
            input.setAlignment(Qt.AlignmentFlag.AlignRight)
            input.setDecimals(3)
            input.setAccelerated(True)
            if unit is not None:
                input.setSuffix(' '+unit)
            input.editingFinished.connect(self._on_value_change)
            layouth.addWidget(input)
            label.sizePolicy().setRetainSizeWhenHidden(True)
            input.sizePolicy().setRetainSizeWhenHidden(True)
            layoutv.addLayout(layouth)
            input.registry_name = f'{widget_prefix}_{param_name}'
            self._widget_registry[input.registry_name] = input
            self._widget_registry[f'{widget_prefix}Label_{param_name}'] = label
        if frame is not None:
            layoutv.addStretch()
            self._widget_registry[f'Frame{widget_prefix}'] = frame
        return frame

    ############################################################################
    ### Action and Callback Handlers
    ############################################################################

    def _menu_do_about(self):
        """Show the About box."""
        supported = ', '.join(self._inst.supported_instruments())
        msg = f"""Siglent SDL1000-series instrument interface.

Copyright 2022, Robert S. French.

Supported instruments: {supported}.

Connected to {self._inst.resource_name}
    {self._inst.model}
    S/N {self._inst.serial_number}
    FW {self._inst.firmware_version}"""

        QMessageBox.about(self, 'About', msg)

    def _menu_do_save_configuration(self):
        """Save the current configuration to a file."""
        fn = QFileDialog.getSaveFileName(self, caption='Save Configuration',
                                         filter='All (*.*);;SDL Configuration (*.sdlcfg)',
                                         initialFilter='SDL Configuration (*.sdlcfg)')
        fn = fn[0]
        if not fn:
            return
        ps = self._param_state.copy()
        # Add the List mode parameters as fake SCPI commands
        step = ps[':LIST:STEP']
        for i in range(step):
            ps[f':LIST:LEVEL {i+1}'] = self._list_mode_levels[i]
            ps[f':LIST:WIDTH {i+1}'] = self._list_mode_widths[i]
            ps[f':LIST:SLEW {i+1}'] = self._list_mode_slews[i]
        with open(fn, 'w') as fp:
            json.dump(ps, fp, sort_keys=True, indent=4)

    def _menu_do_load_configuration(self):
        """Load the current configuration from a file."""
        fn = QFileDialog.getOpenFileName(self, caption='Load Configuration',
                                         filter='All (*.*);;SDL Configuration (*.sdlcfg)',
                                         initialFilter='SDL Configuration (*.sdlcfg)')
        fn = fn[0]
        if not fn:
            return
        with open(fn, 'r') as fp:
            ps = json.load(fp)
        # Retrieve the List mode parameters
        step = ps[':LIST:STEP']
        self._list_mode_levels = []
        self._list_mode_widths = []
        self._list_mode_slews = []
        for i in range(step):
            cmd = f':LIST:LEVEL {i+1}'
            self._list_mode_levels.append(ps[cmd])
            del ps[cmd]
            cmd = f':LIST:WIDTH {i+1}'
            self._list_mode_widths.append(ps[cmd])
            del ps[cmd]
            cmd = f':LIST:SLEW {i+1}'
            self._list_mode_slews.append(ps[cmd])
            del ps[cmd]
        self._param_state = ps
        # Clean up the param state. We don't want to start with the load or short on.
        self._param_state['SYST:REMOTE:STATE'] = 1
        self._update_load_state(0)
        self._param_state['INPUT:STATE'] = 0
        self._update_short_state(0)
        self._param_state['SHORT:STATE'] = 0
        if self._param_state[':TRIGGER:SOURCE'] == 'Manual':
            # No point in using the SDL's panel when the button isn't available
            self._param_state[':TRIGGER:SOURCE'] = 'Bus'
        self.update_instrument()

    def _menu_do_reset_device(self):
        """Reset the instrument and then reload the state."""
        # A reset takes around 6.75 seconds, so we wait up to 10s to be safe.
        self.setEnabled(False)
        self.repaint()
        self._inst.write('*RST', timeout=10000)
        self.refresh()
        self.setEnabled(True)

    def _menu_do_device_batt_report(self):
        """Produce the battery discharge report, if any, and display it in a dialog."""
        report = self._batt_log_report()
        if report is None:
            report = 'No current battery log.'
        dialog = PrintableTextDialog('Battery Discharge Report', report)
        dialog.exec()

    def _menu_do_view_parameters(self, state):
        """Toggle visibility of the parameters row."""
        if state:
            self._widget_registry['ParametersRow'].show()
            if self._cur_overall_mode == 'List':
                self._widget_registry['ListRow'].show()
        else:
            self._widget_registry['ParametersRow'].hide()
            self._widget_registry['ListRow'].hide()

    def _menu_do_view_global_parameters(self, state):
        """Toggle visibility of the global parameters row."""
        if state:
            self._widget_registry['GlobalParametersRow'].show()
        else:
            self._widget_registry['GlobalParametersRow'].hide()

    def _menu_do_view_load_trigger(self, state):
        """Toggle visibility of the short/load/trigger row."""
        if state:
            self._widget_registry['TriggerRow'].show()
        else:
            self._widget_registry['TriggerRow'].hide()

    def _menu_do_view_measurements(self, state):
        """Toggle visibility of the measurements row."""
        if state:
            self._widget_registry['MeasurementsRow'].show()
        else:
            self._widget_registry['MeasurementsRow'].hide()

    def _on_click_overall_mode(self):
        """Handle clicking on an Overall Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        self._cur_overall_mode = rb.wid
        self._cur_dynamic_mode = None
        new_param_state = {}
        new_param_state[':EXT:MODE'] = 'INT'  # Overridden by 'Ext' below
        # Special handling for each button
        match self._cur_overall_mode:
            case 'Basic':
                self._cur_const_mode = self._param_state[':FUNCTION'].title()
                if self._cur_const_mode in ('Led', 'OCP', 'OPP'):
                    # LED is weird in that the instrument treats it as a BASIC mode
                    # but there's no CV/CC/CP/CR choice.
                    # We lose information going from OCP/OPP back to Basic because
                    # we don't know which basic mode we were in before!
                    self._cur_const_mode = 'Voltage' # For lack of anything else to do
                # Force update since this does more than set a parameter - it switches
                # modes
                self._param_state[':FUNCTION'] = None
                new_param_state[':FUNCTION'] = self._cur_const_mode.upper()
                self._param_state[':FUNCTION:MODE'] = 'BASIC'
            case 'Dynamic':
                self._cur_const_mode = (
                    self._param_state[':FUNCTION:TRANSIENT'].title())
                # Dynamic also has sub-modes - Continuous, Pulse, Toggle
                param_info = self._cur_mode_param_info(null_dynamic_mode_ok=True)
                mode_name = param_info['mode_name']
                self._cur_dynamic_mode = (
                    self._param_state[f':{mode_name}:TRANSIENT:MODE'].title())
                # Force update since this does more than set a parameter - it switches
                # modes
                self._param_state[':FUNCTION:TRANSIENT'] = None
                new_param_state[':FUNCTION:TRANSIENT'] = self._cur_const_mode.upper()
                self._param_state[':FUNCTION:MODE'] = 'TRAN'
            case 'LED':
                # Force update since this does more than set a parameter - it switches
                # modes
                self._param_state[':FUNCTION'] = None
                new_param_state[':FUNCTION'] = 'LED' # LED is consider a BASIC mode
                self._param_state[':FUNCTION:MODE'] = 'BASIC'
                self._cur_const_mode = None
            case 'Battery':
                # This is not a parameter with a state - it's just a command to switch
                # modes. The normal :FUNCTION tells us we're in the Battery mode, but it
                # doesn't allow us to SWITCH TO the Battery mode!
                self._inst.write(':BATTERY:FUNC')
                self._param_state[':FUNCTION:MODE'] = 'BATTERY'
                self._cur_const_mode = self._param_state[':BATTERY:MODE'].title()
            case 'OCPT':
                # This is not a parameter with a state - it's just a command to switch
                # modes. The normal :FUNCTION tells us we're in OCP mode, but it
                # doesn't allow us to SWITCH TO the OCP mode!
                self._inst.write(':OCP:FUNC')
                self._param_state[':FUNCTION:MODE'] = 'OCP'
                self._cur_const_mode = None
            case 'OPPT':
                # This is not a parameter with a state - it's just a command to switch
                # modes. The normal :FUNCTION tells us we're in OPP mode, but it
                # doesn't allow us to SWITCH TO the OPP mode!
                self._inst.write(':OPP:FUNC')
                self._param_state[':FUNCTION:MODE'] = 'OPP'
                self._cur_const_mode = None
            case 'Ext \u26A0':
                # EXTI and EXTV are really two different modes, but we treat them
                # as one for consistency. Unfortunately that means when the user switches
                # to "EXT" mode, you don't know whether they actually want V or I,
                # so we just assume V.
                self._cur_const_mode = 'Voltage'
                new_param_state[':EXT:MODE'] = 'EXTV'
            case 'List':
                # This is not a parameter with a state - it's just a command to switch
                # modes. The normal :FUNCTION tells us we're in List mode, but it
                # doesn't allow us to SWITCH TO the List mode!
                self._inst.write(':LIST:STATE:ON')
                self._param_state[':FUNCTION:MODE'] = 'LIST'
                self._cur_const_mode = self._param_state[':LIST:MODE'].title()
            case 'Program':
                # This is not a parameter with a state - it's just a command to switch
                # modes. The normal :FUNCTION tells us we're in List mode, but it
                # doesn't allow us to SWITCH TO the List mode!
                self._inst.write(':PROGRAM:STATE:ON')
                self._param_state[':FUNCTION:MODE'] = 'PROGRAM'
                self._cur_const_mode = None

        # Changing the mode turns off the load and short.
        # We have to do this manually in order for the later mode change to take effect.
        # If you try to change mode while the load is on, the SDL turns off the load,
        # but then ignores the mode change.
        self._update_load_state(0)
        self._update_short_state(0)

        self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_click_dynamic_mode(self):
        """Handle clicking on a Dynamic Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return

        self._cur_dynamic_mode = rb.wid

        # Changing the mode turns off the load and short.
        # We have to do this manually in order for the later mode change to take effect.
        # If you try to change mode while the load is on, the SDL turns off the load,
        # but then ignores the mode change.
        self._update_load_state(0)
        self._update_short_state(0)

        info = self._cur_mode_param_info()
        mode_name = info['mode_name']
        new_param_state = {':FUNCTION:TRANSIENT': self._cur_const_mode.upper(),
                           f':{mode_name}:TRANSIENT:MODE': rb.wid.upper()}

        self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_click_const_mode(self):
        """Handle clicking on a Constant Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        rb = self.sender()
        if not rb.isChecked():
            return
        self._cur_const_mode = rb.wid
        match self._cur_overall_mode:
            case 'Basic':
                new_param_state = {':FUNCTION': self._cur_const_mode.upper()}
            case 'Dynamic':
                new_param_state = {':FUNCTION:TRANSIENT': self._cur_const_mode.upper()}
                info = self._cur_mode_param_info(null_dynamic_mode_ok=True)
                mode_name = info['mode_name']
                self._cur_dynamic_mode = (
                    self._param_state[f':{mode_name}:TRANSIENT:MODE'].title())
            case 'Battery':
                new_param_state = {':BATTERY:MODE': self._cur_const_mode.upper()}
            case 'Ext \u26A0':
                if self._cur_const_mode == 'Voltage':
                    new_param_state = {':EXT:MODE': 'EXTV'}
                else:
                    new_param_state = {':EXT:MODE': 'EXTI'}
            case 'List':
                new_param_state = {':LIST:MODE': self._cur_const_mode.upper()}
            # None of the other modes have a "constant mode"

        # Changing the mode turns off the load and short.
        # We have to do this manually in order for the later mode change to take effect.
        # If you try to change mode while the load is on, the SDL turns off the load,
        # but then ignores the mode change.
        self._update_load_state(0)
        self._update_short_state(0)

        self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_click_range(self):
        """Handle clicking on a V or I range button."""
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

    def _on_click_ext_voltage_sense(self):
        """Handle click on External Voltage Source checkbox."""
        cb = self.sender()
        load_on = self._param_state[':INPUT:STATE']
        # You can't change the voltage source with the load on, so really quickly
        # turn it off and then back on.
        if load_on:
            self._update_load_state(0, update_widgets=False)
        if cb.isChecked():
            new_param_state = {':SYSTEM:SENSE:STATE': 1}
        else:
            new_param_state = {':SYSTEM:SENSE:STATE': 0}
        self._update_param_state_and_inst(new_param_state)
        if load_on:
            self._update_load_state(1)

    def _on_click_breakover_voltage_latch(self):
        """Handle click on Breakover Voltage Latch checkbox."""
        cb = self.sender()
        if cb.isChecked():
            new_param_state = {':VOLTAGE:LATCH:STATE': 1}
        else:
            new_param_state = {':VOLTAGE:LATCH:STATE': 0}
        self._update_param_state_and_inst(new_param_state)

    def _on_click_ext_input_state(self):
        """Handle click on External Input Control checkbox."""
        cb = self.sender()
        if cb.isChecked():
            new_param_state = {':EXT:INPUT:STATE': 1}
        else:
            new_param_state = {':EXT:INPUT:STATE': 0}
        self._update_param_state_and_inst(new_param_state)

    def _on_click_imonitor(self):
        """Handle click on Enable Ext Current Monitor checkbox."""
        cb = self.sender()
        if cb.isChecked():
            new_param_state = {':SYSTEM:IMONITOR:STATE': 1}
        else:
            new_param_state = {':SYSTEM:IMONITOR:STATE': 0}
        self._update_param_state_and_inst(new_param_state)

    def _on_click_vmonitor(self):
        """Handle click on Enable Ext Voltage Monitor checkbox."""
        cb = self.sender()
        if cb.isChecked():
            new_param_state = {':SYSTEM:VMONITOR:STATE': 1}
        else:
            new_param_state = {':SYSTEM:VMONITOR:STATE': 0}
        self._update_param_state_and_inst(new_param_state)

    def _on_list_table_change(self, row, column, val):
        """Handle change to any List Mode table value."""
        match column:
            case 0:
                self._list_mode_levels[row] = val
                self._inst.write(f':LIST:LEVEL {row+1},{val:.3f}')
            case 1:
                self._list_mode_widths[row] = val
                self._inst.write(f':LIST:WIDTH {row+1},{val:.3f}')
            case 2:
                self._list_mode_slews[row] = val
                self._inst.write(f':LIST:SLEW {row+1},{val:.3f}')
        self._update_list_table_graph(update_table=False)

    def _on_click_short_enable(self):
        """Handle clicking on the short enable checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        cb = self.sender()
        if not cb.isChecked():
            self._update_short_onoff_button(None)
        else:
            self._update_short_state(0) # Also updates the button

    def _on_click_short_on_off(self):
        """Handle clicking on the SHORT button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        state = 1-self._param_state[':SHORT:STATE']
        self._update_short_state(state) # Also updates the button

    def _update_short_onoff_button(self, state=None):
        """Update the style of the SHORT button based on current or given state."""
        if state is None:
            state = self._param_state[':SHORT:STATE']
        bt = self._widget_registry['ShortONOFF']
        if state:
            bt.setText('\u26A0 SHORT IS ON \u26A0')
            bg_color = '#ff0000'
        else:
            bt.setText('SHORT IS OFF')
            bg_color = '#c0c0c0'
        ss = f"""QPushButton {{
                background-color: {bg_color};
                min-width: 7.5em; max-width: 7.5em; min-height: 1.1em; max-height: 1.1em;
                border-radius: 0.3em; border: 3px solid black;
                font-weight: bold; font-size: 14px; }}
             QPushButton::pressed {{ border: 4px solid black; }}
              """
        bt.setStyleSheet(ss)
        if self._cur_overall_mode in ('Battery', 'OCPT', 'OPPT', 'List', 'Program'):
            # There is no SHORT capability in these modes
            self._widget_registry['ShortONOFFEnable'].setEnabled(False)
            self._widget_registry['ShortONOFF'].setEnabled(False)
        elif self._widget_registry['ShortONOFFEnable'].isChecked():
            self._widget_registry['ShortONOFFEnable'].setEnabled(True)
            self._widget_registry['ShortONOFF'].setEnabled(True)
        else:
            self._widget_registry['ShortONOFFEnable'].setEnabled(True)
            self._widget_registry['ShortONOFF'].setEnabled(False)

    def _on_click_load_on_off(self):
        """Handle clicking on the LOAD button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        state = 1-self._param_state[':INPUT:STATE']
        self._update_load_state(state) # Also updates the button
        # This prevents a UI flicker in the measurements due to Trise/Tfall being
        # shown and then later hidden
        self._update_widgets()

    def _update_load_onoff_button(self, state=None):
        """Update the style of the LOAD button based on current or given state."""
        if state is None:
            state = self._param_state[':INPUT:STATE']
        bt = self._widget_registry['LoadONOFF']
        if state:
            if self._cur_overall_mode in ('Battery', 'OCPT', 'OPPT'):
                bt.setText('STOP TEST')
            else:
                bt.setText('LOAD IS ON')
            bg_color = '#ffc0c0'
        else:
            if self._cur_overall_mode in ('Battery', 'OCPT', 'OPPT'):
                bt.setText('START TEST')
            else:
                bt.setText('LOAD IS OFF')
            bg_color = '#c0c0c0'
        ss = f"""QPushButton {{
                    background-color: {bg_color};
                    min-width: 7em; max-width: 7em;
                    min-height: 1em; max-height: 1em;
                    border-radius: 0.4em; border: 5px solid black;
                    font-weight: bold; font-size: 22px; }}
                 QPushButton:pressed {{ border: 7px solid black; }}
              """
        bt.setStyleSheet(ss)

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

    def _update_trigger_buttons(self):
        """Update the trigger button based on the current state."""
        src = self._param_state[':TRIGGER:SOURCE']
        self._widget_registry['Trigger_Bus'].setChecked(src == 'BUS')
        self._widget_registry['Trigger_Man'].setChecked(src == 'MANUAL')
        self._widget_registry['Trigger_Ext'].setChecked(src == 'EXTERNAL')

        enabled = False
        if (src == 'BUS' and
            self._param_state[':INPUT:STATE'] and
            ((self._cur_overall_mode == 'Dynamic' and
              self._cur_dynamic_mode != 'Continuous') or
             self._cur_overall_mode in ('List', 'Program'))):
            enabled = True
        self._widget_registry['Trigger'].setEnabled(enabled)

    def _on_click_trigger(self):
        """Handle clicking on the main trigger button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not self._widget_registry['Trigger'].isEnabled():
            # Necessary for ALT+T shortcut
            return
        self._inst.trg()
        if self._cur_overall_mode == 'List':
            # Hitting TRIG while List mode is running causes it to stop after the
            # current step is complete. Triggering multiple times doesn't change this.
            if not self._list_mode_running:
                if self._list_mode_cur_step_num is None:
                    self._list_mode_cur_step_num = 0
                self._list_mode_running = True
                self._list_mode_cur_step_start_time = time.time()
                self._list_mode_stopping = False
            else:
                # We stop the progression AFTER the current step finished
                self._list_mode_stopping = True
                if self._list_mode_cur_step_num == self._param_state[':LIST:STEP']-1:
                    QMessageBox.warning(
                        self, 'Warning',
                        'Due to a bug in the SDL1000, pausing in List Mode on the final '
                        'step and then resuming will cause execution of an additional '
                        'step not currently displayed. This will confuse the status '
                        'display and could potentially damage your device under test. '
                        'It is strongly recommended that you not resume List Mode '
                        'sequencing at this point but instead turn off the load to '
                        'reset to step 1. This bug has been reported to Siglent.')

    def _on_click_enable_measurements(self):
        """Handle clicking on an enable measurements checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        cb = self.sender()
        match cb.mode:
            case 'V':
                self._enable_measurement_v = cb.isChecked()
            case 'C':
                self._enable_measurement_c = cb.isChecked()
            case 'P':
                self._enable_measurement_p = cb.isChecked()
            case 'R':
                self._enable_measurement_r = cb.isChecked()
            case 'TR':
                self._enable_measurement_trise = cb.isChecked()
            case 'TF':
                self._enable_measurement_tfall = cb.isChecked()
        if self._enable_measurement_trise or self._enable_measurement_tfall:
            new_param_state = {':TIME:TEST:STATE': 1}
        else:
            new_param_state = {':TIME:TEST:STATE': 0}
        self._update_param_state_and_inst(new_param_state)
        self._update_widgets()

    def _on_click_reset_batt_test(self):
        """Handle clicking on the reset battery log button."""
        self._inst.write(':BATTERY:ADDCAP 0')
        self._reset_batt_log()
        self._update_widgets()

    ################################
    ### Internal helper routines ###
    ################################

    def _transient_string(self):
        """Return the SCPI substring corresponding to the Dynamic mode, if applicable."""
        if self._cur_overall_mode == 'Dynamic':
            return ':TRANSIENT'
        return ''

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

    def _put_inst_in_mode(self, overall_mode, const_mode):
        """Place the SDL in the given overall mode (and const mode)."""
        overall_mode = overall_mode.upper()
        if const_mode is not None:
            const_mode = const_mode.upper()
        match overall_mode:
            case 'DYNAMIC':
                self._inst.write(f':FUNCTION:TRANSIENT {const_mode}')
            case 'BASIC':
                self._inst.write(f':FUNCTION {const_mode}')
            case 'LED':
                self._inst.write(':FUNCTION LED')
            case 'BATTERY':
                self._inst.write(':FUNCTION BATTERY')
                self._inst.write(f':BATTERY:MODE {const_mode}')
            case 'OCPT':
                self._inst.write(':OCP:FUNC')
            case 'OPPT':
                self._inst.write(':OPP:FUNC')
            case 'EXT \u26A0':
                if const_mode == 'VOLTAGE':
                    self._inst.write(':EXT:MODE EXTV')
                else:
                    assert const_mode == 'CURRENT'
                    self._inst.write(':EXT:MODE EXTI')
            case 'LIST':
                self._inst.write(':LIST:STATE:ON')
            case 'PROGRAM':
                self._inst.write(':PROGRAM:STATE:ON')
            case _:
                assert False, overall_mode

    def _update_state_from_param_state(self):
        """Update all internal state and widgets based on the current _param_state."""
        if self._param_state[':EXT:MODE'] != 'INT':
            mode = 'Ext \u26A0'
        else:
            mode = self._param_state[':FUNCTION:MODE']
            # Convert the title-case SDL-specific name to the name we use in the GUI
            match mode:
                case 'BASIC':
                    if self._param_state[':FUNCTION'] == 'LED':
                        mode = 'LED'
                case 'TRAN':
                    mode = 'Dynamic'
                case 'OCP':
                    mode = 'OCPT'
                case 'OPP':
                    mode = 'OPPT'
                # Other cases are already correct
            if mode not in ('LED', 'OCPT', 'OPPT'):
                mode = mode.title()
        assert mode in ('Basic', 'LED', 'Battery', 'OCPT', 'OPPT', 'Ext \u26A0',
                        'Dynamic', 'Program', 'List')
        self._cur_overall_mode = mode

        # Initialize the dynamic and const mode as appropriate
        self._cur_dynamic_mode = None
        self._cur_const_mode = None
        match mode:
            case 'Basic':
                self._cur_const_mode = self._param_state[':FUNCTION'].title()
                assert self._cur_const_mode in (
                    'Voltage', 'Current', 'Power', 'Resistance'), self._cur_const_mode
            case 'Dynamic':
                self._cur_const_mode = self._param_state[':FUNCTION:TRANSIENT'].title()
                assert self._cur_const_mode in (
                    'Voltage', 'Current', 'Power', 'Resistance'), self._cur_const_mode
                param_info = self._cur_mode_param_info(null_dynamic_mode_ok=True)
                mode_name = param_info['mode_name']
                self._cur_dynamic_mode = (
                    self._param_state[f':{mode_name}:TRANSIENT:MODE'].title())
                assert self._cur_dynamic_mode in (
                    'Continuous', 'Pulse', 'Toggle'), self._cur_dynamic_mode
            case 'Battery':
                self._cur_const_mode = self._param_state[':BATTERY:MODE'].title()
                assert self._cur_const_mode in (
                    'Current', 'Power', 'Resistance'), self._cur_const_mode
            case 'Ext \u26A0':
                if self._param_state[':EXT:MODE'] == 'EXTV':
                    self._cur_const_mode = 'Voltage'
                else:
                    assert self._param_state[':EXT:MODE'] == 'EXTI'
                    self._cur_const_mode = 'Current'
            case 'List':
                self._cur_const_mode = self._param_state[':LIST:MODE'].title()
                assert self._cur_const_mode in (
                    'Voltage', 'Current', 'Power', 'Resistance'), self._cur_const_mode

        # If the Time test is turned on, then we enable both the TRise and TFall
        # measurements, but if it's off, we disable them both.
        if self._param_state[':TIME:TEST:STATE']:
            self._enable_measurement_trise = True
            self._enable_measurement_tfall = True
        else:
            self._enable_measurement_trise = False
            self._enable_measurement_tfall = False

        # Now update all the widgets and their values with the new info
        # This is a bit of a hack - first do all the widgets ignoring the min/max
        # value limits, which allows us to actually initialize all the values. Then
        # go back and do the same thing again, this time setting the min/max values.
        # It's not very efficient, but it doesn't matter.
        self._update_widgets(minmax_ok=False)
        self._update_widgets(minmax_ok=True)

    def _update_list_mode_from_instrument(self, new_rows_only=False):
        """Update the internal state for List mode from the instruments.

        If new_rows_only is True, then we just read the data for any rows that
        we haven't read already, assuming the rest to already be correct. If the
        number of rows has decreased, we leave the entries in the internal state so
        if the rows are increased again we don't have to bother fetching the data."""
        if not new_rows_only:
            self._list_mode_levels = []
            self._list_mode_widths = []
            self._list_mode_slews = []
        steps = self._param_state[':LIST:STEP']
        for i in range(len(self._list_mode_levels)+1, steps+1):
            self._list_mode_levels.append(float(self._inst.query(f':LIST:LEVEL? {i}')))
            self._list_mode_widths.append(float(self._inst.query(f':LIST:WIDTH? {i}')))
            self._list_mode_slews.append(float(self._inst.query(f':LIST:SLEW? {i}')))

    def _update_load_state(self, state, update_inst=True, update_widgets=True):
        """Update the load on/off internal state, possibly updating the instrument."""
        old_state = self._param_state[':INPUT:STATE']
        if state == old_state:
            return

        # When turning on/off the load, record the details for the battery log
        # or other purposes that may be written in the future
        if state:
            self._load_on_time = time.time()
            self._batt_log_initial_voltage = None
            # And if we're in List mode, reset to step None (it will go to 0 when
            # triggered)
            if self._cur_overall_mode == 'List':
                self._list_mode_cur_step_num = None
        else:
            self._load_off_time = time.time()
            # Any change from one overall mode to another (e.g. leaving List mode
            # for any reason) will pass through here, so this takes care of stopping
            # the update in all those cases
            self._list_mode_cur_step_num = None
            self._list_mode_stopping = False
            self._list_mode_running = False
            self._update_list_table_graph(list_step_only=True)

        # When the load is turned off in battery mode, update the battery log and
        # show the battery report.
        if not state and self._cur_overall_mode == 'Battery':
            self._show_batt_report = True
            # For some reason when using Battery mode remotely, when the test is
            # complete (or aborted), the ADDCAP field is not automatically updated
            # like it is when you run a test from the front panel. So we do the
            # computation and update it here.
            disch_cap = self._inst.measure_battery_capacity()
            add_cap = self._inst.measure_battery_add_capacity()
            new_add_cap = (disch_cap + add_cap) * 1000  # ADDCAP takes mAh
            self._inst.write(f':BATTERY:ADDCAP {new_add_cap}')
            # Update the battery log entries
            if self._load_on_time is not None and self._load_off_time is not None:
                level = self._param_state[':BATTERY:LEVEL']
                match self._cur_const_mode:
                    case 'Current':
                        batt_mode = f'CC {level:.3f}A'
                    case 'Power':
                        batt_mode = f'CP {level:.3f}W'
                    case 'Resistance':
                        batt_mode = f'CR {level:.3f}\u2126'
                self._batt_log_modes.append(batt_mode)
                stop_cond = ''
                if self._param_state[':BATTERY:VOLTAGE:STATE']:
                    v = self._param_state[':BATTERY:VOLTAGE']
                    stop_cond += f'Vmin {v:.3f}V'
                if self._param_state[':BATTERY:CAP:STATE']:
                    if stop_cond != '':
                        stop_cond += ' or '
                        cap = self._param_state[':BATTERY:CAP']/1000
                    stop_cond += f'Cap {cap:3f}Ah'
                if self._param_state[':BATTERY:TIMER:STATE']:
                    if stop_cond != '':
                        stop_cond += ' or '
                    stop_cond += 'Time '+self._time_to_hms(
                        int(self._param_state[':BATTERY:TIMER']))
                if stop_cond == '':
                    self._batt_log_stop_cond.append('None')
                else:
                    self._batt_log_stop_cond.append(stop_cond)
                self._batt_log_initial_voltages.append(self._batt_log_initial_voltage)
                self._batt_log_start_times.append(self._load_on_time)
                self._batt_log_end_times.append(self._load_off_time)
                self._batt_log_run_times.append(self._load_off_time -
                                                self._load_on_time)
                self._batt_log_caps.append(disch_cap)

        if update_inst:
            new_param_state = {':INPUT:STATE': state}
            self._update_param_state_and_inst(new_param_state)
        else:
            self._param_state[':INPUT:STATE'] = state

        if update_widgets:
            self._update_widgets()

    def _update_short_state(self, state):
        """Update the SHORT on/off internal state and update the instrument."""
        new_param_state = {':SHORT:STATE': state}
        self._update_param_state_and_inst(new_param_state)
        self._update_short_onoff_button(state)

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
        # and the "Constant Mode" groups
        for widget_name, widget in self._widget_registry.items():
            if widget_name.startswith('Overall_'):
                widget.setChecked(widget_name.endswith(self._cur_overall_mode))
            if self._cur_const_mode is not None and widget_name.startswith('Const_'):
                widget.setChecked(widget_name.endswith(self._cur_const_mode))

        # First we go through the widgets for the Dynamic sub-modes and the Constant
        # Modes and enable or disable them as appropriate based on the Overall Mode.
        self._show_or_disable_widgets(_SDL_OVERALL_MODES[self._cur_overall_mode])

        # Now we enable or disable widgets by first scanning through the "General"
        # widget list and then the widget list specific to this overall mode (if any).
        self._show_or_disable_widgets(_SDL_MODE_PARAMS['General']['widgets'])
        if param_info['widgets'] is not None:
            self._show_or_disable_widgets(param_info['widgets'])

        # Now we go through the details for each parameter and fill in the widget
        # value and set the widget parameters, as appropriate. We do the General
        # parameters first and then the parameters for the current mode.
        new_param_state = {}
        for phase in range(2):
            if phase == 0:
                params = _SDL_MODE_PARAMS['General']['params']
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
                            if minmax_ok:
                                # This is needed because when we're first loading up the
                                # widgets from a cold start, the paired widget may not
                                # have a good min value yet
                                min_val = self._widget_registry[min_val[2:]].value()
                            else:
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
                                case 'P': # SDL1020 is 200W, SDL1030 is 300W
                                    max_val = self._inst._max_power
                                case 'S': # Slew range depends on IRANGE
                                    if self._param_state[
                                            f':{mode_name}{trans}:IRANGE'] == '5':
                                        max_val = 0.5
                                    else:
                                        max_val = 2.5
                                case 'W':
                                    if minmax_ok:
                                        # This is needed because when we're first loading
                                        # up the widgets from a cold start, the paired
                                        # widget may not have a good max value yet
                                        max_val = (self._widget_registry[max_val[2:]]
                                                   .value())
                                    else:
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

        # Update the buttons
        self._update_load_onoff_button()
        self._update_short_onoff_button()
        self._update_trigger_buttons()

        # Maybe update the List table
        if self._cur_overall_mode == 'List':
            self._update_list_table_graph()

        # Update the Enable Measurements checkboxes
        self._widget_registry['EnableV'].setChecked(self._enable_measurement_v)
        self._widget_registry['EnableC'].setChecked(self._enable_measurement_c)
        self._widget_registry['EnableP'].setChecked(self._enable_measurement_p)
        self._widget_registry['EnableR'].setChecked(self._enable_measurement_r)
        self._widget_registry['EnableTRise'].setChecked(self._enable_measurement_trise)
        self._widget_registry['EnableTFall'].setChecked(self._enable_measurement_tfall)

        # If TRise and TFall are turned off, then also disable their measurement
        # display just to save space, since these are rare functions to actually
        # user. Note they will have been turned on in the code above as part of the
        # normal widget actions for Basic mode, so we only have to worry about hiding
        # them here, not showing them.
        if (self._cur_overall_mode == 'Basic' and
                (self._enable_measurement_trise or self._enable_measurement_tfall)):
            self._widget_registry['MeasureTRise'].show()
            self._widget_registry['MeasureTFall'].show()
        else:
            self._widget_registry['MeasureTRise'].hide()
            self._widget_registry['MeasureTFall'].hide()

        # Finally, we don't allow parameters to be modified during certain modes
        if (self._cur_overall_mode in ('Battery', 'List') and
                self._param_state[':INPUT:STATE']):
            # Battery or List mode is running
            self._widget_registry['FrameMode'].setEnabled(False)
            self._widget_registry['FrameConstant'].setEnabled(False)
            self._widget_registry['FrameRange'].setEnabled(False)
            self._widget_registry['FrameMainParameters'].setEnabled(False)
            self._widget_registry['FrameAuxParameters'].setEnabled(False)
            self._widget_registry['GlobalParametersRow'].setEnabled(False)
        elif self._cur_overall_mode == 'Ext \u26A0' and self._param_state[':INPUT:STATE']:
            # External control mode - can't change range
            self._widget_registry['FrameRange'].setEnabled(False)
        else:
            self._widget_registry['FrameMode'].setEnabled(True)
            self._widget_registry['FrameConstant'].setEnabled(True)
            self._widget_registry['FrameRange'].setEnabled(True)
            self._widget_registry['FrameMainParameters'].setEnabled(True)
            self._widget_registry['FrameAuxParameters'].setEnabled(True)
            self._widget_registry['GlobalParametersRow'].setEnabled(True)
            self._widget_registry['MainParametersLabel_BattC'].setEnabled(True)
            self._widget_registry['MainParameters_BattC'].setEnabled(True)

        status_msg = None
        if self._cur_overall_mode == 'List':
            status_msg = """Turn on load. Use TRIG to start/pause list progression.
List status tracking is an approximation."""
        elif (self._cur_overall_mode == 'Battery' and
              not self._param_state[':INPUT:STATE'] and
              len(self._batt_log_start_times) > 0):
            status_msg = """Warning: Data held from previous discharge.
Reset Addl Cap & Test Log to start fresh."""
        if status_msg is None:
            self._statusbar.clearMessage()
        else:
            self._statusbar.showMessage(status_msg)

        self._disable_callbacks = False

    def _update_list_table_graph(self, update_table=True, list_step_only=False):
        """Update the list table and associated plot if data has changed."""
        if self._cur_overall_mode != 'List':
            return
        vrange = float(self._param_state[':LIST:VRANGE'])
        irange = float(self._param_state[':LIST:IRANGE'])
        # If the ranges changed, we may have to clip the values in the table
        match self._cur_const_mode:
            case 'Voltage':
                self._list_mode_levels = [min(x, vrange)
                                          for x in self._list_mode_levels]
            case 'Current':
                self._list_mode_levels = [min(x, irange)
                                          for x in self._list_mode_levels]
            case 'Power':
                self._list_mode_levels = [min(x, self._inst._max_power)
                                          for x in self._list_mode_levels]
            # Nothing to do for Resistance since its max is largest
        table = self._widget_registry['ListTable']
        step = self._param_state[':LIST:STEP']
        widths = (90, 70, 80)
        match self._cur_const_mode:
            case 'Voltage':
                hdr = ('Voltage (V)', 'Time (s)')
                fmts = ('.3f', '.3f')
                ranges = ((0, vrange), (0.001, 999))
            case 'Current':
                hdr = ['Current (A)', 'Time (s)', 'Slew (A/\u00B5s)']
                fmts = ['.3f', '.3f', '.3f']
                if irange == 5:
                    ranges = ((0, irange), (0.001, 999), (0.001, 0.5))
                else:
                    ranges = ((0, irange), (0.001, 999), (0.001, 2.5))
            case 'Power':
                hdr = ['Power (W)', 'Time (s)']
                fmts = ['.2f', '.3f']
                ranges = ((0, self._inst._max_power), (0.001, 999))
            case 'Resistance':
                hdr = ['Resistance (\u2126)', 'Time (s)']
                fmts = ['.3f', '.3f']
                ranges = ((0.03, 10000), (0.001, 999))
        self._list_mode_levels = [min(max(x, ranges[0][0]), ranges[0][1])
                                  for x in self._list_mode_levels]
        self._list_mode_widths = [min(max(x, ranges[1][0]), ranges[1][1])
                                  for x in self._list_mode_widths]
        if len(ranges) == 3:
            self._list_mode_slews = [min(max(x, ranges[2][0]), ranges[2][1])
                                     for x in self._list_mode_slews]
        if update_table:
            # We don't always want to update the table, because in edit mode when the
            # user changes a value, the table will have already been updated internally,
            # and if we mess with it here it screws up the focus for the edit box and
            # the edit box never closes.
            data = []
            for i in range(step):
                if self._cur_const_mode == 'Current':
                    data.append([self._list_mode_levels[i],
                                 self._list_mode_widths[i],
                                 self._list_mode_slews[i]])
                else:
                    data.append([self._list_mode_levels[i],
                                 self._list_mode_widths[i]])
            table.model().set_params(data, fmts, hdr)
            for i, fmt in enumerate(fmts):
                table.setItemDelegateForColumn(
                    i, DoubleSpinBoxDelegate(self, fmt, ranges[i]))
                table.setColumnWidth(i, widths[i])

        # Update the List plot
        min_plot_y = 0
        max_plot_y = max(self._list_mode_levels[:step])
        if not list_step_only:
            plot_x = [0]
            plot_y = [self._list_mode_levels[0]]
            for i in range(step-1):
                x_val = plot_x[-1] + self._list_mode_widths[i]
                plot_x.append(x_val)
                plot_x.append(x_val)
                plot_y.append(self._list_mode_levels[i])
                plot_y.append(self._list_mode_levels[i+1])
            plot_x.append(plot_x[-1]+self._list_mode_widths[step-1])
            plot_y.append(self._list_mode_levels[step-1])
            plot_widget = self._widget_registry['ListPlot']
            self._list_mode_level_plot.setData(plot_x, plot_y)
            plot_widget.setLabel(axis='left', text=hdr[0])
            plot_widget.setLabel(axis='bottom', text='Cumulative Time (s)')
            plot_widget.setYRange(min_plot_y, max_plot_y)

        # Update the running plot and highlight the appropriate table row
        if self._list_mode_cur_step_num is not None:
            delta = 0
            step_num = self._list_mode_cur_step_num
            if self._list_mode_running:
                delta = time.time() - self._list_mode_cur_step_start_time
            else:
                # When we pause List mode, we end at the end of the previous step,
                # not the start of the next step
                step_num = (step_num-1) % self._param_state[':LIST:STEP']
                delta = self._list_mode_widths[step_num]
            cur_step_x = 0
            if step_num > 0:
                cur_step_x = sum(self._list_mode_widths[:step_num])
            cur_step_x += delta
            self._list_mode_step_plot.setData([cur_step_x, cur_step_x],
                                              [min_plot_y, max_plot_y])
            table.model().set_highlighted_row(step_num)
        else:
            table.model().set_highlighted_row(None)
            self._list_mode_step_plot.setData([], [])

    def _update_heartbeat(self):
        """Handle the rapid heartbeat."""
        if self._list_mode_running:
            cur_time = time.time()
            delta = cur_time - self._list_mode_cur_step_start_time
            if delta >= self._list_mode_widths[self._list_mode_cur_step_num]:
                # We've moved on to the next step (or more than one step)
                if self._list_mode_stopping:
                    self._list_mode_stopping = False
                    self._list_mode_running = False
                while delta >= self._list_mode_widths[self._list_mode_cur_step_num]:
                    delta -= self._list_mode_widths[self._list_mode_cur_step_num]
                    self._list_mode_cur_step_num = (
                        self._list_mode_cur_step_num+1) % self._param_state[':LIST:STEP']
                self._list_mode_cur_step_start_time = cur_time - delta
            self._update_list_table_graph(list_step_only=True)
        if self._show_batt_report:
            self._show_batt_report = False
            self._menu_do_device_batt_report()

    def _cur_mode_param_info(self, null_dynamic_mode_ok=False):
        """Get the parameter info structure for the current mode."""
        if self._cur_overall_mode == 'Dynamic':
            if null_dynamic_mode_ok and self._cur_dynamic_mode is None:
                # We fake this here for refresh(), where we don't know the dynamic
                # mode until we know we're in the dynamic mode itself...catch 22
                key = (self._cur_overall_mode, self._cur_const_mode, 'Continuous')
            else:
                key = (self._cur_overall_mode, self._cur_const_mode,
                       self._cur_dynamic_mode)
        else:
            key = (self._cur_overall_mode, self._cur_const_mode)
        return _SDL_MODE_PARAMS[key]

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

    def _reset_batt_log(self):
        """Reset the battery log."""
        self._batt_log_modes = []
        self._batt_log_stop_cond = []
        self._batt_log_start_times = []
        self._batt_log_end_times = []
        self._batt_log_run_times = []
        self._batt_log_initial_voltage = None
        self._batt_log_initial_voltages = []
        self._batt_log_caps = []

    @staticmethod
    def _time_to_str(t):
        """Convert time in seconds to Y M D H:M:S."""
        return time.strftime('%Y %b %d %H:%M:%S', time.localtime(t))

    @staticmethod
    def _time_to_hms(t):
        """Convert time in seconds to H:M:S."""
        m, s = divmod(t, 60)
        h, m = divmod(m, 60)
        return '%02d:%02d:%02d' % (h, m, s)

    def _batt_log_report(self):
        """Generate the battery log report and return as a string."""
        n_entries = len(self._batt_log_start_times)
        if n_entries == 0:
            return None
        single = (n_entries == 1)
        ret = f'Test device: {self._inst.manufacturer} {self._inst.model}\n'
        ret += f'S/N: {self._inst.serial_number}\n'
        ret += f'Firmware: {self._inst.firmware_version}\n'
        if not single:
            ret += '** Overall test **\n'
        ret += 'Start time: '+self._time_to_str(self._batt_log_start_times[0])+'\n'
        ret += 'End time: '+self._time_to_str(self._batt_log_end_times[-1])+'\n'
        t = self._batt_log_end_times[-1]-self._batt_log_start_times[0]
        ret += 'Elapsed time: '+self._time_to_hms(t)+'\n'
        if not single:
            t = sum(self._batt_log_run_times)
            ret += 'Test time: '+self._time_to_hms(t)+'\n'
        if single:
            ret += 'Test mode: '+self._batt_log_modes[0]+'\n'
            ret += 'Stop condition: '+self._batt_log_stop_cond[0]+'\n'
            if self._batt_log_initial_voltages[0] is None:
                ret += 'Initial voltage: Not measured\n'
            else:
                init_v = self._batt_log_initial_voltages[0]
                ret += f'Initial voltage: {init_v:.3f}V\n'
        cap = sum(self._batt_log_caps)
        ret += f'Capacity: {cap:.3f}Ah\n'
        if not single:
            for i in range(n_entries):
                ret += f'** Test segment #{i+1}  **\n'
                ret += 'Start time: '+self._time_to_str(self._batt_log_start_times[i])
                ret += '\n'
                ret += 'End time: '+self._time_to_str(self._batt_log_end_times[i])+'\n'
                ret += 'Test time: '+self._time_to_hms(self._batt_log_run_times[i])+'\n'
                ret += 'Test mode: '+self._batt_log_modes[i]+'\n'
                ret += 'Stop condition: '+self._batt_log_stop_cond[i]+'\n'
                if self._batt_log_initial_voltages[i] is None:
                    ret += 'Initial voltage: Not measured\n'
                else:
                    init_v = self._batt_log_initial_voltages[i]
                    ret += f'Initial voltage: {init_v:.3f}V\n'
                cap = self._batt_log_caps[i]
                ret += f'Capacity: {cap:.3f}Ah\n'
        return ret


"""
[:SOURce]:PROGram:STEP {< number > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:STEP?
[:SOURce]:PROGram:MODE <step>, {CURRent | VOLTage | POWer | RESistance | LED}
[:SOURce]:PROGram:MODE? <step>
[:SOURce]:PROGram:IRANGe <step,value>
[:SOURce]:PROGram: VRANGe <step, value>,
[:SOURce]:PROGram: V RANGe? <step>
[:SOURce]:PROGram:RRANGe <step>, {LOW | MIDDLE | HIGH}
[:SOURce]:PROGram:RRANGe? <step>
[:SOURce]:PROGram:SHORt <step>, {ON | OFF | 0 | 1}
[:SOURce]:PROGram:SHORt? <step>
[:SOURce]:PROGram:PAUSE <step>, {ON | OFF | 0 | 1 }
[:SOURce]:PROGram:PAUSE? <step>,
[:SOURce]:PROGram:TIME:ON <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:TIME:ON? <step>
[:SOURce]:PROGram:TIME:OFF <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:TIME:OFF? <step>
[:SOURce]:PROGram:TIME:DELay <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:TIME:DELay? <step>
[:SOURce]:PROGram:MIN <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:MIN? <step>
[:SOURce]:PROGram:MAX <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:MAX? <step>
[:SOURce]:PROGram:LEVel <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:LEVel? <step>
[:SOURce]:PROGram:LED:CURRent <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:LED:CURRent? <step>
[:SOURce]:PROGram:LED:RCOnf <step>, {< value > | MINimum | MAXimum | DEFault}
[:SOURce]:PROGram:LED:RCOnf? <step>
[:SOURce]:PROGram:STATe:ON
[:SOURce]:PROGram:STATe?
[:SOURce]:PROGram:TEST? <step>

STOP:ON:FAIL[:STATe] {ON | OFF | 0 | 1}
STOP:ON:FAIL[:STATe]?

TIME:TEST:VOLTage:LOW {< value > | MINimum | MAXimum | DEFault}
TIME:TEST:VOLTage:LOW?
TIME:TEST:VOLTage:HIGH {< value | MINimum | MAXimum | DEFault}
TIME:TEST:VOLTage:HIGH?
"""
