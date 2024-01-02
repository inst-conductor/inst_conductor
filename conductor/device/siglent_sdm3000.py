################################################################################
# conductor/device/siglent_sdm3000.py
#
# This file is part of the inst_conductor software suite.
#
# It contains all code related to the Siglent SDM3000 series of programmable
# multimeters:
#   - SDM3045X
#   - SDM3055
#   - SDM3065X
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
#   - What you send to "RANGE"
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

################################################################################
# *IDN? returns:
#
# Siglent Technologies,SDM3055,SN123456789,1.01.01.25
################################################################################


import asyncio
import json
import logging
import random
import re

from PyQt6.QtWidgets import (QWidget,
                             QButtonGroup,
                             QCheckBox,
                             QGridLayout,
                             QGroupBox,
                             QHBoxLayout,
                             QLabel,
                             QLayout,
                             QRadioButton,
                             QVBoxLayout)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence

from qasync import asyncSlot
from conductor.qasync_helper import (asyncSlotSender,
                                     QAsyncFileDialog,
                                     QAsyncMessageBox)

from conductor.device.config_widget_base import (ConfigureWidgetBase,
                                                 MultiSpeedSpinBox)
from conductor.device import (Device4882,
                              ConnectionLost,
                              InstrumentClosed,
                              NotConnected)
from conductor.version import VERSION


class InstrumentSiglentSDM3000(Device4882):
    """Controller for SDM3000-series devices."""

    @classmethod
    def idn_mapping(cls):
        """Map IDN information to an instrument class."""
        # The main difference between these models is the number of digits:
        #   SDM3045X => 4 1/2
        #   SDM3055  => 5 1/2
        #   SDM3065X => 6 1/2
        # See the SCPI reference below for other differences
        return {
            ('Siglent Technologies', 'SDM3045X'): InstrumentSiglentSDM3000,
            ('Siglent Technologies', 'SDM3055'):  InstrumentSiglentSDM3000,
            ('Siglent Technologies', 'SDM3065X'): InstrumentSiglentSDM3000
        }

    @classmethod
    def supported_instruments(cls):
        """Return a list of supported instrument models."""
        return (
            'SDM3045X',
            'SDM3055',
            'SDM3065X'
        )

    @classmethod
    def get_fake_instrument_class(cls):
        """Return the class of the fake instrument."""
        return FakeInstrumentSiglentSDM3000

    def __init__(self, *args, existing_names=None, manufacturer=None, model=None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        super().init_names('SDM3000', 'SDM', existing_names)

    async def connect(self, *args, **kwargs):
        """Connect to the instrument and set it to remote state."""
        await super().connect(*args, **kwargs)
        idn = await self.idn()
        idn = idn.split(',')
        if len(idn) != 4:
            assert ValueError
        (self._manufacturer,
            self._model,
            self._serial_number,
            self._firmware_version) = idn
        if self._model.strip() == '':
            # Handle my broken SDM3055
            self._model = 'SDM3055'
        if self._manufacturer != 'Siglent Technologies':
            assert ValueError
        if not self._model.startswith('SDM'):
            assert ValueError
        self._long_name = f'{self._model} @ {self._resource_name}'
        # The mere act of doing any SCPI command puts the device in remote mode
        # so we don't have to do anything special here

    async def disconnect(self, *args, **kwargs):
        """Disconnect from the instrument and turn off its remote state."""
        # There is no way to put the SDM back in local mode except by pressing the
        # LOCAL button on the front panel
        self._ready_to_close = False
        await super().disconnect(*args, **kwargs)

    def configure_widget(self, main_window, measurements_only):
        """Return the configuration widget for this instrument."""
        return InstrumentSiglentSDM3000ConfigureWidget(
            main_window,
            self,
            measurements_only=measurements_only)


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
    'DC Voltage':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'AC Voltage':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'DC Current':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'AC Current':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    '2-W Resistance': ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    '4-W Resistance': ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Continuity':     ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Diode':          ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Frequency':      ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Period':         ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Temperature':    ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
    'Capacitance':    ('~FrameRange_.*', '!Speed.*', '!DCFilter', '!AutoZero', '!Impedance.*'),
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
#          and 'r' for a radio button, with the variants 'rvac', 'rvdc',
#          'riac', 'ridc', 'rr', and 'rc' for radio buttons that convert RANGE
#          values, and 'rs' that converts NPLC speeds.
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
            (':TRIGGER:SOURCE',          's', True),
         )
        },
    ('ParamSet'):
        {'widgets': (),
         'mode_name': None,
         'params': (
            # For General only! The third param is True meaning to write it while
            # copying _param_state to the instrument.
            (':FUNCTION',                'r', False),
         )
        },
    ('DC Voltage'):
        {'widgets': ('FrameRange_Voltage:DC', 'FrameParam1'),
         'mode_name': 'VOLT:DC',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'rvdc', None, 'Range_Voltage:DC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Voltage:DC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('IMP',                       'r', 'ImpedanceLabel', 'Impedance_.*'),
            ('6/AZ:STATE',                'b', None, 'AutoZero'), # 3065X
            ('45/FILTER:STATE',           'b', None, 'DCFilter'), # 3045x/3055
          )
        },
    ('AC Voltage'):
        {'widgets': ('FrameRange_Voltage:AC', 'FrameParam1'),
         'mode_name': 'VOLT:AC',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'rvac', None, 'Range_Voltage:AC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Voltage:AC_Auto'),
            # XXX No NPLC for AC VOLTAGE
            # ('BANDWIDTH',                 's', None, ''), # XXX 3065X
          )
        },
    ('DC Current'):
        {'widgets': ('FrameRange_Current:DC', 'FrameParam1'),
         'mode_name': 'CURR:DC',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'ridc', None, 'Range_Current:DC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Current:DC_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('6/AZ:STATE',                'b', None, 'AutoZero'), # 3065X
            ('45/FILTER:STATE',           'b', None, 'DCFilter'), # 3045X/3055
          )
        },
    ('AC Current'):
        {'widgets': ('FrameRange_Current:AC', 'FrameParam1'),
         'mode_name': 'CURR:AC',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'riac', None, 'Range_Current:AC_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Current:AC_Auto'),
            # XXX No NPLC for AC Current
            # ('BANDWIDTH',                 's', None, ''), # XXX 3065X
          )
        },
    ('2-W Resistance'):
        {'widgets': ('FrameRange_Resistance:2W', 'FrameParam1'),
         'mode_name': 'RES',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'rr', None, 'Range_Resistance:2W_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Resistance:2W_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('6/AZ:STATE',                  'b', None, 'AutoZero'), # 3065X
          )
        },
    ('4-W Resistance'):
        {'widgets': ('FrameRange_Resistance:4W', 'FrameParam1'),
         'mode_name': 'FRES',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'rr', None, 'Range_Resistance:4W_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Resistance:4W_Auto'),
            ('NPLC',                     'rs', 'SpeedLabel', 'Speed_.*'),
            ('6/AZ:STATE',                  'b', None, 'AutoZero'), # 3065X
          )
        },
    ('Capacitance'):
        {'widgets': ('FrameRange_Capacitance',),
         'mode_name': 'CAP',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('RANGE',                    'rc', None, 'Range_Capacitance_RB_.*'),
            ('RANGE:AUTO',                'b', None, 'Range_Capacitance_Auto'),
          )
        },
    ('Continuity'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'CONT',
         'params': (
            #  ('THRESHOLD:VALUE',           'f', None, ''), # XXX
            #  ('VOLUME:STATE',              's', None, ''), # XXX Really r
          )
        },
    ('Diode'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'DIOD',
         'params': (
            #  ('THRESHOLD:VALUE',           'f', None, ''), # XXX
            #  ('VOLUME:STATE',              's', None, ''), # XXX Really r
          )
        },
    ('Frequency'):
        {'widgets': ('FrameRange_Frequency:Voltage',),
         'mode_name': 'FREQ',
         'params': (
            # ('NULL:STATE',                 'b', None, ''), # XXX
            # ('NULL:VALUE',                 'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',            'b', None, ''), # XXX
            ('VOLT:RANGE',                 'rvac', None, 'Range_Frequency:Voltage_RB_.*'), # XXX rvac?
            ('VOLT:RANGE:AUTO',            'b', None, 'Range_Frequency:Voltage_Auto'),
            # ('APERTURE',                   'r', None, ''), # XXX 3065X
          )
        },
    ('Period'):
        {'widgets': ('FrameRange_Period:Voltage',),
         'mode_name': 'PER',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            ('VOLT:RANGE',                    'rvac', None, 'Range_Period:Voltage_RB_.*'), # XXX rvac?
            ('VOLT:RANGE:AUTO',                'b', None, 'Range_Period:Voltage_Auto'),
            # ('APERTURE',                    'r', None, ''), # XXX 3065X
          )
        },
    ('Temperature'):
        {'widgets': ('!FrameRange_Voltage:DC',),
         'mode_name': 'TEMP',
         'params': (
            # ('NULL:STATE',                'b', None, ''), # XXX
            # ('NULL:VALUE',                'f', None, ''), # XXX
            # ('NULL:VALUE:AUTO',           'b', None, ''), # XXX
            # ('UDEFINE:THER:TRAN:LIST',    's', None, ''), # XXX
            # ('UDEFINE:RTD:TRAN:LIST',     's', None, ''), # XXX
            # ('MDEFINE:THER:TRAN:LIST',    's', None, ''), # XXX
            # ('MDEFINE:RTD:TRAN:LIST',     's', None, ''), # XXX
            # ('UDEFINE:THER:TRAN:POINT',   's', None, ''), # XXX
            # ('UDEFINE:RTD:TRAN:POINT',    's', None, ''), # XXX
            # ('MDEFINE:THER:TRAN:POINT',   's', None, ''), # XXX
            # ('MDEFINE:RTD:TRAN:POINT',    's', None, ''), # XXX
          )
        },
}

