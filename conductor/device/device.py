################################################################################
# device/device.py
#
# This file is part of the inst_conductor software suite.
#
# It contains the parent class for all devices (the Device class) and the all
# devices that support IEEE-488.2 (the Device4882 class).
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


class NotConnectedError(Exception):
    pass


class Device(object):
    """Class representing any generic device accessible through VISA."""
    def __init__(self, resource_name):
        self._resource_name = resource_name
        self._long_name = resource_name
        self._name = resource_name
        self._resource = None
        self._connected = False
        self._manufacturer = None
        self._model = None
        self._serial_number = None
        self._firmware_version = None
        self._hardware_version = None
        self._debug = False
        self._io_lock = asyncio.Lock()
        self._connection_timeout = 3
        self._is_fake = False

    @property
    def connected(self):
        return self._connected

    @property
    def manufacturer(self):
        return self._manufacturer

    @property
    def model(self):
        return self._model

    @property
    def serial_number(self):
        return self._serial_number

    @property
    def firmware_version(self):
        return self._firmware_version

    @property
    def hardware_version(self):
        return self._hardware_version

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, v):
        self._name = v

    @property
    def long_name(self):
        return self._long_name

    @property
    def resource_name(self):
        return self._resource_name

    def set_debug(self, val):
        self._debug = val

    @classmethod
    def idn_mapping(cls):
        """Map IDN information to an instrument class."""
        raise NotImplementedError

    @classmethod
    def supported_instruments(cls):
        """Return a list of supported instrument models."""
        raise NotImplementedError

    async def connect(self, reader=None, writer=None):
        """Open the connection to the device."""
        if self._connected:
            return
        if reader is not None or writer is not None:
            self._reader, self._writer = reader, writer
            self._connected = True
        elif self._resource_name.startswith('FAKE::'):
            self._reader = self._writer = None
            self._connected = True
            self._is_fake = True
            if self._debug:
                print(f'Connected to fake device {self._resource_name}')
            return
        elif self._resource_name.startswith('TCPIP::'):
            ip_addr = self._resource_name.replace('TCPIP::', '')
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(ip_addr, 5025),
                    timeout=self._connection_timeout)
            except: # Too many possible exceptions to check for
                if self._debug:
                    print(f'Timeout connecting to {self._resource_name}')
                raise NotConnectedError
            self._connected = True
            if self._debug:
                print(f'Connected to {self._resource_name}')
        else:
            print(f'Bad resource name {self._resource_name}')
            return

    def init_names(self, long_pfx, short_pfx, existing_names):
        """Initialize long and short names and ensure uniqueness."""
        self._long_name = f'{long_pfx} @ {self._resource_name}'
        if self._resource_name.startswith('TCPIP'):
            ips = self._resource_name.split('.') # This only works with TCP!
            short_name = f'{short_pfx}{ips[-1]}'
        else:
            short_name = short_pfx
        if existing_names and short_name in existing_names:
            sfx = 1
            while True:
                short_name2 = f'{short_name}[{sfx}]'
                if short_name2 not in existing_names:
                    short_name = short_name2
                    break
                sfx += 1
        self._name = short_name

    ### Direct access to pyvisa functions

    async def disconnect(self):
        """Close the connection to the device."""
        if not self._connected:
            raise NotConnectedError
        if not self._is_fake:
            self._writer.close()
            await self._writer.wait_closed()
        self._connected = False
        if self._debug:
            print(f'Disconnected from {self._resource_name}')

    async def query(self, s, timeout=None):
        """VISA query, write then read."""
        if not self._connected:
            raise NotConnectedError
        if self._is_fake:
            ret = 'QUERY_RESULT'
        else:
            # try:
            async with self._io_lock:
                # Write and Read have to be adjacent to each other
                await self.write_no_lock(s)
                ret = await self.read_no_lock()
            ret = ret.strip(' \t\r\n')
            # except pyvisa.errors.VisaIOError:
            #     self.disconnect()
            #     if self._debug:
            #         print(f'query "{s}" resulted in disconnect')
            #     raise ContactLostError
        if self._debug:
            print(f'query "{s}" returned "{ret}"')
        return ret

    async def read_no_lock(self):
        """VISA read, strips termination characters. No locking."""
        if not self._connected:
            raise NotConnectedError
        if self._is_fake:
            ret = 'READ_RESULT'
        else:
            # try:
            ret = await self._reader.readline()
            ret = ret.decode().strip(' \t\r\n')
            # except pyvisa.errors.VisaIOError:
            #     self.disconnect()
            #     raise ContactLostError
        return ret

    async def read(self):
        """VISA read, strips termination characters."""
        async with self._io_lock:
            ret = await self._read_no_lock()
        if self._debug:
            print(f'read returned "{ret}"')
        return ret

    async def read_raw(self):
        """VISA read_raw."""
        if not self._connected:
            raise NotConnectedError
        if self._is_fake:
            ret = 'READ_RAW_RESULT'
        else:
            # try:
            async with self._io_lock:
                ret = await self._reader.readline()
            ret = ret.decode()
            # except pyvisa.errors.VisaIOError:
            #     self.disconnect()
            #     raise ContactLostError
        if self._debug:
            print(f'read_raw returned "{ret}"')
        return ret

    async def write_no_lock(self, s):
        """VISA write, appending termination characters. No locking."""
        if not self._connected:
            raise NotConnectedError
        if self._is_fake:
            return
        try:
            self._writer.write((s+'\n').encode())
            await self._writer.drain()
        except ConnectionResetError:
                await self.disconnect()
                raise NotConnectedError

    async def write(self, s):
        """VISA write, appending termination characters."""
        if not self._connected:
            raise NotConnectedError
        if self._debug:
            print(f'write "{s}"')
        if self._is_fake:
            return
        try:
            async with self._io_lock:
                self._writer.write((s+'\n').encode())
                await self._writer.drain()
        except ConnectionResetError:
            await self.disconnect()
            raise NotConnectedError

    async def write_raw(self, s):
        """VISA write, no termination characters."""
        if not self._connected:
            raise NotConnectedError
        if self._debug:
            print(f'write_raw "{s}"')
        if self._is_fake:
            return
        try:
            self._writer.write(s.encode())
            await self._writer.drain()
        except ConnectionResetError:
            await self.disconnect()
            raise NotConnectedError

    ### Internal support routines

    async def _read_write(self, query, write, validator=None, value=None):
        if value is None:
            return await self.query(query)
        if validator is not None:
            validator(value)
        await self.write(f'{write} {value}')
        return None

    def _validator_1(self, value):
        if value < 0 or value > 1:
            raise ValueError

    def _validator_8(self, value):
        if not (0 <= value <= 255):  # Should this be 128? Or -128?
            raise ValueError

    def _validator_16(self, value):
        if not (0 <= value <= 65535):
            raise ValueError


