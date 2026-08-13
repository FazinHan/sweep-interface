from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController
import configparser
import os

CONFIG_FILE = 'params.ini'

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError("params.ini not found!")
    
config = configparser.ConfigParser()
config.read(CONFIG_FILE)

try:
    val = float(config.get('Magnet', 'value'))
    unit = config.get('Magnet', 'unit')
except Exception as e:
    raise ValueError(f"Error reading config: {e}")

magnet = MagnetController()
magnet.connect()

if unit == 'mT':
    print(f"Setting field to {val} mT...")
    magnet.set_field(val)
else:
    print(f"Setting current to {val} A...")
    magnet.set_current(val)

magnet.disconnect()
print("Magnet configuration complete.")