from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController
from progress import countdown
import pandas as pd
import numpy as np
import configparser
import time, os

dir = "data"

CONFIG_FILE = 'params.ini'
STABILIZE_TIME = 10  # seconds to let the magnet settle before reading the field

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("Error: params.ini not found!")
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
    
try:
    # Load Experiment tab values
    calibration_resolution = int(config.get('Calibration', 'cal_res', fallback='800'))   
    print("Config loaded successfully.")
except Exception as e:
    raise ValueError("Error reading config file.")

print("Connecting to Magnet Controller...")

magnet = MagnetController()
magnet.connect()

# Sweep the selected magnet's full range, into the file that magnet reads back
# in set_field(). Each magnet keeps its own curve.
curr_arr = np.linspace(-magnet.max_current, magnet.max_current, calibration_resolution)

data = np.zeros((calibration_resolution,2))

print(f"Starting field calibration sweep for {calibration_resolution} points...")

for idx,curr in enumerate(curr_arr):
    print(f"Setting current to {curr:.2f} A")
    magnet.set_current(curr)
    # Wait for the magnet to stabilize
    countdown(STABILIZE_TIME, f"  stabilizing {idx+1}/{calibration_resolution}")
    field = magnet.query_field()
    print(f"Measured field: {field:.2f} mT")
    data[idx,0] = curr
    data[idx,1] = field

# os.rename(magnet.calibration_file, magnet.calibration_file + '.bak') if os.path.exists(magnet.calibration_file) else None

df = pd.DataFrame(data, columns=['Current_A', 'Field_mT'])
df.to_csv(magnet.calibration_file, index=False)

magnet.stop_and_query_field()
magnet.disconnect()

print(f"Field calibrated and data saved to '{magnet.calibration_file}'.")