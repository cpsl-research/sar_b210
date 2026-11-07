# 3 - The GUI live, over ROS 2

## Pending hardware runs (b210-15/16/18)

Not written yet. This page will cover starting the ROS 2 node, the live source in the GUI, and recording to HDF5 for [tutorial 2](02_gui_on_files.md). It waits for the hardware runs of directives b210-15, b210-16 and b210-18, so that every step here has actually been run.

Console's own B210 tutorial (setup entry, config select, start/stop, Open B210 GUI, Record and View) is also pending those runs; it will link here for everything standalone.

## Transmit safety (applies to every live page)

The GUI and capture never transmit without the typed confirmation. Gains are capped (`max_tx_gain_db`), and bench loopback needs the 10 dB attenuator in line: never connect TX directly to RX2. Over-the-air transmission needs the RF checks in [`integration_notes.md`](../integration_notes.md) first.
