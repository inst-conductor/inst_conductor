################################################################################
# inst_conductor.py
#
# This file is part of the inst_conductor software suite.
#
# Is contains the top-level entry point for the suite.
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

import __main__
import argparse
import asyncio
import functools
import logging
import os
import sys

import conductor.qasync
from conductor.qasync import QApplication

import conductor.log as log
from conductor.main_window import MainWindow

app = QApplication.instance()

async def main():
    def close_future(future, loop):
        loop.call_later(10, future.cancel)
        future.cancel()

    loop = asyncio.get_event_loop()
    future = asyncio.Future()

    app = QApplication(sys.argv)  # sys.argv is modified to remove Qt options
    if hasattr(app, "aboutToQuit"):
        getattr(app, "aboutToQuit").connect(
            functools.partial(close_future, future, loop))

    main_window = MainWindow(app, arguments.config_file)
    main_window.show()

    if len(sys.argv) > 1:
        for resource in sys.argv[1:]:
            if resource.find('::') == -1:
                resource = 'TCPIP::' + resource
            await main_window._open_resource(resource)

    await future
    return True


if __name__ == "__main__":
    prog_dir = os.path.dirname(__main__.__file__)
    home_dir = os.path.expanduser('~')
    os.makedirs(os.path.join(home_dir, '.inst_conductor'), exist_ok=True)
    default_logfile = os.path.join(prog_dir, 'inst_conductor.log')
    default_config_file = os.path.join(home_dir, '.inst_conductor',
                                       'inst_conductor.ini')
    parser = argparse.ArgumentParser(prog='inst_conductor')
    parser.add_argument('args', nargs=argparse.REMAINDER)
    log.add_arguments(parser, default_logfile, default_config_file)

    arguments = parser.parse_args(sys.argv[1:])
    sys.argv = sys.argv[:1] + arguments.args
    log.setup_logging(arguments.console_log_level,
                      arguments.logfile_log_level,
                      arguments.logfile)
    log.set_console_format(False)
    logger = logging.getLogger('ic')
    logger.info('Starting')

    try:
        conductor.qasync.run(main())
    except asyncio.exceptions.CancelledError:
        sys.exit(0)
