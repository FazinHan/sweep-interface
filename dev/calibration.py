import time
import configparser

config = configparser.ConfigParser()
config.read('params.ini')

res = config.getfloat('Calibration', 'cal_res')

print(f"--- STARTING CALIBRATION ---")
print(f"Running calibration with resolution: {res}")
time.sleep(2) # Simulate calibration
print("--- CALIBRATION FINISHED ---")