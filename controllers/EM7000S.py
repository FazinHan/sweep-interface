import pyvisa
import time, os
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONTROL SIGNALS  --  NOT YET VERIFIED AGAINST REAL HARDWARE
# =============================================================================
# The EM3000S command set was never published: it was recovered by capturing
# the vendor's Windows application talking to the magnet over the USB-serial
# link, then replaying the byte sequences. The EM7000S has to be captured the
# same way, so every value that a capture will tell us lives in this one block
# instead of being scattered through the methods below.
#
# The placeholders are the EM3000S values (same vendor, same family, so they
# are the best first guess), NOT measurements of an EM7000S. Until a capture
# confirms them, connect() refuses to open the port.
#
# To bring this driver up:
#   1. capture the vendor app driving an EM7000S,
#   2. correct the constants below,
#   3. re-fit CURRENT_FIT_COEFFS from a current sweep (see calibration.py),
#   4. set SIGNALS_VERIFIED = True.
# =============================================================================

#: Flip to True once the block below has been confirmed on a real EM7000S.
SIGNALS_VERIFIED = False

# --- Link settings -----------------------------------------------------------
BAUD_RATE = 19200
DATA_BITS = 8
PARITY = pyvisa.constants.Parity.none
STOP_BITS = pyvisa.constants.StopBits.one
TIMEOUT_MS = 2000

# --- Opcodes -----------------------------------------------------------------
CMD_READY = 0x64        # "are you there" ping; device answers with one byte
CMD_START = 0x1E        # begin a set sequence; poll until ACK comes back
CMD_SET_VALUE = 0x2C    # "the value payload follows"
CMD_END_SET = 0x00      # terminate the set sequence; poll for ACK
CMD_STOP = 0x2B         # stop / de-energise; poll for ACK
CMD_QUERY_FIELD = 0x0A  # query gaussmeter; 3 bytes come back, each echoed
CMD_STOP_TAIL = (0x4E, 0x00)  # tail of the stop sequence; only the first replies
CMD_END = 0x82          # close the stop sequence; poll for ACK
ACK = 0x12              # the byte every polled step waits for

# --- Value payload -----------------------------------------------------------
# Sent as [high byte, low byte, VALUE_PAD, sign flag].
VALUE_PAD = 0x00
SIGN_POSITIVE = 1
SIGN_NEGATIVE = 0

# --- Field decode ------------------------------------------------------------
# field_mT = ((b1 << 8) | b2) / FIELD_SCALE, negated when b3 == FIELD_SIGN_NEGATIVE
FIELD_SCALE = 10.0
FIELD_SIGN_NEGATIVE = 0x01

# --- Current encode ----------------------------------------------------------
# Amps -> device integer, cubic, highest power first (np.polyval order).
CURRENT_FIT_COEFFS = (4.76264, 2.00444, 252.08648, -8.46937)

# --- Ranges and timing -------------------------------------------------------
MAX_CURRENT_A = 4.0            # the EM7000S almost certainly differs; measure it
STARTUP_DELAY_SEC = -2.0       # slack subtracted from pulse() hold times
CALIBRATION_FILE = 'field_calibration_data_em7000s.csv'  # written by calibration.py

# =============================================================================


