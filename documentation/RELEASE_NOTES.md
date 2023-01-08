# Release Notes for Instrument Conductor (inst_conductor)

Source available from https://github.com/inst-conductor/inst_conductor.git

## Version 0.01beta 2023-01-07

### Supported Instruments

* Siglent SDL1000X series (SDL1020X, SDL1020X-E, SDL1030X, SDL1030X-E)
  * "Program Mode" is not implemented

* Siglent SDM3000 series (SDM3045X, SDM3055, SDM3065X)
  * Temperature, diode, and continuity modes not implemented
  * Relative measurements are not implemented
  * Manually returning to "Local" mode on the instrument panel will cause the GUI to fail
    and it must be restarted from scratch.

* Siglent SPD3303 series (SPD3303X, SPD3303X-E)
  * Selecting a voltage or current directly on the instrument will override the minimum
    or maximum limits set in the application.

### Known Bugs

* In a numeric input box, although the Shift, Ctrl, and Alt keyboard modifiers work with
  the arrow keys, only Shift and Ctrl work with the mouse wheel.

* Sometimes Siglent instruments become confused when connected and disconnected from
  remote control. If this happens, you will need to power cycle the instrument.
