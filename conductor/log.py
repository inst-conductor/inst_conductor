################################################################################
# conductor/log.py
#
# This file is part of the inst_conductor software suite.
#
# It contains utility functions related to logging.
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


import logging


LOGGING_LEVEL_CHOICES = ['debug', 'info', 'warning', 'error', 'critical', 'none']
_LOG_DEFAULT_LEVEL = logging.INFO

LOGGING_SUPERCRITICAL = 60

# Functions to set the per-module overrides

def set_inst_level(name, level=_LOG_DEFAULT_LEVEL):
    logger = logging.getLogger(f'ic.device.{name}')
    logger.propagate = True
    logger.setLevel(level)


# Set up the console handler

_LOG_CONSOLE_HANDLER = None

def add_console_handler(level=logging.DEBUG):
    global _LOG_CONSOLE_HANDLER
    _LOG_CONSOLE_HANDLER = logging.StreamHandler()
    _LOG_CONSOLE_HANDLER.setLevel(level)
    root_logger = logging.getLogger('ic')
    root_logger.addHandler(_LOG_CONSOLE_HANDLER)
    set_console_format()

def set_console_format(full=True):
    if full:
        formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(name)s - '
                                      '%(levelname)s - %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
    else:
        formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
    _LOG_CONSOLE_HANDLER.setFormatter(formatter)


# Set up file handler

_LOG_FILE_HANDLER = None

def add_file_handler(filename, level=logging.DEBUG):
    global _LOG_FILE_HANDLER
    _LOG_FILE_HANDLER = logging.FileHandler(filename)
    _LOG_FILE_HANDLER.setLevel(level)
    formatter = logging.Formatter('%(asctime)s.%(msecs)03d - %(name)s - '
                                  '%(levelname)s - %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')
    _LOG_FILE_HANDLER.setFormatter(formatter)
    root_logger = logging.getLogger('ic')
    root_logger.addHandler(_LOG_FILE_HANDLER)


def decode_level(s):
    if s.upper() == 'NONE':
        return LOGGING_SUPERCRITICAL
    return getattr(logging, s.upper())

def min_level(level1, level2):
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR,
                  logging.CRITICAL, LOGGING_SUPERCRITICAL):
        if level1 == level or level2 == level:
            return level
    return LOGGING_SUPERCRITICAL


def setup_logging(console_level, logfile_level, logfile):
    # Set up main loop logging
    logfile_level = decode_level(logfile_level)
    console_level = decode_level(console_level)

    logger = logging.getLogger('ic')
    logger.setLevel(min_level(logfile_level, console_level))

    # Always create a console logger so we don't get a 'no handler' error
    add_console_handler(level=console_level)

    if logfile_level != LOGGING_SUPERCRITICAL:
        add_file_handler(logfile, level=logfile_level)



def add_arguments(parser, default_logfile, default_config_file):
    """Add all logging command line arguments."""
    parser.add_argument(
        '--logfile', metavar='FILENAME', default=default_logfile,
        help=f'The full path of the logfile to write; defaults to {default_logfile}')
    parser.add_argument(
        '--logfile-log-level', metavar='LEVEL', default='debug',
        choices=LOGGING_LEVEL_CHOICES,
        help='Choose the logging level to be output to the logfile')
    parser.add_argument(
        '--console-log-level', metavar='LEVEL', default='error',
        choices=LOGGING_LEVEL_CHOICES,
        help='Choose the logging level to be output to stdout')
    parser.add_argument(
        '--config-file', metavar='FILENAME', default=default_config_file,
        help=f'Program settings file; defaults to {default_config_file}')
