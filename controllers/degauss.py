"""
Degausses the electromagnet.

An iron-cored electromagnet does not return to zero field when the current
returns to zero: the core keeps a remanent magnetisation that depends on
where the current has been. That is why a field sweep taken upwards does not
retrace the one taken downwards, and why the field at "0 A" is whatever the
last run left behind rather than zero.

The cure is the standard one: drive the core around progressively smaller
hysteresis loops by alternating the current's polarity while decaying its
amplitude, walking the remanence down towards zero.

    +I, -0.75I, +0.56I, -0.42I, ... then off

Settings come from [Degauss] in params.ini (steps, decay, dwell); the sweep
starts at the selected magnet's own current limit and stops once the
amplitude falls below DEGAUSS_CURRENT_FLOOR_A, because the drive is not
trustworthy at very small currents and further steps would add time without
adding effect.

Run it before a measurement series, and after anything that has left the
magnet at a large one-sided current. It energises the magnet, so it is
interruptible: the ABORT button terminates it and runs abort_all.py.
"""
from devices import (DEGAUSS_CURRENT_FLOOR_A, degauss_settings,
                     get_magnet_controller)
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController
from progress import countdown

STEPS, DECAY, DWELL = degauss_settings()

magnet = MagnetController()
magnet.connect()

start = float(magnet.max_current)
print(f"Degaussing: up to {STEPS} alternations from {start:.2f} A, "
      f"decaying x{DECAY}, {DWELL}s dwell, floor {DEGAUSS_CURRENT_FLOOR_A} A.")

try:
    amplitude = start
    sign = 1.0
    applied = 0

    for step in range(STEPS):
        if amplitude < DEGAUSS_CURRENT_FLOOR_A:
            print(f"  amplitude {amplitude:.3f} A is below the "
                  f"{DEGAUSS_CURRENT_FLOOR_A} A floor; stopping here.")
            break

        current = sign * amplitude
        print(f"  step {step + 1}/{STEPS}: {current:+.3f} A")
        magnet.set_current(current)
        countdown(DWELL, f"  settling {step + 1}/{STEPS}")

        applied += 1
        amplitude *= DECAY
        sign = -sign

    print(f"Applied {applied} alternation(s).")
finally:
    # Always de-energise, including on an error part-way through: leaving the
    # magnet sitting at a large one-sided current is exactly the state this
    # routine exists to undo.
    print("De-energising...")
    magnet.stop_and_query_field()
    magnet.disconnect()

print("Degauss complete. The core should now be close to zero remanence.\n")
