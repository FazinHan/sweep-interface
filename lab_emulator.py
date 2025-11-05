import os
import numpy as np

class VNAController:
    """
    Facilitating a virtual VNA for development.
    Returns data saved from an R&S ZNLE18 VNA.
    """
    def __init__(self, timeout_ms=50000, backend=None):
        self.rm = None
        self.vna = None
        with open(os.path.join("dev","s_parameters.npz"), 'rb') as f:
            self.data = np.load(f)
            self.freq = self.data['frequency']
            self.s11 = self.data['S11']
            self.s12 = self.data['S12']
            self.s21 = self.data['S21']
            self.s22 = self.data['S22']

    def connect(self):
        print("Emulated VNA connected.")
        self.rm = True
        self.vna = True
        return "EMULATED_VNA"

    def close(self):
        print("Emulated VNA disconnected.")
        self.vna = None
        self.rm = None

    def read_s11(self):
        freq = self.freq
        s11 = self.s11
        return freq, s11
    
    def read_s12(self):
        freq = self.freq    
        s12 = self.s12
        return freq, s12

    def read_s21(self):
        freq = self.freq
        s21 = self.s21
        return freq, s21
    
    def read_s22(self):
        freq = self.freq
        s22 = self.s22
        return freq, s22

    def disconnect(self):
        self.rm = None
        self.vna = None

class MagnetController:
    """
    Facilitating a virtual Magnet Controller for development.
    Simulates setting current and stopping.
    """
    def __init__(self, resource_name=None):
        self.inst = None
        self.rm = None
        self.current = 0.0

    def connect(self):
        print("Emulated Magnet Controller connected.")
        self.inst = True
        return "EMULATED_MAGNET"

    def disconnect(self):
        self.rm = None
        self.vna = None

    def set_current(self, current):
        if self.inst is None:
            raise RuntimeError("Magnet Controller not connected.")
        self.current = current
        print(f"Emulated Magnet current set to {current} A.")

    def set_field(self, field):
        if self.inst is None:
            raise RuntimeError("Magnet Controller not connected.")
        self.field = field
        print(f"Emulated Magnet field set to {field} mT.")

    def stop_and_query_field(self):
        if self.inst is None:
            raise RuntimeError("Magnet Controller not connected.")
        print("Emulated Magnet stopped. Current field queried.")

    def disconnect(self):
        print("Emulated Magnet Controller disconnected.")
        self.inst = None
    
    def query_field(self):
        if self.inst is None:
            raise RuntimeError("Magnet Controller not connected.")
        return 150.0  # Return a dummy field value

if __name__ == "__main__":
    magnet = MagnetController()
    magnet.connect()
    magnet.set_current(1.0)
