# from EM3000S import MagnetController
# from VNA import VNAController
from lab_emulator import MagnetController, VNAController
import numpy as np
import configparser
import time, os

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

pathname = os.path.join(dir, f"s_params_{CURRENT_LOW}{UNIT}_to_{CURRENT_HIGH}{UNIT}_step_{STEP}{UNIT}")

s_params = ['s11', 's12', 's21', 's22']
s_param_dirs = [os.path.join(pathname, s) for s in s_params]
for subdir in s_param_dirs:
    os.makedirs(subdir, exist_ok=True)

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
    np.save(os.path.join(pathname, 'frequency.npy'), freq)
    for s in s_params:
        np.save(os.path.join(pathname, s, f"{curr_return:.2f}{UNIT}.npy"), eval(s))

print("Stopping magnet...")
magnet.stop_and_query_field()

magnet.disconnect()

print("Data saved.\n")

