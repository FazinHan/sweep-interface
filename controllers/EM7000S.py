import pyvisa
import time, os
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONTROL SIGNALS  --  captured from the vendor application, 2026-08-14
# =============================================================================
# The EM7000S command set was recovered the same way as the EM3000S's: USBPcap
# captures of the vendor's Windows software driving the magnet over its FTDI
# USB-serial link (VID 0403, PID 6015). Two captures:
#
#   #1: one coil;  set +1.00, +2.00, +0.50, -1.00 A
#   #2: coil dialog exercised 1-4, then at four coils set +3.00 A / STOP,
#       +0.25 A / STOP, -3.00 A / STOP, then at one coil +1.00 A / STOP
#
# THE PROTOCOL (differs from the EM3000S in almost every constant):
#
#   * Reply rule: a command byte is answered with exactly twice its value
#     (0x64 -> 0xC8, 0x1E -> 0x3C, ...). A data byte is echoed back unchanged.
#     The fixed 0x12 ACK of the EM3000S does not exist here. Two exceptions:
#     COMMIT answers 0x19, and STOP is echoed (0x27 -> 0x27) rather than
#     doubled.
#
#   * The magnet has four independently driven coils. There is NO standalone
#     "coil count" message -- changing it in the vendor app's dialog produces
#     no traffic at all. The count is expressed by shape: each set sequence
#     enables and writes one channel per energised coil.
#
#   * A set sequence, for N coils energised at the same current:
#         READY
#         ENABLE_COIL_BASE + n          for n in 0..N-1
#         CHANNEL_BASE + n, <5 bytes>   for n in 0..N-1
#         COMMIT                        (-> 0x19)
#     Observed verbatim for N=1 and N=4; N=2 and N=3 are the obvious
#     interpolation but were NOT captured (a warning is printed when used).
#
#   * Payload per channel: [value high, value low, 0x00, sign, 0x00],
#     sign 0x01 positive / 0x00 negative. +1.00 A and -1.00 A produced the
#     identical magnitude with only the sign byte differing.
#
#   * STOP is the single byte 0x27, echoed back. Pressed four times across the
#     captures, from +3 A, +0.25 A, -3 A and +1 A states: always just 0x27.
#     (0x2B, the EM3000S stop, is this magnet's coil-4 value channel. The two
#     drivers must never share these constants.)
#
#   * While energised, the vendor app polls 0x2D / 0x2E / 0x38 in a loop and
#     gets short data bursts back -- live readouts of some kind. Their meaning
#     is not established and nothing here needs them.
#
#   * The vendor app rejects settings outside +/-4.2 A in a dialog, so the
#     limit never reaches the wire; 4.2 A is taken from that dialog.
#
# FIELD READING: none. The 0x0A "query" of the EM3000S is this magnet's COMMIT
# byte, no field-query opcode has been observed, and this unit's gaussmeter is
# broken anyway. Everything field-related returns None or raises; the magnet
# is Amps-only (devices.MAGNETS marks it supports_field=False).
#
# CURRENT ENCODE: the five captured (amps -> counts) pairs are used directly,
# with piecewise-linear interpolation between them and edge-slope extrapolation
# beyond -- so a captured current reproduces the vendor app's bytes exactly,
# and nothing is smoothed away by a fitted curve (a cubic fit missed the
# captured points by up to 15 counts). Caveats: 0.25 and 3.00 A were captured
# with four coils energised, the rest with one, so a small coil-count
# dependence cannot be ruled out (+1.00 A at one coil gave identical bytes in
# both captures); above 3.00 A the mapping is extrapolation. ~2735 counts per
# amp, against the EM3000S's ~250 -- one more reason never to mix the two
# drivers' constants.
# =============================================================================

#: The control block above was captured from a real EM7000S; sequences for
#: 2 and 3 coils are inferred (see warning in _run_start_sequence).
SIGNALS_VERIFIED = True

# --- Link settings (confirmed by FTDI control transfers in both captures) ----
BAUD_RATE = 19200
DATA_BITS = 8
PARITY = pyvisa.constants.Parity.none
STOP_BITS = pyvisa.constants.StopBits.one
TIMEOUT_MS = 2000

# --- Opcodes -----------------------------------------------------------------
CMD_READY = 0x64             # -> 0xC8
ENABLE_COIL_BASE = 0x1E      # +n for coil n: 0x1E..0x21, each -> doubled
CHANNEL_BASE = 0x28          # +n for coil n: 0x28..0x2B, each -> doubled
CMD_COMMIT = 0x0A            # -> 0x19 (exception to the doubling rule)
COMMIT_REPLY = 0x19
CMD_STOP = 0x27              # -> echoed 0x27 (exception: not doubled)

# --- Value payload -----------------------------------------------------------
# [high, low, VALUE_PAD, sign, VALUE_TAIL], each byte echoed by the device.
VALUE_PAD = 0x00
VALUE_TAIL = 0x00
SIGN_POSITIVE = 1
SIGN_NEGATIVE = 0

