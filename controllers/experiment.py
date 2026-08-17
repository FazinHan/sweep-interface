from devices import (dvm_channel, dvm_nplc, get_dvm_controller,
                     get_magnet_controller, get_vna_controller,
                     require_field_support, stabilize_time)
from progress import countdown

# Drivers come from the Configuration tab's selection ([Devices] in params.ini).
MagnetController = get_magnet_controller()
VNAController = get_vna_controller()
# Uncomment to run without hardware (overrides the selection above):
# from lab_emulator import MagnetController, VNAController
# from lab_emulator import VNAController

import numpy as np
import configparser
import time, os
import pandas as pd

dir = "data"
CONFIG_FILE = 'params.ini'
STABILIZE_TIME = stabilize_time()  # set in the Configuration tab

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("params.ini not found!")
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
    
try:
    # Load Experiment tab values
    UNIT = config.get('Experiment', 'unit', fallback='A')
    CURRENT_LOW = float(config.get('Experiment', 'low', fallback='0'))
    CURRENT_HIGH = float(config.get('Experiment', 'high', fallback='1'))
    STEP = float(config.get('Experiment', 'step', fallback='0.1'))
    DOWN = config.getboolean('Experiment', 'sweep_down', fallback=False)
   
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

# Fail before creating a run directory or touching the magnet.
require_field_support(UNIT)

pathname = os.path.join(dir, f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}{'_DOWN' if DOWN else ''}")

# s_params = ['s11', 's12', 's21', 's22']
ss = 1
subdir = os.path.join(pathname, str(ss))
while os.path.isdir(subdir):
    ss += 1
    subdir = os.path.join(pathname, str(ss))
os.makedirs(subdir, exist_ok=False)


print("Connecting to VNA and Magnet Controllers...")

vna = VNAController()
magnet = MagnetController()

# Optional third instrument. get_dvm_controller() returns None when the
# Experiment tab's voltmeter box is unticked, and the driver is never
# imported in that case.
DVMController = get_dvm_controller()
dvm = None
if DVMController is not None:
    dvm = DVMController(channel=dvm_channel(), nplc=dvm_nplc())

magnet.connect()
vna.connect()
if dvm is not None:
    dvm.connect()
    # Null once here, before the sweep and with the RF drive off, so standing
    # thermal EMFs are cancelled. Doing this with the drive on would null away
    # the signal itself.
    print("Zeroing nanovoltmeter (RF drive should be OFF for this)...")
    dvm.zero()

print("Sweeping...")

# Stop half a step past `high` rather than a full step. arange excludes its
# stop, so `+ STEP` is meant to make `high` inclusive -- but when floating
# point puts the computed stop a hair above high + step, one extra point slips
# in beyond the requested range (0 -> 0.2 step 0.1 produced a 0.3 point). That
# drives the magnet past what was asked for, and if `high` is near the current
# limit the extra point trips the driver's range assert and loses the run.
# Half a step is unambiguous: it includes `high` and cannot reach high + step.
currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP / 2, STEP)
if DOWN:
    currs = currs[::-1]  # Reverse the array if sweeping down

for curr in currs:
    print(f"Setting field to {curr:.2f} {UNIT}...")
    if UNIT == 'mT':
        curr_return = magnet.set_field(curr)
    else:
        curr_return = magnet.set_current(curr)
    
    # The DC voltage is read inside the same stabilize/read block as the
    # S-parameters, so both channels see identical settling conditions.
    voltages = []

    ### EXPERIMENT 1
    countdown(STABILIZE_TIME, "  stabilizing 1/3")  # Wait for the magnet to stabilize
    freq, s11_1 = vna.read_s11()
    _, s12_1 = vna.read_s12()
    _, s21_1 = vna.read_s21()
    _, s22_1 = vna.read_s22()
    if dvm is not None:
        voltages.append(dvm.read_voltage())

    ### EXPERIMENT 2
    countdown(STABILIZE_TIME, "  stabilizing 2/3")  # Wait for the magnet to stabilize
    freq, s11_2 = vna.read_s11()
    _, s12_2 = vna.read_s12()
    _, s21_2 = vna.read_s21()
    _, s22_2 = vna.read_s22()
    if dvm is not None:
        voltages.append(dvm.read_voltage())

    ### EXPERIMENT 3
    countdown(STABILIZE_TIME, "  stabilizing 3/3")  # Wait for the magnet to stabilize
    freq, s11_3 = vna.read_s11()
    _, s12_3 = vna.read_s12()
    _, s21_3 = vna.read_s21()
    _, s22_3 = vna.read_s22()
    if dvm is not None:
        voltages.append(dvm.read_voltage())

    # Average the repeats the same way the S-parameters are, skipping any
    # read that failed rather than letting one None poison the mean.
    good = [v for v in voltages if v is not None]
    voltage = sum(good) / len(good) if good else None
    if dvm is not None:
        if voltage is None:
            print("  WARNING: no usable voltage reading at this point.")
        else:
            print(f"  DC voltage: {voltage:.9g} V "
                  f"(mean of {len(good)}/{len(voltages)})")

    ### AVERAGE
    s11 = (s11_1 + s11_2 + s11_3)/3
    s12 = (s12_1 + s12_2 + s12_3)/3
    s21 = (s21_1 + s21_2 + s21_3)/3
    s22 = (s22_1 + s22_2 + s22_3)/3


    df = {'Frequency (Hz)': freq,
          'S11 Real': s11.real,
          'S11 Imag': s11.imag,
          'S12 Real': s12.real,
          'S12 Imag': s12.imag,
          'S21 Real': s21.real,
          'S21 Imag': s21.imag,
          'S22 Real': s22.real,
          'S22 Imag': s22.imag,
          'S11 (db)': 20 * np.log10(np.abs(s11)),
          'S12 (db)': 20 * np.log10(np.abs(s12)),
          'S21 (db)': 20 * np.log10(np.abs(s21)),
          'S22 (db)': 20 * np.log10(np.abs(s22)),
          'S11 Phase': np.angle(s11, deg=True),
          'S12 Phase': np.angle(s12, deg=True),
          'S21 Phase': np.angle(s21, deg=True),
          'S22 Phase': np.angle(s22, deg=True)
          }
    df = pd.DataFrame(df)

    # Scalar-per-point values go in a comment-prefixed metadata block above
    # the frequency-indexed table, not as a column repeated down every row --
    # the voltage is one number for this whole field point, and giving it a
    # column would misrepresent it as varying with frequency. Readers skip
    # these lines with comment='#', so a VNA-only file is byte-identical to
    # what this wrote before the voltmeter existed.
    path = os.path.join(subdir, f"{curr_return:.2f}{UNIT}.csv")
    with open(path, 'w', newline='') as handle:
        if voltage is not None:
            handle.write(f"# Field ({UNIT}): {curr_return:.6f}\n")
            handle.write(f"# DC Voltage (V): {voltage:.9e}\n")
        df.to_csv(handle, index=False)

print("Stopping magnet...")
magnet.stop_and_query_field()

magnet.disconnect()
vna.close()
if dvm is not None:
    # Nothing to walk back here: the DVM is read-only and holds no energised
    # state, which is why the abort path ignores it entirely.
    dvm.close()

print("Data saved.\n")