class Device4882(Device):
    """Class representing any device that supports IEEE 488.2 commands."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def idn(self):
        """Read instrument identification."""
        return await self.query('*IDN?')

    async def rst(self):
        """Return to the instrument's default state."""
        self.write('*RST')
        await self.cls()

    async def cls(self):
        """Clear all event registers and the error list."""
        await self.write('*CLS')

    async def ese(self, reg_value=None):
        """Read or write the standard event status enable register."""
        return await self._read_write('*ESE?', '*ESE', self._validator_8, reg_value)

    async def esr(self):
        """Read and clear the standard event status enable register."""
        return await self.query('*ESR?')

    async def send_opc(self):
        """"Set bit 0 in ESR when all ops have finished."""
        await self.write('*OPC')

    async def get_opc(self):
        """"Query if current operation finished."""
        return await self.query('*OPC?')

    async def sre(self, reg_value=None):
        """Read or write the status byte enable register."""
        return await self._read_write('*SRE?', '*SRE', self._validator_8, reg_value)

    async def stb(self):
        """Reads the status byte event register."""
        return await self.query('*STB?')

    async def tst(self):
        """Perform self-tests."""
        return await self.query('*TST')

    async def wait(self):
        """Wait until all previous commands are executed."""
        await self.write('*WAI')

    async def trg(self):
        """Send a trigger command."""
        await self.write('*TRG')