# RANGE parameter conversions

_RANGE_VAC_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
           0.6:  '600MV',
           6.0:     '6V',
          60.0:    '60V',
         600.0:   '600V',
         750.0:   '750V',
    },
    'SDM3055': {
           0.2:  '200MV',
           2.0:     '2V',
          20.0:    '20V',
         200.0:   '200V',
         750.0:   '750V',
    },
    'SDM3065X': {
           0.2:  '200MV',
           2.0:     '2V',
          20.0:    '20V',
         200.0:   '200V',
         750.0:   '750V',
    }
}
_RANGE_VAC_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
        '600MV': '600 mV',
           '6V':    '6 V',
          '60V':   '60 V',
         '600V':  '600 V',
         '750V':  '750 V',
    },
    'SDM3055': {
        '200MV': '200 mV',
           '2V':    '2 V',
          '20V':   '20 V',
         '200V':  '200 V',
         '750V':  '750 V',
    },
    'SDM3065X': {
        '200MV': '200 mV',
           '2V':    '2 V',
          '20V':   '20 V',
         '200V':  '200 V',
         '750V':  '750 V',
    },

}
_RANGE_VAC_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                   for model, x in _RANGE_VAC_SCPI_WRITE_TO_DISP.items()}

_RANGE_VDC_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
           0.6:  '600MV',
           6.0:     '6V',
          60.0:    '60V',
         600.0:   '600V',
        1000.0:  '1000V',
    },
    'SDM3055': {
           0.2:  '200MV',
           2.0:     '2V',
          20.0:    '20V',
         200.0:   '200V',
        1000.0:  '1000V',
    },
    'SDM3065X': {
           0.2:  '200MV',
           2.0:     '2V',
          20.0:    '20V',
         200.0:   '200V',
        1000.0:  '1000V',
    }
}
_RANGE_VDC_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
        '600MV': '600 mV',
           '6V':    '6 V',
          '60V':   '60 V',
         '600V':  '600 V',
        '1000V': '1000 V'
    },
    'SDM3055': {
        '200MV': '200 mV',
           '2V':    '2 V',
          '20V':   '20 V',
         '200V':  '200 V',
        '1000V': '1000 V'
    },
    'SDM3065X': {
        '200MV': '200 mV',
           '2V':    '2 V',
          '20V':   '20 V',
         '200V':  '200 V',
        '1000V': '1000 V'
    },
}
_RANGE_VDC_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                   for model, x in _RANGE_VDC_SCPI_WRITE_TO_DISP.items()}

_RANGE_IAC_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
         0.06:    '60MA',
         0.6:    '600MA',
         6.0:       '6A',
        10.0:      '10A'
    },
    'SDM3055': {
         0.02:    '20MA',
         0.2:    '200MA',
         2.0:       '2A',
        10.0:      '10A'
    },
    'SDM3065X': {
         0.0002: '200UA',
         0.002:    '2MA',
         0.02:    '20MA',
         0.2:    '200MA',
         2.0:       '2A',
        10.0:      '10A'
    }
}
_RANGE_IAC_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
         '60MA':  '60 mA',
        '600MA': '600 mA',
           '6A':    '6 A',
          '10A':    '10 A'

    },
    'SDM3055': {
        '200UA': '200 \u00B5A',
          '2MA':   '2 mA',
         '20MA':  '20 mA',
        '200MA': '200 mA',
           '2A':    '2 A',
          '10A':   '10 A'
    },
    'SDM3065X': {
        '200UA': '200 \u00B5A',
          '2MA':   '2 mA',
         '20MA':  '20 mA',
        '200MA': '200 mA',
           '2A':    '2 A',
          '10A':   '10 A'
    }
}
_RANGE_IAC_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                   for model, x in _RANGE_IAC_SCPI_WRITE_TO_DISP.items()}

_RANGE_IDC_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
         0.0006: '600UA',
         0.006:    '6MA',
         0.06:    '60MA',
         0.6:    '600MA',
         6.0:       '6A',
        10.0:      '10A'
    },
    'SDM3055': {
         0.0002: '200UA',
         0.002:    '2MA',
         0.02:    '20MA',
         0.2:    '200MA',
         2.0:       '2A',
        10.0:      '10A'
    },
    'SDM3065X': {
         0.0002: '200UA',
         0.002:    '2MA',
         0.02:    '20MA',
         0.2:    '200MA',
         2.0:       '2A',
        10.0:      '10A'
    }
}
_RANGE_IDC_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
        '600UA': '600 \u00B5A',
          '6MA':        '6 mA',
         '60MA':       '60 mA',
        '600MA':      '600 mA',
           '6A':         '6 A',
          '10A':        '10 A'
    },
    'SDM3055': {
        '200UA': '200 \u00B5A',
          '2MA':        '2 mA',
         '20MA':       '20 mA',
        '200MA':      '200 mA',
           '2A':         '2 A',
          '10A':        '10 A'
    },
    'SDM3065X': {
        '200UA': '200 \u00B5A',
          '2MA':        '2 mA',
         '20MA':       '20 mA',
        '200MA':      '200 mA',
           '2A':         '2 A',
          '10A':        '10 A'
    }
}
_RANGE_IDC_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                   for model, x in _RANGE_IDC_SCPI_WRITE_TO_DISP.items()}

_RANGE_R_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
              600.0:  '600OHM',
             6000.0:   '6KOHM',
            60000.0:  '60KOHM',
           600000.0: '600KOHM',
          6000000.0:   '6MOHM',
         60000000.0:  '60MOHM',
        100000000.0: '100MOHM'
    },
    'SDM3055': {
              200.0:  '200OHM',
             2000.0:   '2KOHM',
            20000.0:  '20KOHM',
           200000.0: '200KOHM',
          2000000.0:   '2MOHM',
         10000000.0:  '10MOHM',
        100000000.0: '100MOHM'
    },
    'SDM3065X': {
              200.0:  '200OHM',
             2000.0:   '2KOHM',
            20000.0:  '20KOHM',
           200000.0: '200KOHM',
          1000000.0:   '1MOHM',
         10000000.0:  '10MOHM',
        100000000.0: '100MOHM'

    }
}
_RANGE_R_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
         '600OHM': '600 \u2126',
          '6KOHM':   '6 k\u2126',
         '60KOHM':  '60 k\u2126',
        '600KOHM': '600 k\u2126',
          '6MOHM':   '6 M\u2126',
         '60MOHM':  '60 M\u2126',
        '100MOHM': '100 M\u2126'

    },
    'SDM3055': {
         '200OHM': '200 \u2126',
          '2KOHM':   '2 k\u2126',
         '20KOHM':  '20 k\u2126',
        '200KOHM': '200 k\u2126',
          '2MOHM':   '2 M\u2126',
         '10MOHM':  '10 M\u2126',
        '100MOHM': '100 M\u2126'
    },
    'SDM3065X': {
         '200OHM': '200 \u2126',
          '2KOHM':   '2 k\u2126',
         '20KOHM':  '20 k\u2126',
        '200KOHM': '200 k\u2126',
          '1MOHM':   '1 M\u2126',
         '10MOHM':  '10 M\u2126',
        '100MOHM': '100 M\u2126'
    }
}
_RANGE_R_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                 for model, x in _RANGE_R_SCPI_WRITE_TO_DISP.items()}

