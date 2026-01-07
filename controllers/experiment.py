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
while True:
    try:
        subdir = os.path.join(pathname, str(ss))
        os.makedirs(subdir, exist_ok=False)
        break
    except FileExistsError:
        ss += 1


print("Connecting to VNA and Magnet Controllers...")

vna = VNAController()
magnet = MagnetController()

magnet.connect()
vna.connect()

print("Sweeping...")

currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP, STEP)
s_param_magnitudes = {'s11': [], 's12': [], 's21': [], 's22': []}

for curr in currs:
    print(f"Setting field to {curr:.2f} mT")
    curr_return = magnet.set_field(curr)
    time.sleep(2)  # Wait for the magnet to stabilize
    freq, s11 = vna.read_s11()
    _, s12 = vna.read_s12()
    _, s21 = vna.read_s21()
    _, s22 = vna.read_s22()
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
    df.to_csv(os.path.join(subdir, f"{curr_return:.2f}{UNIT}.csv"), index=False)

print("Stopping magnet...")
magnet.stop_and_query_field()

magnet.disconnect()

print("Data saved.\n")

