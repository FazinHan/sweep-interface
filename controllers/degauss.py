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

    +1.00, -0.75, +0.56, -0.42, +0.32, -0.24, +0.18, -0.13, +0.10 A, then off

Settings come from [Degauss] in params.ini (start, steps, decay, dwell). The
sweep starts mild -- 1 A by default, not the magnet's full range -- and stops
once the amplitude falls below DEGAUSS_CURRENT_FLOOR_A, because the drive is
not trustworthy at very small currents and further steps would add time
without adding effect.

Starting mild is a deliberate trade. Beginning at saturation degausses more
completely, but puts the magnet through its hardest duty every time it is
run. 1 A clears the remanence left by ordinary sweeps; it cannot clear
remanence from having been driven harder than the starting amplitude, so
raise [Degauss]/start after a run that went near the magnet's limit.

Run it before a measurement series, and after anything that has left the
magnet at a large one-sided current. It energises the magnet, so it is
interruptible: the ABORT button terminates it and runs abort_all.py.
"""
from devices import (DEGAUSS_CURRENT_FLOOR_A, degauss_settings,
                     get_magnet_controller)
MagnetController = get_magnet_controller()   # selected in the Configuration tab
# from lab_emulator import MagnetController
from progress import countdown

START, STEPS, DECAY, DWELL = degauss_settings()

magnet = MagnetController()
magnet.connect()

# Never ask for more than the selected magnet can take, whatever the config
# says -- the drivers assert on this anyway, and failing here is tidier than
# failing part-way through the sequence with the core left magnetised.
start = min(float(START), float(magnet.max_current))
if start < START:
    print(f"Requested start {START} A exceeds this magnet's "
          f"{magnet.max_current} A limit; starting at {start} A.")

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