class MagnetController:
    """
    A PyVISA-based controller for the Holmarc HO-EM7000S electromagnet.

    Same public API as EM3000S.MagnetController — connect/disconnect,
    set_current/set_field, query_field/stop_and_query_field, pulse — so the
    controllers in this directory can drive either magnet without caring which
    one is plugged in. Everything device-specific comes from the control-signal
    block at the top of this file.

    Protocol: 19200 Baud, 8-N-1, Raw Byte Commands (provisional).
    """
    startup_delay_sec = STARTUP_DELAY_SEC
    max_current = MAX_CURRENT_A
    calibration_file = CALIBRATION_FILE

    def _current_map(self, current_amps):
        """Returns the 4-byte value array for a given current in Amps."""
        pos = SIGN_POSITIVE            # sign flag rides in the last payload byte
        if current_amps < 0:
            current_amps = abs(current_amps)
            pos = SIGN_NEGATIVE
        mapped = int(np.polyval(CURRENT_FIT_COEFFS, current_amps))
        print(f"  mapped {current_amps:.3f} A -> {mapped}")
        # Split into high/low bytes directly. EM3000S does this by slicing the
        # hex string, which silently yields 0 for values below 0x10.
        high, low = divmod(max(mapped, 0), 0x100)
        return [high & 0xFF, low, VALUE_PAD, pos]

    def __init__(self, resource_name=os.getenv("EM_ID")):
        self.resource_name = resource_name
        self.baud_rate = BAUD_RATE
        self.inst = None
        self.rm = pyvisa.ResourceManager()

    def connect(self):
        """Initializes and configures the serial connection."""
        if not SIGNALS_VERIFIED:
            raise RuntimeError(
                "EM7000S control signals have not been verified yet. The opcodes "
                "at the top of controllers/EM7000S.py are EM3000S placeholders; "
                "capture the vendor application driving an EM7000S, correct them, "
                "then set SIGNALS_VERIFIED = True."
            )
        print(f"Connecting to {self.resource_name} at {self.baud_rate} baud...")
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

    def _write(self, byte):
        """Sends one raw byte."""
        self.inst.write_raw(bytes([byte]))

    def _read_one_byte(self):
        """Reads a single byte, returns int or None."""
        try:
            return self.inst.read_bytes(1)[0]
        except pyvisa.errors.VisaIOError:
            return None

    def _poll_for_byte(self, expected_byte):
        """Keeps reading until a specific byte is found or timeout."""
        try:
            while True:
                response = self.inst.read_bytes(1)[0]
                if response == expected_byte:
                    return response
        except pyvisa.errors.VisaIOError:
            return None

    def _run_start_sequence(self, value_bytes):
        """Sends the full 10-step START sequence."""
        print(f"  Sending START sequence: {[f'0x{b:02X}' for b in value_bytes]}")

        self._write(CMD_READY); self._read_one_byte()      # 1. Ready
        self._write(CMD_READY); self._read_one_byte()      # 2. Ready
        self._write(CMD_START); self._poll_for_byte(ACK)   # 3. Start
        self._write(CMD_READY); self._read_one_byte()      # 4. Ready
        self._write(CMD_SET_VALUE); self._read_one_byte()  # 5. Set Value

        # Steps 6-9: Send the 4-byte value
        for value_byte in value_bytes:
            self._write(value_byte); self._read_one_byte()

        # Step 10: End Command
        self._write(CMD_END_SET); self._poll_for_byte(ACK)
        print("  START sequence complete.")

    def set_current(self, amps):
        """
        Sets the electromagnet current to a known value.
        """
        assert abs(amps) <= self.max_current, (
            f"Current out of range for Magnet Controller "
            f"(-{self.max_current}A to {self.max_current}A)."
        )
        value_bytes = self._current_map(amps)
        self._run_start_sequence(value_bytes)
        return amps

    def set_field(self, field):
        """
        Sets the electromagnet field to a known value in mT based on
        calibration data. Run calibration.py against this magnet to generate.
        """
        if not os.path.exists(self.calibration_file):
            raise FileNotFoundError(
                f"{self.calibration_file} not found — run a calibration with the "
                f"EM7000S selected before sweeping in mT."
            )
        dataframe = pd.read_csv(self.calibration_file)
        field_cal = dataframe['Field_mT'].values
        current_cal = dataframe['Current_A'].values
        coeffs = np.polyfit(field_cal, current_cal, 3)  # re-fits on every call - SLOW
        current_from_field = np.poly1d(coeffs)
        self.set_current(current_from_field(field))
        return field

    def _read_field_bytes(self):
        """
        Sends the field query and echoes the three bytes back, as the device
        expects. Returns (b1, b2, b3) or None if the read timed out.
        """
        self._write(CMD_QUERY_FIELD)

        received = []
        for _ in range(3):  # magnitude high, magnitude low, sign flag
            byte = self._read_one_byte()
            if byte is None:
                return None
            self._write(byte)  # every byte must be echoed back
            received.append(byte)
        return tuple(received)

    def _decode_field(self, field_bytes):
        """Turns the three queried bytes into a field in mT."""
        byte1, byte2, byte3 = field_bytes
        raw_magnitude = (byte1 << 8) | byte2
        scaled_magnitude = raw_magnitude / FIELD_SCALE
        return -scaled_magnitude if byte3 == FIELD_SIGN_NEGATIVE else scaled_magnitude

    def stop_and_query_field(self):
        """
        Stops the current and queries the field, replicating the log sequence.
        Returns the field reading in mT.
        """
        print("\n  Sending STOP and QUERY sequence...")

        # --- Part 1: Send STOP command ---
        self._write(CMD_READY); self._read_one_byte()   # Ready Check
        self._write(CMD_STOP); self._poll_for_byte(ACK)  # Stop Cmd

        # --- Part 2: Send QUERY command ---
        field_bytes = self._read_field_bytes()
        if field_bytes is None:
            return "Query Failed"

        # --- Part 3: Finish the STOP sequence ---
        self._write(CMD_STOP_TAIL[0]); self._read_one_byte()
        self._write(CMD_STOP_TAIL[1])  # no response expected

        self._write(CMD_READY); self._read_one_byte()  # Ready Check
        self._write(CMD_END); self._poll_for_byte(ACK)  # End Cmd

        print("  STOP/QUERY sequence complete.")

        try:
            final_value = self._decode_field(field_bytes)
            print(f"  Received Bytes: {[f'0x{b:02X}' for b in field_bytes]}")
            print(f"  Decoded Field: {final_value} mT")
            return final_value
        except Exception as e:
            return f"Query Failed: Error decoding bytes: {e}"

    def query_field(self):
        """
        Queries the field without stopping the current.
        Returns the field reading in mT.
        """
        # NOTE: inherited from EM3000S, where the stop command below means this
        # does interrupt the drive despite the docstring. Re-check on capture.
        self._write(CMD_READY); self._read_one_byte()   # Ready Check
        self._write(CMD_STOP); self._poll_for_byte(ACK)  # Stop Cmd

        field_bytes = self._read_field_bytes()
        if field_bytes is None:
            return "Query Failed"

        try:
            return self._decode_field(field_bytes)
        except Exception as e:
            Warning(f"Query Failed: Error decoding bytes: {e}")
            return None

    def current_map_test(self):
        currs = np.arange(-.4, .4, 0.1)
        for curr in currs:
            print(f"--- Querying for {curr}A ---")
            self.set_current(curr)
            time.sleep(2)
            field = self.stop_and_query_field()
            print(f"  Measured Field: {field} mT")
            print("---                       ---")

    def pulse(self, amps, duration_sec):
        """
        Pulses the magnet to a specified current for a given duration.
        """
        if not isinstance(duration_sec, int):
            Warning("Duration should be an integer number of seconds. Using floor...")
            duration_sec = int(duration_sec)
        print(f"\n--- Pulsing magnet to {amps}A for {duration_sec} seconds ---")
        self.set_current(amps)
        for i in range(int(duration_sec + self.startup_delay_sec)):
            print(self.query_field(), "mT")
            time.sleep(1)
        field = self.stop_and_query_field()
        print(f"  Measured Field after pulse: {field} mT")
        print("--- Pulse complete ---")


if __name__ == "__main__":
    magnet = MagnetController(resource_name='ASRL5::INSTR')
    magnet.connect()
    magnet.pulse(3.0, 5)
    magnet.pulse(-3.0, 5)
    magnet.disconnect()
