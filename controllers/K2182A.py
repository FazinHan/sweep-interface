"""
Keithley 2182/2182A nanovoltmeter, over VISA (GPIB or RS-232).

Reads one DC voltage per field point, alongside the VNA's S-parameters, for
electrically-detected FMR. Same lifecycle shape as VNA.py -- connect/close,
context manager, *IDN? check on connect -- so experiment.py drives it the
same way it drives everything else.

SCPI COMMANDS BELOW ARE NOT YET CONFIRMED AGAINST HARDWARE.
They follow the documented 2182A SCPI set, but the manual's body text could
not be extracted mechanically, so they have been written from the command
structure rather than copied from the page. Confirm each against the
*Model 2182/2182A User's Manual* (2182A-900-01 Rev. B) before a real run:

    :SENSe:FUNCtion / :SENSe:CHANnel            manual section 2 (2-20)
    :SENSe:VOLTage:NPLCycles                    section 3, Rate (3-7)
    :SENSe:VOLTage:CHANnelN:REFerence (REL)     section 4, Relative (4-4)
    :READ? vs :FETCh? vs :MEASure?              section 13 (13-2..13-4)

The read/fetch distinction is the one that matters most here. :FETCh? returns
the *last* reading the instrument took, which after settling a new field is
precisely the stale value this rig must not record. :READ? starts a fresh
acquisition and returns that, so it is what read_voltage() uses.

Being wrong about a SCPI string here is a failed query, not a damaged
instrument -- unlike the magnet drivers, this is a read-only device with no
energised state -- so it is safe to bench-test by simply running it.
"""
import os

import pyvisa
from dotenv import load_dotenv

load_dotenv()

#: Answer to *IDN? must contain this for the instrument to be accepted.
EXPECTED_MODEL = '2182'

DEFAULT_TIMEOUT_MS = 20000
DEFAULT_CHANNEL = 1
DEFAULT_NPLC = 5.0

#: NPLC bounds accepted by the instrument (power line cycles per reading).
NPLC_MIN = 0.01
NPLC_MAX = 60.0


class NanovoltmeterController:
    """
    Thin wrapper for the 2182A's DC voltage function.

    channel: 1 or 2, matching how the sample is wired into the LEMO input.
    nplc:    integration time in power line cycles. Higher is slower and
             quieter; a field sweep is gated by the magnet settle time
             anyway, so there is rarely a reason to rush this.
    """

    def __init__(self, resource_name=None, channel=DEFAULT_CHANNEL,
                 nplc=DEFAULT_NPLC, timeout_ms=DEFAULT_TIMEOUT_MS,
                 backend=None):
        self.resource_str = resource_name or os.getenv("DVM_ID")
        if channel not in (1, 2):
            raise ValueError(f"2182A channel must be 1 or 2, got {channel}.")
        self.channel = int(channel)
        self.nplc = self._validate_nplc(nplc)
        self.timeout_ms = timeout_ms
        if backend is None:
            from devices import VISA_BACKEND
            backend = VISA_BACKEND
        self.backend = backend
        self.rm = None
        self.inst = None

    @staticmethod
    def _validate_nplc(nplc):
        try:
            value = float(nplc)
        except (TypeError, ValueError):
            raise ValueError(f"NPLC must be a number, got {nplc!r}.")
        if not NPLC_MIN <= value <= NPLC_MAX:
            raise ValueError(
                f"NPLC must be between {NPLC_MIN} and {NPLC_MAX}, got {value}."
            )
        return value

    # --- lifecycle ------------------------------------------------------------
    def connect(self):
        if not self.resource_str or self.resource_str == "None":
            raise RuntimeError(
                "No nanovoltmeter address: DVM_ID is unset in controllers/.env. "
                "Run 'Detect Insts!' with the voltmeter box ticked."
            )
        self.rm = (pyvisa.ResourceManager(self.backend) if self.backend
                   else pyvisa.ResourceManager())
        self.inst = self.rm.open_resource(self.resource_str)
        self.inst.timeout = self.timeout_ms
        self.inst.read_termination = '\n'
        self.inst.write_termination = '\n'

        idn = self.inst.query("*IDN?").strip()
        if EXPECTED_MODEL not in idn:
            raise RuntimeError(f"Unexpected instrument at {self.resource_str}: "
                               f"{idn}")

        self.inst.write("*RST")
        self.inst.write("*CLS")
        # DC volts on the wired channel, autoranged, integrating over `nplc`
        # line cycles. INIT:CONT OFF leaves it idle so nothing is acquired
        # until :READ? asks for it -- a free-running meter would hand back a
        # reading taken before the field finished settling.
        self.inst.write(f":SENSe:CHANnel {self.channel}")
        self.inst.write(":SENSe:FUNCtion 'VOLTage:DC'")
        self.inst.write(f":SENSe:VOLTage:CHANnel{self.channel}:RANGe:AUTO ON")
        self.inst.write(f":SENSe:VOLTage:NPLCycles {self.nplc}")
        self.inst.write(":INITiate:CONTinuous OFF")
        print(f"Nanovoltmeter: {idn}")
        print(f"  channel {self.channel}, {self.nplc} NPLC, autorange on")
        return idn

    def close(self):
        if self.inst is not None:
            try:
                self.inst.close()
            finally:
                self.inst = None
        if self.rm is not None:
            try:
                self.rm.close()
            finally:
                self.rm = None

    # Named to match the magnet drivers so teardown code can be uniform.
    disconnect = close

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # --- public API -----------------------------------------------------------
    def zero(self):
        """
        Cancels standing offsets using the instrument's REL feature.

        Call once before a sweep with the RF drive off: whatever it reads at
        that moment (thermal EMF in the connections, mostly) becomes the new
        zero. Thermal EMFs in a nanovolt measurement are routinely larger than
        the signal, so this is not optional in practice.

        It follows that calling this with the drive ON would null away the
        very signal being measured.
        """
        self._require_connection()
        self.inst.write(f":SENSe:VOLTage:CHANnel{self.channel}:REFerence:ACQuire")
        self.inst.write(f":SENSe:VOLTage:CHANnel{self.channel}:REFerence:STATe ON")
        reference = self.inst.query(
            f":SENSe:VOLTage:CHANnel{self.channel}:REFerence?").strip()
        print(f"  nulled against {float(reference):.9g} V")
        return float(reference)

    def read_voltage(self):
        """
        Triggers one fresh reading and returns it in volts.

        :READ? rather than :FETCh? -- see the module docstring. Returns None
        if the instrument does not answer, so one failed reading costs one
        point rather than the whole sweep.
        """
        self._require_connection()
        try:
            return float(self.inst.query(":READ?").strip())
        except (pyvisa.errors.VisaIOError, ValueError) as exc:
            print(f"  WARNING: nanovoltmeter read failed ({type(exc).__name__});"
                  f" recording it blank.")
            return None

    def _require_connection(self):
        if self.inst is None:
            raise RuntimeError("Not connected. Call connect() first.")


if __name__ == "__main__":
    # Bench test: no magnet, no VNA. Confirms the SCPI above is right.
    with NanovoltmeterController() as dvm:
        dvm.zero()
        for _ in range(5):
            print(f"  {dvm.read_voltage()} V")