_RANGE_C_SCPI_READ_TO_WRITE = {
    'SDM3045X': {
        0.000000002:     '2NF',
        0.000000020:    '20NF',
        0.000000200:   '200NF',
        0.000002000:     '2UF',
        0.000020000:    '20UF',
        0.000200000:   '200UF',
        0.010000000: '10000UF'
    },
    'SDM3055': {
        0.000000002:     '2NF',
        0.000000020:    '20NF',
        0.000000200:   '200NF',
        0.000002000:     '2UF',
        0.000020000:    '20UF',
        0.000200000:   '200UF',
        0.010000000: '10000UF'
    },
    'SDM3065X': {
        0.000000002:   '2NF',
        0.000000020:  '20NF',
        0.000000200: '200NF',
        0.000002000:   '2UF',
        0.000020000:  '20UF',
        0.000200000: '200UF',
        0.002000000:   '2MF',
        0.020000000:  '20MF',
        0.100000000: '100MF'
    }
}
_RANGE_C_SCPI_WRITE_TO_DISP = {
    'SDM3045X': {
            '2NF':     '2 nF',
           '20NF':    '20 nF',
          '200NF':   '200 nF',
            '2UF':     '2 \u00B5F',
           '20UF':    '20 \u00B5F',
          '200UF':   '200 \u00B5F',
        '10000UF': '10000 \u00B5F'
    },
    'SDM3055': {
            '2NF':     '2 nF',
           '20NF':    '20 nF',
          '200NF':   '200 nF',
            '2UF':     '2 \u00B5F',
           '20UF':    '20 \u00B5F',
          '200UF':   '200 \u00B5F',
        '10000UF': '10000 \u00B5F'
    },
    'SDM3065X': {
            '2NF':     '2 nF',
           '20NF':    '20 nF',
          '200NF':   '200 nF',
            '2UF':     '2 \u00B5F',
           '20UF':    '20 \u00B5F',
          '200UF':   '200 \u00B5F',
            '2MF':     '2 mF',
           '20MF':    '20 mF',
          '100MF':   '100 mF'
    }
}
_RANGE_C_DISP_TO_SCPI_WRITE = {model: {value: key for key, value in x.items()}
                                 for model, x in _RANGE_C_SCPI_WRITE_TO_DISP.items()}


# This class encapsulates the main SDM configuration widget.