# --- Current encode ----------------------------------------------------------
# Every (amps -> counts) pair ever captured from the vendor app, ascending.
# _counts_for_amps interpolates between them; see header for caveats.
CURRENT_COUNTS_TABLE = (
    (0.25, 555),     # capture #2, four coils
    (0.50, 1198),    # capture #1, one coil
    (1.00, 2570),    # captures #1 and #2, one coil, identical both times
    (2.00, 5301),    # capture #1, one coil
    (3.00, 8061),    # capture #2, four coils
)

# --- Ranges ------------------------------------------------------------------
MAX_CURRENT_A = 4.2          # the vendor app's own dialog: "between -4.2 and
                             # 4.2A". Note 3.0 A is the largest captured value,
                             # so the fit extrapolates above it.
CALIBRATION_FILE = 'field_calibration_data_em7000s.csv'  # unused until the
                             # gaussmeter works and a calibration exists

# =============================================================================


def _counts_for_amps(amps):
    """
    Device counts for a current magnitude, from CURRENT_COUNTS_TABLE.

    Piecewise-linear through the captured points (so a captured current
    reproduces the vendor app's bytes exactly); beyond either end, the edge
    segment's slope is extended, clamped at zero counts.
    """
    knots_a = np.array([p[0] for p in CURRENT_COUNTS_TABLE])
    knots_c = np.array([p[1] for p in CURRENT_COUNTS_TABLE])
    if amps <= knots_a[0]:
        slope = (knots_c[1] - knots_c[0]) / (knots_a[1] - knots_a[0])
        counts = knots_c[0] + slope * (amps - knots_a[0])
    elif amps >= knots_a[-1]:
        slope = (knots_c[-1] - knots_c[-2]) / (knots_a[-1] - knots_a[-2])
        counts = knots_c[-1] + slope * (amps - knots_a[-1])
    else:
        counts = np.interp(amps, knots_a, knots_c)
    return max(int(round(counts)), 0)


def expected_reply(command):
    """
    What the device answers a command byte with: twice the byte, except the
    two documented exceptions (COMMIT -> 0x19, STOP -> echoed).
    """
    if command == CMD_COMMIT:
        return COMMIT_REPLY
    if command == CMD_STOP:
        return CMD_STOP
    return (command * 2) & 0xFF


