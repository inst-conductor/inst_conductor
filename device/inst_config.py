################################################################################
# device/inst_config.py
#
# This file is part of the inst_conductor software suite.
#
# It contains the parent class for all instrument configurations (the
# InstrumentConfiguration) class.
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

from collections import namedtuple

class InstrumentConfiguration(object):
    """Class representing the configuration of a SCPI instrument.

    The default behavior is to assume that each SCPI command can be executed
    in two forms:

    SCPICMD <val> writes the value to the instrument.
    SCPICMD? reads the value from the instrument.

    It's possible that the value read and the value written may not be in the
    same format (I'm looking at you, SDM3000), so we support conversion
    functions.
    """
    ParamDetails = namedtuple('ParamDetails', ['from_inst_func', 'to_inst_func'])

    def __init__(self):
        self._modes = {}
        self._global_params = {}

    def add_mode(self, mode_name):
        """Add a new mode. Each mode can support multiple parameters."""
        self._modes.append(mode_name)

    def add_mode_param(self, mode_name, scpi_cmd, from_inst_func, to_inst_func):
        """Add a SCPI parameter to the supported list for a particular mode.

        from_inst_func is the function to call to convert a string read from the
        instrument to a value to store.
        to_inst_func is the function to call to convert a stored value to a
        string that can be written to the instrument.

        The order of the added parameters will be remembered and used to update
        the instrument.
        """
        if mode_name not in self._modes:
            self._modes[mode_name] = {}
        self._modes[mode_name][scpi_cmd] = self.ParamDetails(
            from_inst_func=from_inst_func, to_inst_func=to_inst_func)

    def add_global_param(self, scpi_cmd, from_inst_func, to_inst_func):
        """Add a SCPI parameter that is not associated with a mode.

        from_inst_func is the function to call to convert a string read from the
        instrument to a value to store.
        to_inst_func is the function to call to convert a stored value to a
        string that can be written to the instrument.

        The order of the added parameters will be remembered and used to update
        the instrument.
        """
        self._global_params[scpi_cmd] = self.ParamDetails(
            from_inst_func=from_inst_func, to_inst_func=to_inst_func)