class InstrumentSiglentSDM3000ConfigureWidget(ConfigureWidgetBase):
    # The number of possible paramsets. If this is set to something other than
    # 4, the View menu will need to be updated.
    _NUM_PARAMSET = 4

    def __init__(self, *args, **kwargs):
        # Override the widget registry to be paramset-specific.
        self._widget_registry = [{} for i in range(self._NUM_PARAMSET+1)]

        # The current state of all SCPI parameters. String values are always stored
        # in upper case! Entry 0 is for global values and the current instrument state,
        # and 1-N are for stored paramsets.
        self._param_state = [{} for i in range(self._NUM_PARAMSET+1)]
        self._config_lock = asyncio.Lock()

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

        self._initialize_measurements_and_triggers()

        self._measurement_interval = 0 #250 # ms


    ######################
    ### Public methods ###
    ######################

    # This reads instrument -> _param_state
    async def refresh(self):
        """Read all parameters from the instrument and set our internal state to match."""
        async with self._config_lock:
            try:
                # Start with a blank slate
                self._param_state = [{} for i in range(self._NUM_PARAMSET+1)]
                for mode, info in _SDM_MODE_PARAMS.items():
                    # Loaded paramset entries go in index 1
                    idx = 0 if mode == 'Global' else 1
                    for param_spec in info['params']:
                        param0 = self._scpi_cmds_from_param_info(info, param_spec)
                        if param0 is None:
                            continue
                        if param0 in self._param_state[idx]:
                            # Modes often ask for the same data, no need to retrieve it twice
                            continue
                        val = await self._inst.query(f'{param0}?')
                        param_type = param_spec[1]
                        if param_type[0] == '.': # Handle .3f
                            param_type = param_type[-1]
                        match param_type:
                            case 'f': # Float
                                val = float(val)
                            case 'b' | 'd': # Boolean or Decimal
                                val = int(float(val))
                            case 's' | 'r': # String or radio button
                                # The SDM3000 wraps function strings in double qoutes for
                                # some reason
                                val = val.strip('"').upper()
                            case 'rvac': # Voltage range
                                val = self._range_vac_scpi_read_to_scpi_write(val)
                            case 'rvdc': # Voltage range
                                val = self._range_vdc_scpi_read_to_scpi_write(val)
                            case 'riac': # Current range
                                val = self._range_iac_scpi_read_to_scpi_write(val)
                            case 'ridc': # Current range
                                val = self._range_idc_scpi_read_to_scpi_write(val)
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

                    self._inst._logger.debug('** REFRESH / PARAMSET')
                    for i, param_state in enumerate(self._param_state):
                        self._inst._logger.debug(f'{i}: {self._param_state[i]}')

                # Since everything has changed, update all the widgets
                self._update_all_widgets()
            except NotConnected:
                return
            except InstrumentClosed:
                await self._actually_close()
                return
            except ConnectionLost:
                await self._connection_lost()
                return

    # This writes _param_state -> instrument (opposite of refresh)
    async def _update_instrument(self, paramset_num=0, prev_state=None):
        """Update the instrument with the current _param_state.

        Does not lock.
        """
        ltd_param_state = self._limited_param_state(paramset_num)
        for key, val in ltd_param_state.items():
            if prev_state.get(key, None) != val:
                await self._update_one_param_on_inst(key, val)
        return ltd_param_state

    def _initialize_measurements_and_triggers(self):
        """Initialize the measurements and triggers cache with names and formats."""
        triggers = {}
        measurements = {
            'DC Voltage':       {'name':          'DC Voltage',
                                 'unit':          'V',
                                 'format':        '10.6f',
                                 'display_units': (
                                     (1,    1e3,  '7.3f',  'mVDC'),
                                     (None, 1,    '10.6f', 'VDC')
                                 ),
                                 'val':           None},
            'AC Voltage':       {'name':         'AC Voltage',
                                 'unit':         'V',
                                 'display_unit': 'VAC',
                                 'format':       '10.6f',
                                 'display_units': (
                                     (1,    1e3,  '7.3f',  'mVAC'),
                                     (None, 1,    '10.6f', 'VAC')
                                 ),
                                 'val':          None},
            'DC Current':       {'name':         'DC Current',
                                 'unit':         'A',
                                 'format':       '10.6f',
                                 'display_units': (
                                     (1,    1e3,  '7.3f',  'mADC'),
                                     (None, 1,    '10.6f', 'ADC')
                                 ),
                                 'val':          None},
            'AC Current':       {'name':         'AC Current',
                                 'unit':         'A',
                                 'format':       '10.6f',
                                 'display_units': (
                                     (1,    1e3,  '7.3f',  'mADC'),
                                     (None, 1,    '10.6f', 'ADC')
                                 ),
                                 'val':          None},
            '2-W Resistance':   {'name':          '2-W Resistance',
                                 'unit':          '\u2126',
                                 'format':        '13.6f',
                                 'display_units': (
                                     (1e3,  1,    '7.3f',  '\u2126'),
                                     (1e6,  1e-3, '7.3f',  'k\u2126'),
                                     (None, 1e-6, '7.3f',  'M\u2126')
                                 ),
                                 'val':    None},
            '4-W Resistance':   {'name':   '4-W Resistance',
                                 'unit':   '\u2126',
                                 'format': '13.6f',
                                 'display_units': (
                                     (1e3,  1,    '7.3f',  '\u2126'),
                                     (1e6,  1e-3, '7.3f',  'k\u2126'),
                                     (None, 1e-6, '7.3f',  'M\u2126')
                                 ),
                                 'val':    None},
            'Capacitance':      {'name':   'Capacitance',
                                 'unit':   'F',
                                 'format': '14.12f',
                                 'display_units': ( # Max 10,000.000 uF
                                     (1e-6, 1e9, '7.3f', 'nF'),
                                     (None, 1e6, '9.3f', '\u00B5F')
                                 ),
                                 'val':    None},
            # Freq/Per range 1 Hz - 4.1 MHz (empirical) = 1 s - 243.90 nS
            'Frequency':        {'name':   'Frequency',
                                 'unit':   'Hz',
                                 'format': '13.5f',
                                 'display_units': ( # Max 4.2 MHz
                                     (1e3,  1,    '9.5f', 'Hz'),
                                     (1e6,  1e-3, '9.5f', 'kHz'),
                                     (None, 1e-6, '9.5f', 'MHz')
                                 ),
                                 'val':    None},
            'Period':           {'name':   'Period',
                                 'unit':   'ms',
                                 'format': '14.12f',
                                 'display_units': (
                                     (1e-6, 1e9,  '9.5f', 'nS'),
                                     (1e-3, 1e6,  '9.5f', '\u00B5S'),
                                     (1,    1e3,  '9.5f', 'mS'),
                                     (None, 1,    '9.5f', 'S')
                                 ),
                                 'val':    None},
            'Temperature':      {'name':   'Temperature', # XXX
                                 'unit':   'K',
                                 'format': '13.6f',
                                 'display_units': (
                                     (None, 1,    '13.6f', 'K'), # XXX
                                 ),
                                 'val':    None},
            'Continuity':       {'name':   'Continuity', # XXX
                                 'unit':   '\u2126',
                                 'format': '13.6f',
                                 'display_units': (
                                     (None, 1,    '13.6f', '\u2126'), # XXX
                                 ),
                                 'val':    None},
            'Diode':            {'name':   'Diode', # XXX
                                 'unit':   'V',
                                 'format': '13.6f',
                                 'display_units': (
                                     (None, 1,    '13.6f', 'V'), # XXX
                                 ),
                                 'val':    None},
        }

        self._cached_triggers = triggers
        self._cached_measurements = measurements

    async def start_measurements(self):
        """Start the measurement loop."""
        await self._update_measurements_and_triggers()

    @asyncSlot()
    async def _update_measurements_and_triggers(self, read_inst=True):
        """Read current values, update control panel display, return the values."""
        # self._inst._logger.debug('** MEASUREMENTS / PARAMSET')
        # for i, param_state in enumerate(self._param_state):
        #     self._inst._logger.debug(f'{i}: {param_state}')

        triggers = self._cached_triggers
        measurements = self._cached_measurements

        for paramset_num in range(1, self._NUM_PARAMSET+1):
            async with self._config_lock:
                try:
                    # Hold the lock for one complete instrument update/read cycle
                    if (paramset_num != 1 and
                        not self._widget_registry[paramset_num]['Enable'].isChecked()):
                        continue
                    # Update the instrument for the current paramset
                    self._last_measurement_param_state = (
                        await self._update_instrument(paramset_num,
                                                      self._last_measurement_param_state))
                    val = float(await self._inst.query('READ?'))
                    if abs(val) == 9.9e37:
                        val = None
                    mode = self._scpi_to_mode(self._param_state[paramset_num][':FUNCTION'])
                    measurements[mode]['val'] = val
                    if val is None:
                        text = 'Overload'
                    else:
                        disp_units = measurements[mode]['display_units']
                        for threshold, scale, fmt, units in disp_units:
                            if threshold is None:
                                break
                            if val < threshold:
                                break
                        else:
                            assert False, val
                        text = ('%' + fmt) % (val * scale)
                        text += f' {units}'
                    self._widget_registry[paramset_num]['Measurement'].setText(text)
                except NotConnected:
                    return
                except InstrumentClosed:
                    await self._actually_close()
                    return
                except ConnectionLost:
                    await self._connection_lost()
                    return

        # Wait until all measurements have been made and then update the display
        # widgets all at once so it looks like they were done simultaneously.
        # Once we've gone through the whole measurement series, schedule it to
        # run again soon.
        QTimer.singleShot(self._measurement_interval,
                          self._update_measurements_and_triggers)

    def get_measurements(self):
        """Return most recently cached measurements."""
        return self._cached_measurements

    def get_triggers(self):
        """Return most recently cached triggers."""
        return self._cached_triggers

    ############################################################################
    ### Setup Window Layout
    ############################################################################

    def _init_widgets(self, measurements_only=False):
        """Set up all the toplevel widgets."""
        toplevel_widget = self._toplevel_widget()

        ### Add to Device menu

        ### Add to View menu

        # If this changes, the menu needs to be updated
        # assert self._NUM_PARAMSET == 4 XXX
        action = QAction('&Parameters #1', self, checkable=True)
        action.setShortcut(QKeySequence('Ctrl+1'))
        action.setChecked(not measurements_only)
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

        # action = QAction('&Keyboard Shortcuts...', self)
        # action.triggered.connect(self._menu_do_keyboard_shortcuts)
        # self._menubar_help.addAction(action)

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

            if paramset_num > 1 or measurements_only:
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
                             'Temperature'
                             )):
                layoutv = QVBoxLayout()
                layoutv.setSpacing(10)
                layouth.addLayout(layoutv)
                for mode in columns:
                    rb = QRadioButton(mode)
                    layoutv.addWidget(rb)
                    bg.addButton(rb)
                    rb.button_group = bg
                    rb.wid = (paramset_num, mode)
                    rb.clicked.connect(self._on_click_overall_mode)
                    self._widget_registry[paramset_num][f'Overall_{mode}'] = rb

            layouts.addStretch()

            ### ROWS 1-4, COLUMN 2 ###

            layouts = QVBoxLayout()
            layouts.setSpacing(0)
            row_layout.addLayout(layouts)

            # V/I/R/C Range selections
            for row_num, (mode, ranges) in enumerate(
                (('Voltage:DC',
                  list(_RANGE_VDC_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Voltage:AC',
                  list(_RANGE_VAC_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Current:DC',
                  list(_RANGE_IDC_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Current:AC',
                  list(_RANGE_IAC_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Resistance:2W',
                  list(_RANGE_R_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Resistance:4W',
                  list(_RANGE_R_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Capacitance',
                  list(_RANGE_C_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Frequency:Voltage',
                  list(_RANGE_VAC_DISP_TO_SCPI_WRITE[self._inst._model].keys())),
                 ('Period:Voltage',
                  list(_RANGE_VAC_DISP_TO_SCPI_WRITE[self._inst._model].keys())))):
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
                    rb.clicked.connect(self._on_click_range)
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
                rb.clicked.connect(self._on_click_speed)
                layouth2.addWidget(rb)
                self._widget_registry[paramset_num][f'Speed_{speed}'] = rb
            layouth2.addStretch()

            if self._inst._model in ('SDM3045X', 'SDM3055'):
                # DC Filter selection
                layouth2 = QHBoxLayout()
                layoutv2.addLayout(layouth2)
                w = QCheckBox('DC Filter')
                w.wid = paramset_num
                layouth2.addWidget(w)
                w.clicked.connect(self._on_click_dcfilter)
                self._widget_registry[paramset_num]['DCFilter'] = w

            if self._inst._model == 'SDM3065X':
                # Auto-zero selection
                layouth2 = QHBoxLayout()
                layoutv2.addLayout(layouth2)
                w = QCheckBox('Auto Zero')
                w.wid = paramset_num
                layouth2.addWidget(w)
                w.clicked.connect(self._on_click_autozero)
                self._widget_registry[paramset_num]['AutoZero'] = w

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
                rb.clicked.connect(self._on_click_impedance)
                layouth2.addWidget(rb)
                self._widget_registry[paramset_num][f'Impedance_{imp}'] = rb
            layouth2.addStretch()

            layouts.addStretch()

            # ### ROWS 1-4, COLUMN 3 ###

            # layouts = QVBoxLayout()
            # layouts.setSpacing(0)
            # row_layout.addLayout(layouts)

            # # Relative measurement
            # frame = QGroupBox(f'Relative To')
            # self._widget_registry[paramset_num]['FrameRelative'] = frame
            # layouts.addWidget(frame)
            # layoutv2 = QVBoxLayout(frame)

            # # Relative measurement mode on
            # w = QCheckBox('Relative Mode On')
            # w.wid = paramset_num
            # layoutv2.addWidget(w)
            # w.clicked.connect(self._on_click_rel_mode_on)
            # self._widget_registry[paramset_num]['RelModeOn'] = w

            # layouth2 = QHBoxLayout()
            # label = QLabel('Relative Value:')
            # layouth2.addWidget(label)
            # input = MultiSpeedSpinBox(1.)
            # input.wid = paramset_num
            # input.setAlignment(Qt.AlignmentFlag.AlignRight)
            # input.setDecimals(3)
            # input.setAccelerated(True)
            # input.editingFinished.connect(self._on_value_change_rel_mode)
            # layouth2.addWidget(input)
            # label.sizePolicy().setRetainSizeWhenHidden(True)
            # input.sizePolicy().setRetainSizeWhenHidden(True)
            # layoutv2.addLayout(layouth2)
            # self._widget_registry[paramset_num]['RelModeVal'] = input

            # # Relative value source
            # layouth2 = QHBoxLayout()
            # layoutv2.addLayout(layouth2)
            # w = QLabel('Value source:')
            # layouth2.addWidget(w)
            # self._widget_registry[paramset_num]['RelModeSourceLabel'] = w
            # bg = QButtonGroup(layouth2)
            # for imp in ('Manual', 'Last', 'Last 10'):
            #     rb = QRadioButton(imp)
            #     bg.addButton(rb)
            #     rb.button_group = bg
            #     rb.wid = (paramset_num, imp)
            #     rb.clicked.connect(self._on_click_rel_mode_source)
            #     layouth2.addWidget(rb)
            #     self._widget_registry[paramset_num][f'RelModeSource_{imp}'] = rb
            # layouth2.addStretch()

            # layouts.addStretch()

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
        msg = f"""Siglent SDM3000-series instrument interface ({VERSION}).

Copyright 2023, Robert S. French.

Supported instruments: {supported}.

Connected to {self._inst.resource_name}
    {self._inst.model}
    S/N {self._inst.serial_number}
    FW {self._inst.firmware_version}"""

        QAsyncMessageBox.about(self, 'About', msg)

    def _menu_do_keyboard_shortcuts(self):
        """Show the Keyboard Shortcuts."""
        msg = """XXX TBD
"""
        QAsyncMessageBox.about(self, 'Keyboard Shortcuts', msg)

    @asyncSlot()
    async def _menu_do_save_configuration(self):
        """Save the current configuration to a file."""
        fn = await QAsyncFileDialog.getSaveFileName(
            self, caption='Save Configuration',
            filter='SDM Configuration (*.sdmcfg)',
            selectedFilter='SDM Configuration (*.sdmcfg)',
            defaultSuffix='.sdmcfg')
        if not fn or not fn[0]:
            return
        fn = fn[0]
        async with self._config_lock:
            ps = self._param_state.copy()
            with open(fn, 'w') as fp:
                json.dump(ps, fp, sort_keys=True, indent=4)

    @asyncSlot()
    async def _menu_do_load_configuration(self):
        """Load the current configuration from a file."""
        fn = await QAsyncFileDialog.getOpenFileName(
            self, caption='Load Configuration',
            filter='SDM Configuration (*.sdmcfg);;All (*.*)',
            selectedFilter='SDM Configuration (*.sdmcfg)')
        if not fn or not fn[0]:
            return
        fn = fn[0]
        async with self._config_lock:
            with open(fn, 'r') as fp:
                ps = json.load(fp)
            # Retrieve the List mode parameters
            self._param_state = ps
            # Clean up the param state. We don't want to start with the load or short on.
            await self._update_instrument()
            self._update_all_widgets()

    @asyncSlot()
    async def _menu_do_reset_device(self):
        """Reset the instrument and then reload the state."""
        # A reset takes around 6.75 seconds, so we wait up to 10s to be safe.
        async with self._config_lock:
            try:
                self.setEnabled(False)
                self.repaint()
                await self._inst.write('*RST')
                self.refresh()
                self.setEnabled(True)
            except NotConnected:
                return
            except InstrumentClosed:
                await self._actually_close()
                return
            except ConnectionLost:
                await self._connection_lost()
                return

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

    @asyncSlotSender()
    async def _on_click_overall_mode(self, rb):
        """Handle clicking on an Overall Mode button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not rb.isChecked():
            return
        paramset_num, mode = rb.wid
        self._inst._logger.debug(f'Set overall mode #{paramset_num}')
        async with self._config_lock:
            self._param_state[paramset_num][':FUNCTION'] = self._mode_to_scpi(mode)
            self._inst._logger.debug(f'  :FUNCTION="{self._mode_to_scpi(mode)}" ({mode})')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_range(self, rb):
        """Handle clicking on a V/I/R/C range button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        self._inst._logger.debug(f'Set range #{paramset_num}')
        async with self._config_lock:
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            orig_val = val
            match mode_name:
                case 'FREQ':
                    mode_name = 'FREQ:VOLT'
                    val = self._range_vac_disp_to_scpi_write(val)
                case 'PER':
                    mode_name = 'PER:VOLT'
                    val = self._range_vac_disp_to_scpi_write(val)
                case 'VOLT:AC':
                    val = self._range_vac_disp_to_scpi_write(val)
                case 'VOLT:DC':
                    val = self._range_vdc_disp_to_scpi_write(val)
                case 'CURR:AC':
                    val = self._range_iac_disp_to_scpi_write(val)
                case 'CURR:DC':
                    val = self._range_idc_disp_to_scpi_write(val)
                case 'RES' | 'FRES':
                    val = self._range_r_disp_to_scpi_write(val)
                case 'CAP':
                    val = self._range_c_disp_to_scpi_write(val)
            self._param_state[paramset_num][f':{mode_name}:RANGE'] = val
            if mode_name == 'FREQ:VOLT': # Shared parameter
                self._param_state[paramset_num][f':PER:VOLT:RANGE'] = val
            elif mode_name == 'PER:VOLT':
                self._param_state[paramset_num][f':FREQ:VOLT:RANGE'] = val
            self._inst._logger.debug(f'  :{mode_name}:RANGE="{val}" ({orig_val})')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_range_auto(self, cb):
        """Handle clicking on a V/I/R/C Auto range checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        paramset_num = cb.wid
        self._inst._logger.debug(f'Set range auto #{paramset_num}')
        async with self._config_lock:
            val = int(cb.isChecked())
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            if mode_name == 'FREQ':
                mode_name = 'FREQ:VOLT'
            elif mode_name == 'PER':
                mode_name = 'PER:VOLT'
            self._param_state[paramset_num][f':{mode_name}:RANGE:AUTO'] = val
            if mode_name == 'FREQ:VOLT': # Shared parameter
                self._param_state[paramset_num][f':PER:VOLT:RANGE:AUTO'] = val
            elif mode_name == 'PER:VOLT':
                self._param_state[paramset_num][f':FREQ:VOLT:RANGE:AUTO'] = val
            self._inst._logger.debug(f'  :{mode_name}:RANGE:AUTO="{val}')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_speed(self, rb):
        """Handle clicking on a speed button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        self._inst._logger.debug(f'Set speed #{paramset_num}')
        async with self._config_lock:
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            scpi_val = self._speed_disp_to_scpi_write(val)
            self._param_state[paramset_num][f':{mode_name}:NPLC'] = scpi_val
            self._inst._logger.debug(f'  :{mode_name}:NPLC="{scpi_val} ({val})')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_dcfilter(self, cb):
        """Handle clicking on the DC Filter checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        paramset_num = cb.wid
        self._inst._logger.debug(f'Set DC filter #{paramset_num}')
        async with self._config_lock:
            val = cb.isChecked()
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            self._param_state[paramset_num][f':{mode_name}:FILTER:STATE'] = val
            self._inst._logger.debug(f'  :{mode_name}:FILTER:STATE="{val}"')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_autozero(self, cb):
        """Handle clicking on the Auto Zero checkbox."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        paramset_num = cb.wid
        self._inst._logger.debug(f'Set Auto Zero #{paramset_num}')
        async with self._config_lock:
            val = cb.isChecked()
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            self._param_state[paramset_num][f':{mode_name}:AZ:STATE'] = val
            self._inst._logger.debug(f'  :{mode_name}:AZ:STATE="{val}"')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    @asyncSlotSender()
    async def _on_click_impedance(self, rb):
        """Handle clicking on an impedance button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not rb.isChecked():
            return
        paramset_num, val = rb.wid
        self._inst._logger.debug(f'Set impedance #{paramset_num}')
        async with self._config_lock:
            info = self._cur_mode_param_info(paramset_num)
            mode_name = info['mode_name']
            self._param_state[paramset_num][f':{mode_name}:IMP'] = val
            self._inst._logger.debug(f'  :{mode_name}:IMP="{val}')
            self._inst._logger.debug(str(self._param_state[paramset_num]))
            self._update_widgets(paramset_num)

    def _on_click_rel_mode_on(self):
        """Handle clicking on the relative mode on checkbox."""
        pass # XXX

    def _on_click_rel_mode_source(self):
        """Handle clicking on a relative mode source radio button."""
        pass # XXX

    def _on_value_change_rel_mode(self):
        """Handling entering a value for the relative mode."""
        pass # XXX

    @asyncSlotSender()
    async def _on_value_change(self, input):
        """Handle clicking on any input value edit box."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        assert False # XXX
        async with self._config_lock:
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

    @asyncSlotSender()
    async def _on_click_trigger_source(self, rb):
        """Handle clicking on a trigger source button."""
        if self._disable_callbacks: # Prevent recursive calls
            return
        if not rb.isChecked():
            return
        async with self._config_lock:
            new_param_state = {':TRIGGER:SOURCE': rb.mode.upper()}
            self._update_global_param_state_and_inst(new_param_state)
            self._update_trigger_buttons() # XXX


    ################################
    ### Internal helper routines ###
    ################################

    def _cmd_for_instrument(self, cmd):
        """Check if the given SCPI cmd is for the current instrument."""
        ind = cmd.find('/')
        if ind == -1: # Valid for all instruments
            return cmd
        inst_list = cmd[:ind]
        cmd = cmd[ind+1:]
        if ((self._inst._model == 'SDM3045X' and '4' in inst_list) or
            (self._inst._model == 'SDM3055'  and '5' in inst_list) or
            (self._inst._model == 'SDM3065X' and '6' in inst_list)):
            return cmd
        return None

    def _scpi_cmds_from_param_info(self, param_info, param_spec):
        """Create a SCPI command from a param_info structure."""
        mode_name = param_info['mode_name']
        if mode_name is None: # General parameters
            mode_name = ''
        else:
            mode_name = f':{mode_name}:'
        ps1 = param_spec[0]
        ps1 = self._cmd_for_instrument(ps1)
        if ps1 is None:
            return None
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

    async def _put_inst_in_mode(self, mode):
        """Place the SDM in the given mode.

        Does not lock.
        """
        param = self._mode_to_scpi(mode)
        await self._inst.write(f':FUNCTION "{param}"')

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

    def _range_vac_scpi_read_to_scpi_write(self, param):
        """Convert an AC Voltage SCPI read range to a Voltage write range."""
        return _RANGE_VAC_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_vdc_scpi_read_to_scpi_write(self, param):
        """Convert a DC Voltage SCPI read range to a Voltage write range."""
        return _RANGE_VDC_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_vac_scpi_write_to_disp(self, range):
        """Convert an AC Voltage SCPI write range to a display range."""
        return _RANGE_VAC_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_vdc_scpi_write_to_disp(self, range):
        """Convert a DC Voltage SCPI write range to a display range."""
        return _RANGE_VDC_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_vac_disp_to_scpi_write(self, range):
        """Convert an AC Voltage display range to a SCPI write range."""
        return _RANGE_VAC_DISP_TO_SCPI_WRITE[self._inst._model][range]

    def _range_vdc_disp_to_scpi_write(self, range):
        """Convert a DC Voltage display range to a SCPI write range."""
        return _RANGE_VDC_DISP_TO_SCPI_WRITE[self._inst._model][range]

    def _range_iac_scpi_read_to_scpi_write(self, param):
        """Convert an AC Current SCPI read range to a Current write range."""
        return _RANGE_IAC_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_idc_scpi_read_to_scpi_write(self, param):
        """Convert a DC Current SCPI read range to a Current write range."""
        return _RANGE_IDC_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_iac_scpi_write_to_disp(self, range):
        """Convert an AC Current SCPI write range to a display range."""
        return _RANGE_IAC_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_idc_scpi_write_to_disp(self, range):
        """Convert a DC Current SCPI write range to a display range."""
        return _RANGE_IDC_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_iac_disp_to_scpi_write(self, range):
        """Convert an AC Current display range to a SCPI write range."""
        return _RANGE_IAC_DISP_TO_SCPI_WRITE[self._inst._model][range]

    def _range_idc_disp_to_scpi_write(self, range):
        """Convert a DC Current display range to a SCPI write range."""
        return _RANGE_IDC_DISP_TO_SCPI_WRITE[self._inst._model][range]

    def _range_r_scpi_read_to_scpi_write(self, param):
        """Convert a Resistance SCPI read range to a SCPI write range."""
        return _RANGE_R_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_r_scpi_write_to_disp(self, range):
        """Convert a Resistance SCPI write range to a display range."""
        return _RANGE_R_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_r_disp_to_scpi_write(self, range):
        """Convert a Resistance display range to a SCPI write range."""
        return _RANGE_R_DISP_TO_SCPI_WRITE[self._inst._model][range]

    def _range_c_scpi_read_to_scpi_write(self, param):
        """Convert a Capacitance SCPI read range to a SCPI write range."""
        return _RANGE_C_SCPI_READ_TO_WRITE[self._inst._model][float(param)]

    def _range_c_scpi_write_to_disp(self, range):
        """Convert a Capacitance SCPI write range to a display range."""
        return _RANGE_C_SCPI_WRITE_TO_DISP[self._inst._model][range]

    def _range_c_disp_to_scpi_write(self, range):
        """Convert a Capacitance display range to a SCPI write range."""
        return _RANGE_C_DISP_TO_SCPI_WRITE[self._inst._model][range]

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
            param_scpi = self._cmd_for_instrument(param[0])
            if param_scpi is None:
                continue
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
        # if self._debug:
        #     print('Disable callbacks True')
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
            scpi_cmd = self._cmd_for_instrument(scpi_cmd)
            if scpi_cmd is None:
                continue
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
                    if self._inst._model == 'SDM3045X':
                        imp_list = ('600MV',)
                    elif self._inst._model == 'SDM3055':
                        imp_list = ('200MV', '2V')
                    else:
                        assert self._inst._model == 'SDM3065X'
                        imp_list = ('200MV', '2V', '20V')
                    if (self._param_state[paramset_num][':VOLT:DC:RANGE:AUTO'] or
                        self._param_state[paramset_num][':VOLT:DC:RANGE']
                            not in imp_list):
                        continue
                self._widget_registry[paramset_num][widget_label].show()
                self._widget_registry[paramset_num][widget_label].setEnabled(True)

            # XXX Review this entire section
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
                    case 'r' | 'rvac' | 'rvdc' | 'riac' | 'ridc' | 'rr' | 'rc' | 'rs':
                        # Radio button
                        if param_type == 'rvac':
                            val = self._range_vac_scpi_write_to_disp(val)
                        if param_type == 'rvdc':
                            val = self._range_vdc_scpi_write_to_disp(val)
                        elif param_type == 'riac':
                            val = self._range_iac_scpi_write_to_disp(val)
                        elif param_type == 'ridc':
                            val = self._range_idc_scpi_write_to_disp(val)
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
                                # print(f'Enabled #{paramset_num} {trial_widget}')
                                widget.setEnabled(True)
                                checked = (trial_widget.upper()
                                           .endswith('_'+str(val).upper()))
                                # print(f'Checked {checked}  #{paramset_num} {trial_widget}')
                                widget.setChecked(checked)
                    case _:
                        assert False, f'Unknown param type {param_type}'

        # XXX self._update_param_state_and_inst(paramset_num, new_param_state)

        status_msg = None
        if status_msg is None:
            self._statusbar.clearMessage()
        else:
            self._statusbar.showMessage(status_msg)

        self._disable_callbacks = False
        # if self._debug:
        #     print('Disable callbacks False')

    def _cur_mode_param_info(self, paramset_num):
        """Get the parameter info structure for the current mode."""
        cur_mode = self._scpi_to_mode(self._param_state[paramset_num][':FUNCTION'])
        return _SDM_MODE_PARAMS[cur_mode]

    async def _update_global_param_state_and_inst(self, new_param_state):
        """Update the internal global state and instrument from partial param_state.

        Does not lock.
        """
        for key, data in new_param_state.items():
            if data != self._param_state[0][key]:
                await self._update_one_param_on_inst(key, data)
                self._param_state[0][key] = data

    async def _update_one_param_on_inst(self, key, data):
        """Update the value for a single parameter on the instrument.

        Does not lock.
        """
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
        await self._inst.write(f'{key} {fmt_data}')


##########################################################################################
# SCPI COMMAND REFERENCE
##########################################################################################
#
# We only include the portions of the SCPI commands that we use. We ignore:
#   ABORT
#   FETCH
#   INIT
#   OUTPUT:TRIGGER:SLOPE
#   R?
#   READ?
#   SAMPLE:COUNT
#   CALC:xxx
#   CONF:xxx
#   DATA:xxx
#   MEAS:xxx
#   TRIG:xxx
#   ROUT:xxx
#   SYST:PRESET
#   SYST:COMMUNICATE
#
##########################################################################################
#
# [SENSe:]FUNCtion[:ON] function
#   CONTinuity   CURRent:AC   CURRent[:DC]
#   DIODe        FREQuency    FRESistance
#   PERiod       RESistance   TEMPerature
#   VOLTage:AC   VOLTage:AC   VOLTage[:DC]
#   CAPacitance
# [SENSe:]FUNCtion[:ON]?
#   The short form of the selected function is returned in
#   quotation marks, with no optional keywords
#     "CONT", "CURR:AC", "DIOD" and so on
#
#
########## CURRENT ##########
#
# [SENSe:]CURRent:{AC|DC}:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]CURRent:{AC|DC}:NULL[:STATe]?
#
# [SENSe:]CURRent:{AC|DC}:NULL:VALue {<value>|MIN|MAX|DEF}
#   -11 to 11 A
#   Disables :AUTO
# [SENSe:]CURRent:{AC|DC}:NULL:VALue? [{MIN|MAX|DEF}]
#
# [SENSe:]CURRent:{AC|DC}:NULL:VALue:AUTO {ON|1|OFF|0}
#   When automatic reference selection is ON, the first measurement made is used as the
#   null value for all subsequent measurements.
# [SENSe:]CURRent:{AC|DC}:NULL:VALue:AUTO?
#
# [SENSe:]CURRent:{AC|DC}:RANGe {<range>|MIN|MAX|DEF}
#   3045X:  {600uA|6mA|60mA|600mA|6A|10A|AUTO}
#       600uA and 6mA only in DC mode, min range 60mA in AC mode
#   3055:   {200uA|2mA|20mA|200mA|2A|10A|AUTO}
#       200uA and 2mA only in DC mode, min range 20mA in AC mode
#   3065X:  {200uA|2mA|20mA|200mA|2A|10A|AUTO}
#   Disables :AUTO
# XXX? Unlike CONFigure and MEASure?, this command does not support the 10 A range
# [SENSe:]CURRent:{AC|DC}:RANGe? [{MIN|MAX|DEF}]
#
# [SENSe:]CURRent:{AC|DC}:RANGe:AUTO {OFF|ON|ONCE}
# [SENSe:]CURRent:{AC|DC}:RANGe:AUTO?
#
# [SENSe:]CURRent[:DC]:NPLC {<PLC>|MIN|MAX|DEF}
#   3045X/3055: {0.3|1|10}
#   3065X:      {100|10|1|0.5|0.05|0.005}
#
# [SENSe:]CURRent[:DC]:NPLC? [{MIN|MAX|DEF}]
#
# 3065X ONLY:
#   [SENSe:]CURRent[:AC]:BANDwidth{VAL|MIN|MAX|DEF}
#     {3Hz|20Hz|200Hz}
#   [SENSe:]CURRent[:AC]:BANDwidth? [{MIN|MAX|DEF}]
#
# 3065X ONLY:
#   [SENSe:]CURRent[:DC]:AZ[:STATe] {ON|1|OFF|0}
#   [SENSe:]CURRent[:DC]:AZ[:STATe]?
#     Auto-zero function
#
# 3045X/3055 ONLY:
#   [SENSe]:CURRent[:DC]:FILTer[:STATe] {ON|1|OFF|0}
#   [SENSe]:CURRent[:DC]:FILTer[:STATe]
#
#
# ########## FREQUENCY/PERIOD ##########
#
# All parameters are shared between frequency and period measurements.
#
# [SENSe:]{FREQuency|PERiod}:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]{FREQuency|PERiod}:NULL[:STATe]?
# [SENSe:]{FREQuency|PERiod}:NULL:VALue {<value>| minimum | maximum | default }
#   -1.2E6 to +1.2E6
#   Disables :AUTO
# [SENSe:]{FREQuency|PERiod}:NULL:VALue? [{MIN|MAX|DEF}]
#
# [SENSe:]{FREQuency|PERiod}:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]{FREQuency|PERiod}:NULL:VALue:AUTO?
#
# [SENSe:]{FREQuency|PERiod}:VOLTage:RANGe {<range>|MIN|MAX|DEF}
#   3045X:      {600 mV|6 V|60 V|600 V|750 V}
#   3055/3065X: {200 mV|2 V|20 V|200 V|750 V}
#   Disables :AUTO
# [SENSe:]{FREQuency|PERiod}:VOLTage:RANGe? [{MIN|MAX|DEF}]
#
# [SENSe:]{FREQuency|PERiod}:VOLTage:RANGe:AUTO {OFF|ON|ONCE}
# [SENSe:]{FREQuency|PERiod}:VOLTage:RANGe:AUTO?
#
# 3065X ONLY:
#   [SENSe:]{FREQuency|PERiod}:APERture {<value>|MIN|MAX|DEF}
#     {1ms|10ms|100ms|1s}
#   [SENSe:]{FREQuency|PERiod}:APERture? [{MIN|MAX|DEF}]
#
#
# ########## RESISTANCE/FRESISTANCE ##########
#
# [SENSe:]{RESistance|FRESistance}:NPLC {<PLC>|MIN|MAX|DEF}
#   3045X/3055: {0.3|1|10}
#   3065X:      {100|10|1|0.5|0.05|0.005}  (50 Hz power)
#
# [SENSe:]{RESistance|FRESistance}:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]{RESistance|FRESistance}:NULL[:STATe]?
#
# [SENSe:]{RESistance|FRESistance}:NULL:VALue {<value>|MIN|MAX|DEF}
#   -110 MOHM to 110 MOHM
#   Disables :AUTO
# [SENSe:]{RESistance|FRESistance}:NULL:VALue? [{MIN|MAX|DEF}]
#
# [SENSe:]{RESistance|FRESistance}:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]{RESistance|FRESistance}:NULL:VALue:AUTO?
#
# [SENSe:]{RESistance|FRESistance}:RANGe {<range>|MIN|MAX|DEF}
#   3045X:  {600 ohm|6 kohm|60 kohm|600 kohm|6 Mohm|60 Mohm|100 Mohm}
#   3055:   {200 ohm|2 kohm|20 kohm|200 kohm|2 Mohm|10 Mohm|100 Mohm}
#   3065X:  {200 ohm|2 kohm|20 kohm|200 kohm|1 Mohm|10 Mohm|100 Mohm}
# [SENSe:]{RESistance|FRESistance}:RANGe? [{MIN|MAX|DEF}]
#
# [SENSe:]{RESistance|FRESistance}:RANGe:AUTO {OFF|ON|ONCE}
# [SENSe:]{RESistance|FRESistance}:RANGe:AUTO?
#
# 3065X ONLY:
#   [SENSe:]{RESistance|FRESistance}:AZ[:STATe] {ON|1|OFF|0}
#   [SENSe:]{RESistance|FRESistance}:AZ[:STATe]?
#
#
# ########## TEMPERATURE ##########
#
# [SENSe:]TEMPerature:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]TEMPerature:NULL[:STATe]?
#
# [SENSe:]TEMPerature:NULL:VALue {<value>|MIN|MAX|DEF}
#   -1.0E15 to +1.0E15
# [SENSe:]TEMPerature:NULL:VALue? [{MIN|MAX|
#
# [SENSe:]TEMPerature:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]TEMPerature:NULL:VALue:AUTO?
#
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer:LIST?
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer
#   RTD:     {PT100|PT1000}
#   THER:    {BITS90|EITS90|JITS90|KITS90|NITS90|RITS90|SITS90|TITS90}
# [SENSe:]TEMPerature:TRANsducer?
#
# [SENSe:]TEMPerature:{UDEFine|MDEFine}:{THER|RTD}:TRANsducer:POINt?
#
#   See UNIT:TEMP
#
#
# ########## VOLTAGE ##########
#
# [SENSe:]VOLTage:{AC|DC}:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]VOLTage:{AC|DC}:NULL[:STATe]?
#
# [SENSe:]VOLTage:{AC|DC}:NULL:VALue {<value>|MIN|MAX|DEF}
#   XXX ALL:        -1200 to +1200 PER SPECIFIC MANUALS
#   3055/3065X:     DCV NULL range: 1100 to +1100 V
#                   ACV NULL range:  825 to + 825 V
#   Disables :AUTO
# [SENSe:]VOLTage:{AC|DC}:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]VOLTage:{AC|DC}:NULL:VALue:AUTO?
#
# [SENSe:]VOLTage:{AC|DC}:RANGe {<range>|MIN|MAX|DEF}
# [SENSe:]VOLTage:{AC|DC}:RANGe? [{MIN|MAX|DEF}]
#   3045X:      {600 mV|6 V|60 V|600 V|750 V /AC/ or 1000 V /DC/}
#   3055/3065X: {200 mV|2 V|20 V|200 V|750 V /AC/ or 1000 V /DC/}
#   Disables :AUTO
#
# [SENSe:]VOLTage:{AC|DC}:RANGe:AUTO {OFF|ON|ONCE}
# [SENSe:]VOLTage:{AC|DC}:RANGe:AUTO?
#
# [SENSe:]VOLTage[:DC]:NPLC {<PLC>|MIN|MAX|DEF}
# [SENSe:]VOLTage[:DC]:NPLC? [{MIN|MAX|
#   3045X/3055: {0.3|1|10}
#   3065X:      {100|10|1|0.5|0.05|0.005}
#
# [SENSe:]VOLTage[:DC]:IMPedance <impedance>
#   {10M|10G}
#   3045X:  600 mV range range only
#   3055:   200 mV and 2 V ranges only
#   3065X:  200 mV, 2 V, and 20 V ranges only
# [SENSe:]VOLTage[:DC]:IMPedance?
#
# 3065X ONLY:
#   [SENSe:]VOLTage[:AC]:BANDwidth {|MIN|MAX|DEF}
#     {3|20|200}
#   [SENSe:]VOLTage[:AC]:BANDwidth? [{MIN|MAX|DEF}]
#
# 3065X ONLY:
#   [SENSe:]VOLTage[:DC]:AZ[:STATe] {ON|1|OFF|0}
#   [SENSe:]VOLTage[:DC]:AZ[:STATe]?
#
# 3045X/3055 ONLY:
#   [SENSe]:VOLTage[:DC]:FILTer[:STATe] {ON|1|OFF|0}
#   [SENSe]:VOLTage[:DC]:FILTer[:STATe]
#
#
# ########## CAPACITANCE ##########
#
# [SENSe:]CAPacitance:NULL[:STATe] {ON|1|OFF|0}
# [SENSe:]CAPacitance:NULL[:STATe]?
#
# [SENSe:]CAPacitance:NULL:VALue {<value>|MIN|MAX|DEF}
# [SENSe:]CAPacitance:NULL:VALue? [{MIN|MAX|DEF}]
#   -12 to +12 mF
#
# [SENSe:]CAPacitance:NULL:VALue:AUTO {ON|1|OFF|0}
# [SENSe:]CAPacitance:NULL:VALue:AUTO?
#
# [SENSe:]CAPacitance:RANGe {<range>|MIN|MAX|DEF}
#   3045X/3055: {2nF|20nF|200nF|2uF|20uF|200uF|10000uF}
#   SDM3065X:   {2nF|20nF|200nF|2uF|20uF|200uF|2mF|20mF|100mF}
#
# [SENSe:]CAPacitance:RANGe:AUTO {OFF|ON|ONCE}
# [SENSe:]CAPacitance:RANGe:AUTO?
#
#
# ########## CONTINUITY ##########
#
# [SENSe:]CONTinuity:THReshold:VALue {<value>|MIN|MAX|DEF}
#   0 to 2000 ohm
#
# [SENSe:]CONTinuity:VOLume:STATe{<value>|LOW|MIDDLE|HIGH}
# [SENSe:]CONTinuity:VOLume:STATe?
#
#   See SYSTEM:BEEPER
#
#
# ########## DIODE ##########
#
# None of these are listed in the manuals!
#
# [SENSe:]DIODe:THReshold:VALue {<value>|MIN|MAX|DEF}
#   0 to 4 V
#
# [SENSe:]DIODe:VOLume:STATe{<value>|LOW|MIDDLE|HIGH}
# [SENSe:]DIODe:VOLume:STATe?
#
#   See SYSTEM:BEEPER
#
#
# ########## SYSTEM SUBSYSTEM ##########
#
# SYSTem:BEEPer:STATe {ON|1|OFF|0}
# SYSTem:BEEPer:STATe?
#
# TRIGger:SOURce {IMMediate|EXTernal|BUS}
#   We only support IMMedaiate
# TRIGger:SOURce?
#
# UNIT:TEMP {C|F|K}
# UNIT:TEMP?


class FakeInstrumentSiglentSDM3000(InstrumentSiglentSDM3000):
    """Controller for internally-faked SDM3000-series devices."""

    def __init__(self, *args, existing_names=None, manufacturer=None, model=None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        super().init_names('SDM3000', 'SDM', existing_names)
        self._manufacturer = manufacturer
        self._model = model
        self._serial_number = '0123456789'
        self._firmware_version = 'FW1.2.3.4'
        self._hardware_version = 'HW1.2.3.4'
        self._is_fake = True
        self._logger = logging.getLogger(f'ic.fake')

        self._config = {
            ':TRIGGER:SOURCE': 'IMM',
            ':FUNCTION': '"DIOD"',
            ':VOLT:DC:NULL:STATE': '0',
            ':VOLT:DC:NULL:VALUE': '+0.00000000E+00',
            ':VOLT:DC:NULL:VALUE:AUTO': '0',
            ':VOLT:DC:RANGE': '+2.00000000E-01',
            ':VOLT:DC:RANGE:AUTO': '1',
            ':VOLT:DC:NPLC': '+1.00000000E+01',
            ':VOLT:DC:IMP': '10M',
            ':VOLT:AC:NULL:STATE': '0',
            ':VOLT:AC:NULL:VALUE': '+0.00000000E+00',
            ':VOLT:AC:NULL:VALUE:AUTO': '0',
            ':VOLT:AC:RANGE': '+2.00000000E-01',
            ':VOLT:AC:RANGE:AUTO': '1',
            ':CURR:DC:NULL:STATE': '0',
            ':CURR:DC:NULL:VALUE': '+0.00000000E+00',
            ':CURR:DC:NULL:VALUE:AUTO': '0',
            ':CURR:DC:RANGE': '+2.00000000E-04',
            ':CURR:DC:RANGE:AUTO': '1',
            ':CURR:DC:NPLC': '+1.00000000E+01',
            ':CURR:AC:NULL:STATE': '0',
            ':CURR:AC:NULL:VALUE': '+0.00000000E+00',
            ':CURR:AC:NULL:VALUE:AUTO': '0',
            ':CURR:AC:RANGE': '+2.00000000E-02',
            ':CURR:AC:RANGE:AUTO': '1',
            ':RES:NULL:STATE': '0',
            ':RES:NULL:VALUE': '+0.00000000E+00',
            ':RES:NULL:VALUE:AUTO': '0',
            ':RES:RANGE': '+2.00000000E+02',
            ':RES:RANGE:AUTO': '1',
            ':RES:NPLC': '+1.00000000E+01',
            ':FRES:NULL:STATE': '0',
            ':FRES:NULL:VALUE': '+0.00000000E+00',
            ':FRES:NULL:VALUE:AUTO': '0',
            ':FRES:RANGE': '+2.00000000E+02',
            ':FRES:RANGE:AUTO': '1',
            ':FRES:NPLC': '+1.00000000E+01',
            ':CAP:NULL:STATE': '0',
            ':CAP:NULL:VALUE': '+0.00000000E+00',
            ':CAP:NULL:VALUE:AUTO': '0',
            ':CAP:RANGE': '+2.00000000E-07',
            ':CAP:RANGE:AUTO': '1',
            ':CONT:THRESHOLD:VALUE': '+5.00000000E+01',
            ':CONT:VOLUME:STATE': 'MIDDLE',
            ':DIOD:THRESHOLD:VALUE': '+3.00000000E+00',
            ':DIOD:VOLUME:STATE': 'MIDDLE',
            ':FREQ:NULL:STATE': '0',
            ':FREQ:NULL:VALUE': '+0.00000000E+00',
            ':FREQ:NULL:VALUE:AUTO': '0',
            ':FREQ:VOLT:RANGE': '+2.00000000E+00',
            ':FREQ:VOLT:RANGE:AUTO': '1',
            ':PER:NULL:STATE': '0',
            ':PER:NULL:VALUE': '+0.00000000E+00',
            ':PER:NULL:VALUE:AUTO': '0',
            ':PER:VOLT:RANGE': '+2.00000000E+00',
            ':PER:VOLT:RANGE:AUTO': '1',
            ':TEMP:NULL:STATE': '0',
            ':TEMP:NULL:VALUE': '+0.00000000E+00',
            ':TEMP:NULL:VALUE:AUTO': '0',
            ':TEMP:UDEFINE:THER:TRAN:LIST': 'NONE',
            ':TEMP:UDEFINE:RTD:TRAN:LIST': 'NONE',
            ':TEMP:MDEFINE:THER:TRAN:LIST': 'BITS90,EITS90,JITS90,KITS90,NITS90,RITS90,SITS90,TITS90',
            ':TEMP:MDEFINE:RTD:TRAN:LIST': 'PT100,PT1000',
            ':UNIT:TEMP': 'C'
        }

        if model in ('SDM3045X', 'SDM3055'):
            self._config.update({
                ':VOLT:DC:FILTER:STATE': '0',
                ':CURR:DC:FILTER:STATE': '0',
            })
        elif model == 'SDM3065X':
            self._config.update({
                ':VOLT:AC:BANDWIDTH': '20Hz',
                ':VOLT:DC:AZ:STATE': '0',
                ':CURR:AC:BANDWIDTH': '20Hz',
                ':CURR:DC:AZ:STATE': '0',
                ':FREQ:APERTURE': '+1.00000000E-01',
                ':PER:APERTURE': '+1.00000000E-01',
                ':RES:AZ:STATE': '0',
                ':FRES:AZ:STATE': '0',
            })
        else:
            assert False, model

    async def connect(self, *args, **kwargs):
        """Connect to the instrument and set it to remote state."""
        self._connected = True
        self._long_name = f'{self._model} @ {self._resource_name}'

    async def disconnect(self, *args, **kwargs):
        """Disconnect from the instrument and turn off its remote state."""
        self._connected = False

    async def query(self, s):
        """VISA query, write then read."""
        if not self._connected:
            raise NotConnected
        assert s[-1] == '?', s
        s = s[:-1]
        if s == 'READ':
            await asyncio.sleep(0.2)
            ret = str(random.random())
        elif s == ':FUNCTION':
            ret = random.choice((
                'VOLT:DC', 'VOLT:AC', 'CURR:DC', 'CURR:AC',
                'RES', 'FRES', 'CAP', 'CONT', 'DIOD', 'FREQ', 'PER', 'TEMP'
            ))
        elif s.endswith(':NULL:STATE'):
            ret = random.randint(0, 1)
        elif s in ('CURR:AC:NULL:VALUE', 'CURR:DC:NULL:VALUE'):
            ret = random.uniform(-11, 11) # Correct?
        elif s in ('FREQ:AC:NULL:VALUE', 'PER:DC:NULL:VALUE'):
            ret = random.uniform(-1.2e6, 1.2e6) # Correct?
        elif s in ('RES:AC:NULL:VALUE', 'FRES:DC:NULL:VALUE'):
            ret = random.uniform(-110e6, 110e6) # Correct?
        elif s == 'TEMP:NULL:VALUE':
            ret = random.uniform(-1e15, 1e15) # Correct?
        elif s == 'CAP:NULL:VALUE':
            ret = random.uniform(-12e-3, 12e-3) # Correct?
        elif s in ('VOLT:AC:NULL:VALUE', 'VOLT:DC:NULL:VALUE'):
            ret = random.uniform(-1200, 1200) # Correct?
        elif s == 'CONT:THRESHOLD:VALUE':
            ret = random.uniform(0, 2000)
        elif s == 'DIOD:THRESHOLD:VALUE':
            ret = random.uniform(0, 2000)
        elif s.endswith(':IMP'):
            ret = random.choice(('10M', '10G'))
        elif s in (':VOLT:AC:RANGE', ':FREQ:VOLT:RANGE', ':PER:VOLT:RANGE'):
            ret = random.choice(list(_RANGE_VAC_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s == ':VOLT:DC:RANGE':
            ret = random.choice(list(_RANGE_VDC_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s == ':CURR:AC:RANGE':
            ret = random.choice(list(_RANGE_IAC_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s == ':CURR:DC:RANGE':
            ret = random.choice(list(_RANGE_IDC_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s in (':RES:RANGE', ':FRES:RANGE'):
            ret = random.choice(list(_RANGE_R_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s == ':CAP:RANGE':
            ret = random.choice(list(_RANGE_C_SCPI_READ_TO_WRITE[self._model].keys()))
        elif s.endswith(':NPLC'):
            ret = random.choice(('0.3', '1', '10'))
        else:
            ret = self._config[s]
        self._config[s] = ret
        self._logger.debug(f'{self._long_name} - query "{s}" returned "{ret}"')
        return ret

    async def write(self, s):
        """VISA write, appending termination characters."""
        self._logger.debug(f'{self._long_name} - write "{s}"')
        ind = s.find(' ')
        assert ind != -1, s
        param_name = s[:ind]
        val = s[ind+1:]
        self._config[param_name] = val
        # Validate values here
