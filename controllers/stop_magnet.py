from devices import get_magnet_controller
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController

magnet = MagnetController()
magnet.connect()

print("Stopping magnet...")
field = magnet.stop_and_query_field()

if isinstance(field, float):
    print(f"PROBE_RESULT: {field:.2f}")
else:
    print("PROBE_RESULT: Stopped")

magnet.disconnect()
print("Magnet stopped successfully.")