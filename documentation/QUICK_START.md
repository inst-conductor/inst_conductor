# Quick-Start Guide for Instrument Conductor (inst_conductor)

* Version 0.01beta 2023-01-07
* Source available from https://github.com/inst-conductor/inst_conductor.git
* Please join the mailing list at https://groups.io/g/instrument-conductor-users


## The Main Window

### Opening an Instrument

* Instrument control windows may be opened using "Device / Open with IP address...". At this time only TCP/IP connections are supported. The most recent 10 devices opened (along with their model types) are available in this menu for quick access. A given instrument may only be opened once at a time. If you try to connect to an instrument with multiple copies of Instrument Conductor or other software, the results will likely be unpleasant.
  * Instruments may also be opened when the application is first started by specifying them on the command line, like:

        C:\Downloads> inst_conductor.exe 192.168.0.62 192.168.0.63


* The "Help / About..." menu option shows the supported instruments and details on the currently opened instruments.


### Acquisition and Recording

* Each instrument window retrieves data at a rate governed by the responsiveness of the instrument. Although each instrument responds at its own rate, recording and plotting require uniform sampling. The acquisition window does this by simultaneously sampling each instruments' most recent values at a specified interval. The interval defaults to 1.00 second but can be reduced as low as 0.01 seconds. Note that most instruments will not be able to keep up with that pace of sampling (resulting in duplicate measurements), and that performance will suffer as the rate of sampling increases.
* The start time of the current acquisition sequence, the span of data available, and the number of data points are shown.
* Acquisition can be manually paused and restarted by pressing the "Pause" and "Record" buttons. While acquisition is paused, no measurements will be recorded and there will be a time gap in the data.
* Acquisition may also be automatically paused and restarted by specifying a trigger. The status of the acquisition is shown in the lower right. The trigger may be:
  * **Always** - No trigger is used.
  * **Instrument State** - Recording happens when a binary instrument state is true. Examples include an SDL's list mode is running or an SPD's output is turned on.
  * **Measurement Value** - Recording happens when a specified measurement value has the given relation to a constant. Examples include when an SDM's current increases beyond a certain value, or an SDL's power is less than a given amount.
* All recorded data may be erased by pressing "Erase All Data".
* The currently stored data may be exported to a CSV file by pressing "Save CSV". All measurements that have data will be included.
* A convenient sequence to get only the data you want is:
  1. Set the acquisition trigger appropriately.
  2. Erase All Data.
  3. Start the test or put the instrument in the mode that will trigger the acquisition.
  4. When finish, either manually pause the acquisition or remove the instrument from the triggering mode.
  5. Save the resulting measurements in a CSV file.


## The XY Plot Window

* You can open one or more X/Y plot windows by selecting "Plot / New X/Y Plot" from the main window. Each plotting window operates independently.
* The X axis can display a version of time (Elapsed Time, Absolute Time, or Sample Number), in which case the plot shows measurements vs. time. The X axis can also display an instrument measurement, in which case the plot becomes a scatter plot. The X axis color can be changed, and in either mode the data can be limited to a particular time range.
* Up to eight values may be displayed on the Y axis at the same time. Four of them will show their axes on the left of the plot, and four on the right. Select the values to display using the dropdown boxes. Press "Show All" to populate the eight plots with the first eight measurements that have data available. Press "Show None" to remove all plots.
* The color, line width, and line style of each plot can be selected when Time is used for the X axis. The color, marker size, and marker shape of each plot can be selected for scatter plots.
* When multiple measurements use the same units (e.g. Voltage), they have their own Y axes and are auto-scaled as appropriate. However, it is sometimes desireable to have multiple plots share the same Y scaling for ease of comparison. To accomplish this check the "Share Y Axes" box. All measurements with the same units will be combined together and only one axis will be shown for each. The color of the axis will be that of the first plot in the series with that unit; other plots will maintain their selected colors, but that color will not match up with the axis color, which can be confusing. It is recommended to set all plots with the same units to the same color and use other features, such as line width or line style, to distinguish among them.
* You may use the mouse to left-click-and-drag on a Y axis to move it up or down, or the mouse wheel to zoom in or out. To reset the axis to its default behavior, right-click on the axis and select "Y axis" and "Auto".
* To simplify the display, you can use the View menu to hide various rows of parameters.
* On occasion the Y axes may become confused. Slightly resizing the window will generally fix things.


## The Histogram Window

* You can open one or more Histogram windows by selecting "Plot / New Histogram" from the main window. Each histogram window operates indepently.
* You may display a histogram for any instrument measurement that has data available. The data can be limited to a recent time window, the number of bins for the histogram can be selected up to 999, and the colors of the background, bar edge, and bar fill can be changed.
* Selecting "Percentage" will change the Y axis from absolute counts to relative counts.
* Along with the histogram, statistics are shown, including the value of the most recent reading and the minimum, maximum, mean, median, and standard deviation of the most recent data in the specified time window.
* To simplify the display, you can use the View menu to hide various rows of parameters.


## Instrument Windows

