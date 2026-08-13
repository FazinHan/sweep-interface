from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController

magnet = MagnetController()

magnet.connect()
print("Emergency stop: de-energising magnet...")
magnet.stop_and_query_field()
magnet.disconnect()
print("Magnet de-energised.")