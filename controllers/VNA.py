import pyvisa
import numpy as np
import os
from dotenv import load_dotenv
import warnings
import time

load_dotenv()

class VNAController:
    """
    Thin wrapper for R&S ZNLE SCPI over VISA (LAN).
    Provides read_s11/s12/s21/s22 methods returning (freq_hz, complex_sparam).
    """
    #: How far the read-back power may sit from the requested value before it
    #: counts as a failure. The value makes a round trip through the
    #: instrument's own text formatting, so an exact comparison would warn
    #: about settings that were applied perfectly well.
    POWER_TOLERANCE_DB = 0.05

    def __init__(self, timeout_ms=50000, backend=None):
        """
        timeout_ms: VISA timeout in milliseconds
        backend: VISA backend string for pyvisa.ResourceManager(). Defaults to
            devices.VISA_BACKEND (the system library, i.e. NI-VISA) so the VNA
            and the magnets go through the same VISA implementation. Pass
            '@py' to force pyvisa-py.
        """
        self.resource_str = os.getenv("VNA_ID")
        if backend is None:
            from devices import VISA_BACKEND
            backend = VISA_BACKEND
        self.backend = backend
        self.timeout_ms = timeout_ms
        self.rm = None
        self.vna = None

    # --- lifecycle ------------------------------------------------------------
    def connect(self):
        if not self.resource_str or self.resource_str == "None":
            raise RuntimeError(
                "No VNA address: VNA_ID is unset in controllers/.env. "
                "Run 'Detect Insts!' with the VNA powered and on the network."
            )
        self.rm = pyvisa.ResourceManager(self.backend) if self.backend else pyvisa.ResourceManager()
        self.vna = self.rm.open_resource(self.resource_str)
        self.vna.timeout = self.timeout_ms
        self.vna.read_termination = '\n'
        self.vna.write_termination = '\n'

        idn = self.vna.query("*IDN?")
        if "ZNLE" not in idn and "ZNL" not in idn:  # some firmwares report ZNL/ZNLE similarly
            raise RuntimeError(f"Unexpected instrument: {idn.strip()}")
        # deterministic sweeps
        self.vna.write("INIT1:CONT OFF")
        return idn.strip()

    def close(self):
        if self.vna is not None:
            try:
                self.vna.close()
            finally:
                self.vna = None
        if self.rm is not None:
            try:
                self.rm.close()
            finally:
                self.rm = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # --- public API -----------------------------------------------------------
    # --- public API -----------------------------------------------------------
    def set_power(self, power_dbm):
        """
        Sets the internal RF source driving power in dBm for Channel 1.
        """
        if self.vna is None:
            raise RuntimeError("Not connected. Call connect() first.")
        
        # SOURce1:POWer sets the base power level
        self.vna.write(f"SOUR1:POW {power_dbm}")
        print(f"VNA source power set to {power_dbm} dBm")

        # Read back once and compare with a tolerance: the value makes a round
        # trip through the instrument's own text formatting, so an exact float
        # comparison warns about settings that were applied perfectly well.
        actual = self.get_power()
        if abs(actual - float(power_dbm)) > self.POWER_TOLERANCE_DB:
            warnings.warn(f"Failed to set power to {power_dbm} dBm. Current power: {actual} dBm")

    def get_power(self):
        """
        Queries the current RF source driving power in dBm.
        """
        if self.vna is None:
            raise RuntimeError("Not connected. Call connect() first.")
        
        power = self.vna.query("SOUR1:POW?").strip()
        return float(power)

    def read_s11(self): return self._read_sparam("S11", trace_name="MeasS11")
    def read_s12(self): return self._read_sparam("S12", trace_name="MeasS12")
    def read_s21(self): return self._read_sparam("S21", trace_name="MeasS21")
    def read_s22(self): return self._read_sparam("S22", trace_name="MeasS22")

    # --- internals ------------------------------------------------------------
    def _read_sparam(self, code, trace_name="Meas"):
        """
        Ensure a trace for the given S-parameter exists on channel 1,
        run a single sweep, return (freq_hz, complex_values).
        """
        v = self.vna
        if v is None:
            raise RuntimeError("Not connected. Call connect() first.")

        # ensure the measurement exists and is selected
        self._ensure_measurement(code, trace_name)
        # print(f"Measurement {code} {trace_name} exists")

        t0 = time.time()
        # trigger one sweep and wait until done
        v.write("INIT1; *WAI")
        # print(f"Sweep for {code} done in {time.time()-t0:.3f} s")

        # get complex data: interleaved Re,Im
        raw = v.query("CALC1:DATA? SDAT")
        # print(f"Raw data length for {code}: {len(raw)}")
        data = np.fromstring(raw, sep=",", dtype=float)
        real = data[0::2]
        imag = data[1::2]
        s_complex = real + 1j * imag

        # get frequency axis (Hz)
        # (On ZNLE, SENSe1:FREQuency:DATA? yields an array of frequency points.)
        # Build frequency vector from sweep settings
        f_start = float(v.query("SENS1:FREQ:STAR?"))
        f_stop  = float(v.query("SENS1:FREQ:STOP?"))
        npts    = int(float(v.query("SENS1:SWE:POIN?")))
        freq = np.linspace(f_start, f_stop, npts)
        # f_raw = v.query("SENS1:FREQ:DATA?") ### TIMEOUT HERE
        # print(f"Raw frequency data length for {code}: {len(freq)}")
        # freq = np.fromstring(f_raw, sep=",", dtype=float)

        # sanity alignment
        if freq.size != s_complex.size:
            raise RuntimeError(f"Point count mismatch: freq={freq.size}, data={s_complex.size}")

        return freq, s_complex

    #: Trace slot in window 1 for each S-parameter's display binding.
    #:
    #: One slot each, because a slot holds exactly one measurement. This used
    #: to be hardcoded to TRAC1 for all four, so S11 claimed the slot and the
    #: other three were rejected with "-114: Header suffix out of range" -- the
    #: suffix being out of range because that trace was already bound. Only the
    #: display binding failed, never the data (CALC1:PAR:SEL below runs either
    #: way), so sweeps were correct while the instrument showed one trace and
    #: an error.
    TRACE_SLOTS = {'S11': 1, 'S12': 2, 'S21': 3, 'S22': 4}

    def _ensure_measurement(self, code, trace_name):
        """
        Create/select a measurement named `trace_name` for S-parameter `code` (e.g., 'S21').
        Idempotent: if it exists, just selects it.
        """
        v = self.vna

        # Query existing parameters on channel 1
        # Returns: "name1,def1,name2,def2,..."
        cat = v.query("CALC1:PAR:CAT?").strip().strip('"')
        tokens = [t for t in cat.split(",") if t] if cat else []

        exists = False
        for i in range(0, len(tokens), 2):
            name_i = tokens[i]
            if name_i == trace_name:
                exists = True
                break

        if not exists:
            # Define the measurement, then bind it to its own trace slot so it
            # is visible on the instrument. An unknown code falls back to slot
            # 1: displaying it matters less than not guessing a slot number.
            v.write(f"CALC1:PAR:DEF '{trace_name}',{code}")
            slot = self.TRACE_SLOTS.get(code.upper(), 1)
            v.write(f"DISP:WIND1:TRAC{slot}:FEED '{trace_name}'")

        # Select it so CALC1:DATA? applies to this measurement
        v.write(f"CALC1:PAR:SEL '{trace_name}'")

if __name__ == "__main__":
    with VNAController() as vna:
        freq, s11 = vna.read_s11()
        print(f"Read {len(s11)} S11 points from VNA at {vna.ip}")