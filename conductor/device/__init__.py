################################################################################
# conductor/device/__init__.py
#
# This file is part of the inst_conductor software suite.
#
# It contains the top-level interface to the instrument device driver module.
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

from .device import (Device4882,
                     ConnectionLost,    # Needed for pass-thru import; leave here
                     InstrumentClosed,
                     NotConnected)
from .siglent_sdl1000 import InstrumentSiglentSDL1000
from .siglent_sdm3000 import InstrumentSiglentSDM3000
from .siglent_spdx000 import InstrumentSiglentSPDx000


_DEVICE_MAPPING = {}  # Mapping of IDN to Python class
SUPPORTED_INSTRUMENTS = []  # List of model numbers of supported instruments
# We could auto-discover these by looking for files in the current directory to
# allow for future plug-in support, but that messes up the creation of Python
# install images and it's a small list for now anyway.
for cls in (InstrumentSiglentSDL1000,
            InstrumentSiglentSDM3000,
            InstrumentSiglentSPDx000):
    _DEVICE_MAPPING.update(cls.idn_mapping())
    SUPPORTED_INSTRUMENTS += cls.supported_instruments()


class UnknownInstrumentType(Exception):
    pass


async def create_device(resource_name, existing_names=None, **kwargs):
    """"Query a device for its IDN and create the appropriate instrument class."""
    dev = Device4882(resource_name)
    await dev.connect()
    inst_cls = None
    if dev._is_fake:
        model = resource_name.replace('FAKE::', '')
        for (manufacturer, inst_name), cls in _DEVICE_MAPPING.items():
            if inst_name == model:
                inst_cls = _DEVICE_MAPPING.get((manufacturer, model), None)
                inst_cls = inst_cls.get_fake_instrument_class()
                break
    else:
        idn = await dev.idn()
        idn_split = idn.split(',')
        if len(idn_split) >= 2:
            manufacturer, model, *_ = idn_split
            inst_cls = _DEVICE_MAPPING.get((manufacturer, model), None)
    if inst_cls is None:
        raise UnknownInstrumentType(idn)
    new_dev = inst_cls(resource_name, existing_names=existing_names,
                       manufacturer=manufacturer, model=model,
                       **kwargs)
    await new_dev.connect(reader=dev._reader, writer=dev._writer)
    return new_dev
