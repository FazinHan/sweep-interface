from EM3000S import MagnetController
from VNA import VNAController
# from lab_emulator import MagnetController, VNAController
# from lab_emulator import VNAController
import numpy as np
import configparser
import time, os
import pandas as pd

dir = "data"
CONFIG_FILE = 'params.ini'

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
   
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

if UNIT == 'A':
    if CURRENT_HIGH > 4 or CURRENT_LOW < -4:
        raise ValueError("Current out of range for Magnet Controller (-4A to 4A).")

pathname = os.path.join(dir, f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}")

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

magnet.connect()
vna.connect()

print("Sweeping...")

currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP, STEP)
s_param_magnitudes = {'s11': [], 's12': [], 's21': [], 's22': []}

for curr in currs:
    print(f"Setting field to {curr:.2f} {UNIT}...")
    if UNIT == 'mT':
        curr_return = magnet.set_field(curr)
    else:
        curr_return = magnet.set_current(curr)
    
    ### EXPERIMENT 1
    time.sleep(2)  # Wait for the magnet to stabilize
    freq, s11_1 = vna.read_s11()
    _, s12_1 = vna.read_s12()
    _, s21_1 = vna.read_s21()
    _, s22_1 = vna.read_s22()

    ### EXPERIMENT 2
    time.sleep(2)  # Wait for the magnet to stabilize
    freq, s11_2 = vna.read_s11()
    _, s12_2 = vna.read_s12()
    _, s21_2 = vna.read_s21()
    _, s22_2 = vna.read_s22()

    ### EXPERIMENT 3
    time.sleep(2)  # Wait for the magnet to stabilize
    freq, s11_3 = vna.read_s11()
    _, s12_3 = vna.read_s12()
    _, s21_3 = vna.read_s21()
    _, s22_3 = vna.read_s22()

    ### AVERAGE
    s11 = np.mean(s11_1 + s11_2 + s11_3)
    s12 = np.mean(s12_1 + s12_2 + s12_3)
    s21 = np.mean(s21_1 + s21_2 + s21_3)
    s22 = np.mean(s22_1 + s22_2 + s22_3)


    df = {'Frequency (Hz)': freq,
          'S11 Real Mean': s11.real,
          'S11 Imag Mean': s11.imag,
          'S12 Real Mean': s12.real,
          'S12 Imag Mean': s12.imag,
          'S21 Real Mean': s21.real,
          'S21 Imag Mean': s21.imag,
          'S22 Real Mean': s22.real,
          'S22 Imag Mean': s22.imag,
          'S11 (db) Mean': 20 * np.log10(np.abs(s11)),
          'S12 (db) Mean': 20 * np.log10(np.abs(s12)),
          'S21 (db) Mean': 20 * np.log10(np.abs(s21)),
          'S22 (db) Mean': 20 * np.log10(np.abs(s22)),
          'S11 Phase Mean': np.angle(s11, deg=True),
          'S12 Phase Mean': np.angle(s12, deg=True),
          'S21 Phase Mean': np.angle(s21, deg=True),
          'S22 Phase Mean': np.angle(s22, deg=True)
          }
    df = pd.DataFrame(df)
    df.to_csv(os.path.join(subdir, f"{curr_return:.2f}{UNIT}.csv"), index=False)

print("Stopping magnet...")
magnet.stop_and_query_field()

magnet.disconnect()

print("Data saved.\n")

