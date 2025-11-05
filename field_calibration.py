from EM3000S import MagnetController
import pandas as pd
import numpy as np
import time, os

calibration_resolution = 100

print("Connecting to Magnet Controller...")

magnet = MagnetController(resource_name='ASRL5::INSTR')

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
df.to_csv('field_calibration_data.csv', index=False)

magnet.stop_and_query_field()
magnet.disconnect()

print("Field calibrated and data saved to 'field_calibration_data.csv'.")