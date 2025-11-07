from EM3000S import MagnetController
from VNA import VNAController
# from lab_emulator import MagnetController, VNAController
import numpy as np
import time, os

dir = "data"

with open("params.ini", 'r') as f:
    lines = f.readlines()
    params = {}
    for line in lines:
        if line.strip() and not line.startswith("---"):
            key, value = line.strip().split('=')
            params[key] = value.strip().strip('"')

CONFIG_FILE = 'params.ini'

if not os.path.exists(CONFIG_FILE):
    status_var.set("Error: params.ini not found!")
    return
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)
    
try:
    # Load Experiment tab values
    unit = config.get('Experiment', 'unit', fallback='A')
    mag_used = (unit == 'mT')
    exp_low_var.set(config.get('Experiment', 'low', fallback='0'))
    exp_high_var.set(config.get('Experiment', 'high', fallback='1'))
    exp_step_var.set(config.get('Experiment', 'step', fallback='0.1'))
    
    # Load Calibration tab values
    cal_res_var.set(config.get('Calibration', 'cal_res', fallback='800'))
    
    status_var.set("Config loaded successfully.")
except Exception as e:
    status_var.set("Error reading config file.")
    print(f"Error loading config: {e}")


try:
    CURRENT_LOW = float(params["FIELD_LOW"])
    CURRENT_HIGH = float(params["FIELD_HIGH"])
    STEP = float(params.get("STEP", 10))
    mag_used = True
except KeyError as e:
    CURRENT_HIGH = float(params.get("CURRENT_HIGH", 1))
    CURRENT_LOW = float(params.get("CURRENT_LOW", -1))
    mag_used = False
    STEP = float(params.get("STEP", .1))

if mag_used:
    pathname = os.path.join(dir, f"s_params_{CURRENT_LOW}mT_to_{CURRENT_HIGH}mT_step_{STEP}mT")
else:
    pathname = os.path.join(dir, f"s_params_{CURRENT_LOW}A_to_{CURRENT_HIGH}A_step_{STEP}A")

s_params = ['s11', 's12', 's21', 's22']
s_param_dirs = [os.path.join(pathname, s) for s in s_params]
for subdir in s_param_dirs:
    os.makedirs(subdir, exist_ok=True)

print("Connecting to VNA and Magnet Controllers...")

vna = VNAController()
magnet = MagnetController()

vna.connect()

print("Sweeping...")

currs = np.arange(CURRENT_LOW, CURRENT_HIGH + STEP, STEP)
s_param_magnitudes = {'s11': [], 's12': [], 's21': [], 's22': []}

for curr in currs:
    if mag_used:
        print(f"Setting field to {curr:.2f} mT")
        curr = magnet.set_field(curr)    
    else:
        print(f"Setting current to {curr:.2f} A")
        magnet.set_current(curr)    
    time.sleep(2)  # Wait for the magnet to stabilize
    freq, s11 = vna.read_s11()
    _, s12 = vna.read_s12()
    _, s21 = vna.read_s21()
    _, s22 = vna.read_s22()
    np.save(os.path.join(pathname, 'frequency.npy'), freq)
    for s in s_params:
        if mag_used:
            np.save(os.path.join(pathname, s, f"{curr:.2f}mT.npy"), eval(s))
        else:
            np.save(os.path.join(pathname, s, f"{curr:.2f}A.npy"), eval(s))

print("Stopping magnet...")
magnet.stop_and_query_field()

magnet.disconnect()

print("Data saved.\n")

