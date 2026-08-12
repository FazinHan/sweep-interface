from EM3000S import MagnetController
# from lab_emulator import MagnetController

magnet = MagnetController()

magnet.connect()
print("Emergency stop: de-energising magnet...")
magnet.stop_and_query_field()
magnet.disconnect()
print("Magnet de-energised.")