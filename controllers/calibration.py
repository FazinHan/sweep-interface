"""
Sweeps the selected magnet across its current range, recording the internal
gaussmeter at each step, to build the mT <-> A curve that set_field() reads
back.

The sweep is long (a point takes the stabilisation time, so 800 points is
hours) and it drives real hardware, so it is written to never lose what it has
already measured:

* a reading that does not come back is recorded as blank and the sweep carries
  on, instead of killing the run on a format string,
* every point is appended to a .partial file as it is measured, so a crash or
  an ABORT still leaves everything collected up to that moment on disk,
* the existing curve is only replaced once a usable sweep has finished, and the
  file it replaces is kept under a timestamped name.

That last point matters: the previous version wrote the curve in one go at the
very end, so a failure at point 799 of 800 saved nothing, and when it did save
it overwrote the only good calibration in place.
"""
from devices import get_magnet_controller, stabilize_time
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController
from progress import countdown
from field_calibration import MIN_POINTS, FIELD_COLUMN, CURRENT_COLUMN
import numpy as np
import configparser
import csv
import datetime
import os

CONFIG_FILE = 'params.ini'
STABILIZE_TIME = stabilize_time()  # settle time before reading, Configuration tab

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("Error: params.ini not found!")

config = configparser.ConfigParser()
config.read(CONFIG_FILE)

try:
    calibration_resolution = int(config.get('Calibration', 'cal_res', fallback='800'))
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

if calibration_resolution < MIN_POINTS:
    raise ValueError(
        f"Calibration resolution is {calibration_resolution}; the cubic fit in "
        f"set_field() needs at least {MIN_POINTS} points. Raise it in the "
        f"Calibration tab."
    )


def as_field(value):
    """
    query_field() returns a float, or the string 'Query Failed' / None when the
    device did not answer. Turn anything unusable into NaN so one bad read
    costs one point instead of the whole sweep.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float('nan')
    return float(value)


def timestamped_backup(path):
    """Moves `path` aside under a name that cannot collide with an older backup."""
    stamp = datetime.datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
    backup = f"{path}.{stamp}.bak"
    os.replace(path, backup)
    return backup


print("Connecting to Magnet Controller...")

magnet = MagnetController()
magnet.connect()

# Sweep the selected magnet's full range, into the file that magnet reads back
# in set_field(). Each magnet keeps its own curve.
curr_arr = np.linspace(-magnet.max_current, magnet.max_current, calibration_resolution)

final_path = magnet.calibration_file
partial_path = f"{final_path}.partial"

print(f"Starting field calibration sweep for {calibration_resolution} points...")
print(f"Writing points to '{partial_path}' as they are measured.")

measured = 0
failed = 0

# newline='' is what csv wants on Windows; without it every row gets a blank
# line between it and the next.
with open(partial_path, 'w', newline='') as handle:
    writer = csv.writer(handle)
    writer.writerow([CURRENT_COLUMN, FIELD_COLUMN])
    handle.flush()

    for idx, curr in enumerate(curr_arr):
        print(f"Setting current to {curr:.2f} A")
        magnet.set_current(curr)
        # Wait for the magnet to stabilize
        countdown(STABILIZE_TIME, f"  stabilizing {idx+1}/{calibration_resolution}")

        field = as_field(magnet.query_field())
        if np.isnan(field):
            failed += 1
            print(f"  WARNING: no field reading at {curr:.2f} A; "
                  f"recording it blank and continuing.")
            writer.writerow([f"{curr:.6f}", ""])
        else:
            measured += 1
            print(f"Measured field: {field:.2f} mT")
            writer.writerow([f"{curr:.6f}", f"{field:.6f}"])

        # Flush every point: an ABORT terminates this process outright (on
        # Windows there is no chance to run cleanup), so anything still sitting
        # in the buffer would be lost.
        handle.flush()

magnet.stop_and_query_field()
magnet.disconnect()

if failed:
    print(f"\n{failed} of {calibration_resolution} points had no field reading.")

if measured < MIN_POINTS:
    print(f"\nOnly {measured} usable point(s) - a cubic fit needs {MIN_POINTS}.")
    print(f"Keeping the existing calibration; this sweep is left at "
          f"'{partial_path}' for inspection.")
    raise SystemExit(1)

if os.path.exists(final_path):
    backup = timestamped_backup(final_path)
    print(f"Previous calibration kept as '{backup}'.")

os.replace(partial_path, final_path)

print(f"Field calibrated and data saved to '{final_path}' "
      f"({measured} usable point(s)).")
