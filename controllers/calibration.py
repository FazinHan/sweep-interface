from EM3000S import MagnetController
# from lab_emulator import MagnetController
import pandas as pd
import numpy as np
import configparser
import time, os

dir = "data"

CONFIG_FILE = 'params.ini'

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

curr_arr = np.linspace(-4,4,calibration_resolution)

data = np.zeros((calibration_resolution,2))

print(f"Starting field calibration sweep for {calibration_resolution} points...")

for idx,curr in enumerate(curr_arr):
    print(f"Setting current to {curr:.2f} A")
    magnet.set_current(curr)
    time.sleep(2)  # Wait for the magnet to stabilize
    field = magnet.query_field()
    print(f"Measured field: {field:.2f} mT")
    data[idx,0] = curr
    data[idx,1] = field

df = pd.DataFrame(data, columns=['Current_A', 'Field_mT'])
df.to_csv(os.path.join('..', 'field_calibration_data.csv'), index=False)

magnet.stop_and_query_field()
magnet.disconnect()

print("Field calibrated and data saved to 'field_calibration_data.csv'.")