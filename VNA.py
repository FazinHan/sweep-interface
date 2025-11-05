import pyvisa
import numpy as np
import os
from dotenv import load_dotenv
import time

load_dotenv()

class VNAController:
    """
    Thin wrapper for R&S ZNLE SCPI over VISA (LAN).
    Provides read_s11/s12/s21/s22 methods returning (freq_hz, complex_sparam).
    """
    def __init__(self, timeout_ms=50000, backend='@py'):
        """
        ip: string, e.g. '192.168.1.20'
        timeout_ms: VISA timeout in milliseconds
        backend: optional VISA backend string for pyvisa.ResourceManager(), e.g. '@ni'
        """
        # self.ip = os.getenv("VNA_IP")
        self.resource_str = os.getenv("VNA_ID")
        self.backend = backend
        self.timeout_ms = timeout_ms
        self.rm = None
        self.vna = None

    # --- lifecycle ------------------------------------------------------------
    def connect(self):
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
            # Define and bind the measurement to a (visible) trace if needed
            v.write(f"CALC1:PAR:DEF '{trace_name}',{code}")
            # Optional: create a new trace window binding (if none exists for this name)
            # Many VNAs auto-bind, but this is safe:
            v.write(f"DISP:WIND1:TRAC1:FEED '{trace_name}'")

        # Select it so CALC1:DATA? applies to this measurement
        v.write(f"CALC1:PAR:SEL '{trace_name}'")

if __name__ == "__main__":
    with VNAController() as vna:
        freq, s11 = vna.read_s11()
        print(f"Read {len(s11)} S11 points from VNA at {vna.ip}")