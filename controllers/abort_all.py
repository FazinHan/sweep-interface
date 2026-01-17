# from EM3000S import MagnetController
from lab_emulator import MagnetController

magnet = MagnetController()
vna = VNAController()

magnet.connect()
magnet.stop_and_query_field()
magnet.disconnect()