* Each instrument window controls a single physical instrument. The window combines parameter setting (similar to the front panel) and measurement display. As much as possible the instrument control is designed to be intuitive to someone who already understands how the instrument works.
* The "Configuration" menu allows the current state of the instrument to be stored to or recalled from a file. This includes any parameters supported by Instrument Controller that are not actually implemented by the instrument (such as SPD presets). You may also refresh the program with the current state of the instrument (not usually needed) and, in some cases, reset the instrument to its default configuration.
* The "Device" menu allows you to rename the device. This affects the window's title bar and also how the instrument's measurements are presented in the Acquisition and Plotting windows.
* Each instrument window has multiple rows of controls or measurements, and each may be shown or hidden independently to simplify the display. This is done using the "View" menu or the keyboard shortcuts Ctrl+<number>. The numbers increase from the top to the bottom of the window's rows.
* The "Help" menu will give you information about the current instrument as well as any non-obvious keyboard shortcuts.
* When entering numbers directly (such as an SPD voltage or current), the following shortcuts are available:
  * The up and down arrows will change the "1" position. This can also be done using the mouse wheel.
  * Adding only the Shift key will change the "0.1" position.
  * Adding only the Ctrl key will change the "0.01" position.
  * Adding only the Alt key will change the "0.001" position.
* Other menus and options are available depending on the particular instrument.
* If you want to start all instruments with only measurements displayed (and no control parameters), you can specify this on the command line. Combining this with a list of instruments to open will allow quick startup of windows that simply monitor the current measurements programmed into each instrument. You can always use the "View" menu to show the parameters that have been hidden.

        C:\Downloads> inst_conductor.exe --measurements-only 192.168.0.62 192.168.0.63

* The following sections describe unique features of each instrument window that go beyond what is available in the actual instrument.

### SDL1000 Series

* When performing a battery test (or continuing a previous battery test), information is collected and a report is presented when the test is stopped. This report includes the type of SDL including its S/N and firmware version, the start and end time of the test and the total elapsed time, the test mode, why the test stopped, the initial voltage, and the total battery capacity measured. When multiple tests are run in series, the report includes information about the overall test as well as each test segment individually. The report can be saved or printed, and is small enough that it could be taped to the battery being tested. The current report can be displayed again using the "Device / Show last battery report..." menu option, and the saved data can be deleted (to start a fresh test) by pressing the "Reset Addl Cap & Test Log" button.
* The "List" mode is shown in both tabular and graph form. When running, the current position in the list is estimated using wall-clock time because the instrument does not provide remote access to this information.
* You can enable or disable the individual reading of voltage, current, power, and resistance. This does not change what the actual device is doing, only what is being read. When fewer values are read, the remaining ones will be read more quickly.

### SDM3000 Series

* You may specify up to four unique sets of parameters (each with a mode, range, acquisition speed, etc.). The first set is always enabled, while the others may be enabled or disabled individually. When multiple parameter sets are enabled, the instrument is sequentially programmed for each set and a measurement is taken, in a round-robin fashion. This is similar to, but more flexible than, the "Dual" mode available natively in the instrument. Measurements are recorded based on mode, so that it is meaningless to, for example, have multiple parameter sets that are recording "DC Voltage" with different ranges; the measurements will simply overwrite each other. Please be careful not to do something potentially damaging to the instrument like try to measure "DC Voltage" and "Resistance" at the same time.
* The full precision of the 3065X (and then some) is displayed regardless of the instrument type. Whether the excess digits are useful (and accurate) on a 3045X or 3055 is up to you.

### SPD3303 Series

* The instrument window supports minimum and maximum values for voltage and current. This is not a feature present in the instrument, and is designed to help prevent accidental damage to the device being powered. These values are stored when you save the configuration to a file.
* The instrument window supports six presets for voltage and current for each channel. This is not a feature present in the instrument. You can activate a present by clicking on it, or store the current voltage and current in a preset by clicking-and-holding on a present for more than one second.
* The SPD is unique among supported Siglent instruments in that you can use the device's front panel while it is under remote control. If you intend to modify the voltage and current settings on the device, you should ensure that "Refresh display from inst" is checked on the appropriate channels to allow the program to keep up with your changes. If you do not intend to do this, you can uncheck these options, which will allow measurements to be made more rapidly.
* Modifying the Timer mode parameter table on the device will not update the values in the program even if "Refresh display from inst" is checked. If you wish to change the parameters on the device, you can select "Configuration / Refresh from instrument" to get the program back in sync.
* You may select whether modifying the voltage or current setpoints immediately updates the instrument (which is what happens when using the front panel), or whether you have to press "Enter" or otherwise click on another input for changes to take effect. The latter is safer if you want to make sure that you don't accidentally set a voltage or current that will damage your powered device (of course you can also use the minimum/maximum limits for this purpose), or simply want to have more control over when the change takes place.
* The "Timer" mode is shown in both tabular and graph form. When running, the current position of the timer is estimated using wall-clock time because the instrument does not provide remote access to this information.
