from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController

magnet = MagnetController()
magnet.connect()

print("Probing field...")
field = magnet.query_field()

if field is not None:
    print(f"PROBE_RESULT: {field:.2f}")
else:
    print("PROBE_RESULT: Error")

magnet.disconnect()