class MagnetController:
    """
    A PyVISA-based controller for the Holmarc HO-EM7000S electromagnet.

    Same public API as EM3000S.MagnetController -- connect/disconnect,
    set_current/set_field, query_field/stop_and_query_field, pulse -- so the
    controllers in this directory can drive either magnet without caring which
    one is plugged in. This magnet is Amps-only: set_field raises, and the
    field queries return None (no gaussmeter; see header).

    The number of energised coils (1-4) comes from params.ini [EM7000S] via
    devices.em7000s_coils(), settable in the GUI's Configuration tab.

    Protocol: 19200 Baud, 8-N-1, raw bytes; captured 2026-08-14.
    """
    max_current = MAX_CURRENT_A
    calibration_file = CALIBRATION_FILE

    def __init__(self, resource_name=os.getenv("EM_ID"), coils=None):
        self.resource_name = resource_name
        self.baud_rate = BAUD_RATE
        self.inst = None
        self.rm = pyvisa.ResourceManager()
        # Rig configuration, not a run parameter: read from params.ini unless
        # the caller overrides. Imported lazily so the driver stays runnable
        # standalone without dragging the registry in at module load.
        if coils is None:
            from devices import em7000s_coils
            coils = em7000s_coils()
        from devices import EM7000S_COILS_MIN, EM7000S_COILS_MAX
        if not EM7000S_COILS_MIN <= int(coils) <= EM7000S_COILS_MAX:
            raise ValueError(
                f"EM7000S coils must be {EM7000S_COILS_MIN}-"
                f"{EM7000S_COILS_MAX}, got {coils}."
            )
        self.coils = int(coils)

    # --- lifecycle -----------------------------------------------------------
    def connect(self):
        """Initializes and configures the serial connection."""
        if not SIGNALS_VERIFIED:
            raise RuntimeError(
                "EM7000S control signals are marked unverified; refusing to "
                "open the port. See the header of controllers/EM7000S.py."
            )
        if not self.resource_name or self.resource_name == "None":
            raise RuntimeError(
                "No magnet address: EM_ID is unset in controllers/.env. "
                "Run 'Detect Insts!' with the magnet powered and plugged in."
            )
        print(f"Connecting to {self.resource_name} at {self.baud_rate} baud "
              f"({self.coils} coil(s) energised)...")
        self.inst = self.rm.open_resource(self.resource_name)
        self.inst.baud_rate = self.baud_rate
        self.inst.data_bits = DATA_BITS
        self.inst.parity = PARITY
        self.inst.stop_bits = STOP_BITS
        self.inst.write_termination = None
        self.inst.read_termination = None
        self.inst.timeout = TIMEOUT_MS
        self.inst.clear()
        print("Connection successful.")
        return True

    def disconnect(self):
        """Closes the connection."""
        if self.inst:
            self.inst.close()
        self.rm.close()
        print("Resource manager closed.")

    # --- byte primitives -----------------------------------------------------
    def _read_one_byte(self):
        """Reads a single byte, returns int or None on timeout."""
        try:
            return self.inst.read_bytes(1)[0]
        except pyvisa.errors.VisaIOError:
            return None

    def _exchange(self, byte, expect):
        """
        Sends one byte and reads the one-byte reply, checking it against the
        protocol. A mismatch is loud but not fatal: mid-sequence the safest
        continuation is to finish the sequence, not to leave the device
        half-programmed.
        """
        self.inst.write_raw(bytes([byte]))
        reply = self._read_one_byte()
        if reply != expect:
            got = "timeout" if reply is None else f"0x{reply:02X}"
            print(f"  WARNING: sent 0x{byte:02X}, expected 0x{expect:02X}, "
                  f"got {got}")
        return reply

    def _command(self, opcode):
        return self._exchange(opcode, expected_reply(opcode))

    def _data(self, byte):
        return self._exchange(byte, byte)   # data bytes are echoed

    # --- protocol ------------------------------------------------------------
    def _current_map(self, current_amps):
        """Returns the 5-byte per-channel payload for a current in Amps."""
        sign = SIGN_POSITIVE
        if current_amps < 0:
            current_amps = abs(current_amps)
            sign = SIGN_NEGATIVE
        counts = _counts_for_amps(current_amps)
        print(f"  mapped {current_amps:.3f} A -> {counts} counts")
        high, low = divmod(counts, 0x100)
        return [high & 0xFF, low, VALUE_PAD, sign, VALUE_TAIL]

    def _run_start_sequence(self, value_bytes):
        """
        Runs one full set sequence: READY, an enable per energised coil, the
        payload written to each coil's channel, COMMIT.
        """
        if self.coils in (2, 3):
            print(f"  WARNING: the {self.coils}-coil sequence is inferred "
                  f"from the 1- and 4-coil captures, not itself captured. "
                  f"Verify the magnet behaves before trusting a run.")
        print(f"  Sending SET sequence to {self.coils} coil(s): "
              f"{[f'0x{b:02X}' for b in value_bytes]}")

        self._command(CMD_READY)
        for n in range(self.coils):
            self._command(ENABLE_COIL_BASE + n)
        for n in range(self.coils):
            self._command(CHANNEL_BASE + n)
            for value_byte in value_bytes:
                self._data(value_byte)
        self._command(CMD_COMMIT)
        print("  SET sequence complete.")

    # --- public API ----------------------------------------------------------
    def set_current(self, amps):
        """Sets every energised coil to the given current."""
        assert abs(amps) <= self.max_current, (
            f"Current out of range for Magnet Controller "
            f"(-{self.max_current}A to {self.max_current}A)."
        )
        self._run_start_sequence(self._current_map(amps))
        return amps

    def set_field(self, field):
        """
        Not available on this magnet: no working gaussmeter, so no calibration
        can exist to convert mT to Amps. devices.MAGNETS marks it
        supports_field=False, which is what hides mT in the GUI; this is the
        backstop for anything calling the driver directly.
        """
        raise NotImplementedError(
            "The EM7000S cannot be commanded in mT: its gaussmeter is broken, "
            "so no field calibration exists. Use Amps."
        )

    def stop(self):
        """De-energises the magnet: the single captured STOP byte."""
        print("  Sending STOP (0x27)...")
        self._command(CMD_STOP)
        print("  STOP complete.")

    def stop_and_query_field(self):
        """
        Stops the current. Returns None: this magnet offers no field reading
        (broken gaussmeter, and no query opcode has been captured either).
        Kept under the EM3000S name so the logic scripts work unchanged.
        """
        self.stop()
        print("  (no field reading available on the EM7000S)")
        return None

    def query_field(self):
        """No field reading available on the EM7000S; returns None."""
        print("  (no field reading available on the EM7000S)")
        return None

    def pulse(self, amps, duration_sec):
        """Holds the given current for a duration, then stops."""
        duration_sec = int(duration_sec)
        print(f"\n--- Pulsing magnet to {amps}A for {duration_sec} seconds ---")
        self.set_current(amps)
        time.sleep(duration_sec)
        self.stop()
        print("--- Pulse complete ---")


if __name__ == "__main__":
    magnet = MagnetController()
    magnet.connect()
    try:
        magnet.pulse(1.0, 3)
        magnet.pulse(-1.0, 3)
    finally:
        magnet.stop()
        magnet.disconnect()
