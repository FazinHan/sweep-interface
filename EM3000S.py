import pyvisa
import time,os
import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

class MagnetController:
    """
    A PyVISA-based controller for the Holmarc EM-series electromagnet
    based on reverse-engineered packet captures.
    
    Protocol: 19200 Baud, 8-N-1, Raw Byte Commands
    """
    startup_delay_sec = -2.0  # Time to wait 

    def _current_map(self,current_amps):
        """Returns the 4-byte value array for a given current in Amps."""
        def map_func(amp):
            # amp = int((1299-35)/(4.0-0.1)*amp)
            amp = 4.76264*amp**3 + 2.00444*amp**2 + 252.08648*amp - 8.46937
            print(amp)
            return int(amp)
        pos = 1
        if current_amps<0:
            current_amps = abs(current_amps)
            pos = 0
        mapped = hex(map_func(current_amps))
        return_list = [0x00]*4
        try:
            return_list[1] = int(mapped[-2:],16)
        except ValueError:
            return_list[1] = 0
        if (zeroth:=mapped.split('x')[1][:-2])!='':
            return_list[0] = int(zeroth,16)
        return_list[-1] = pos
        return return_list

    def __init__(self, resource_name=os.getenv("EM_ID")):
        self.resource_name = resource_name
        self.baud_rate = 19200
        self.inst = None
        self.rm = pyvisa.ResourceManager()
        self.connect()

    def connect(self):
        """Initializes and configures the serial connection."""
        print(f"Connecting to {self.resource_name} at {self.baud_rate} baud...")
        self.inst = self.rm.open_resource(self.resource_name)
        self.inst.baud_rate = self.baud_rate
        self.inst.data_bits = 8
        self.inst.parity = pyvisa.constants.Parity.none
        self.inst.stop_bits = pyvisa.constants.StopBits.one
        self.inst.write_termination = None
        self.inst.read_termination = None
        self.inst.timeout = 2000  # 2-second timeout
        self.inst.clear()
        print("Connection successful.")
        return True

    def disconnect(self):
        """Closes the connection."""
        if self.inst:
            self.inst.close()
        self.rm.close()
        print("Resource manager closed.")

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
        
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # 1. Ready
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # 2. Ready
        self.inst.write_raw(bytes([0x1E])); self._poll_for_byte(0x12) # 3. Start
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # 4. Ready
        self.inst.write_raw(bytes([0x2C])); self._read_one_byte() # 5. Set Value
        
        # Steps 6-9: Send the 4-byte value
        self.inst.write_raw(bytes([value_bytes[0]])); self._read_one_byte()
        self.inst.write_raw(bytes([value_bytes[1]])); self._read_one_byte()
        self.inst.write_raw(bytes([value_bytes[2]])); self._read_one_byte()
        self.inst.write_raw(bytes([value_bytes[3]])); self._read_one_byte()
        
        # Step 10: End Command
        self.inst.write_raw(bytes([0x00])); self._poll_for_byte(0x12)
        print("  START sequence complete.")

    def set_current(self, amps):
        """
        Sets the electromagnet current to a known value.
        """
        # if amps not in self.CURRENT_MAP:
        #     print(f"Error: {amps}A is not in the known value map.")
        #     print(f"Please capture packets for this value and add it to CURRENT_MAP.")
        #     return

        # value_bytes = self.CURRENT_MAP[amps]
        value_bytes = self._current_map(amps)
        self._run_start_sequence(value_bytes)

    def set_field(self, field):
        """
        Sets the electromagnet field to a known value in mT based on
        calibration data. Run field_calibration.py to generate.
        """
        dataframe = pd.read_csv('field_calibration_data.csv')
        field_cal = dataframe['Field_mT'].values
        current_cal = dataframe['Current_A'].values
        idx = (np.abs(field_cal - field)).argmin()
        self.set_current(current_cal[idx])
        return field_cal[idx]

    def stop_and_query_field(self):
        """
        Stops the current and queries the field, replicating the log sequence.
        Returns the field reading in mT.
        """
        print("\n  Sending STOP and QUERY sequence...")
        
        # --- Part 1: Send STOP command ---
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # Ready Check
        self.inst.write_raw(bytes([0x2B])); self._poll_for_byte(0x12) # Stop Cmd
        
        # --- Part 2: Send QUERY command (0x0A) ---
        self.inst.write_raw(bytes([0x0A])) # The query
        
        byte1 = self._read_one_byte() # Field Mag High Byte
        if byte1 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte1])) # Echo
        
        byte2 = self._read_one_byte() # Field Mag Low Byte
        if byte2 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte2])) # Echo
        
        byte3 = self._read_one_byte() # Field Sign Flag
        if byte3 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte3])) # Echo

        # --- Part 3: Finish the STOP sequence ---
        # We use the sequence from the -1.0A log (packets_-1.txt)
        # as it seems to be a reliable "set to zero"
        self.inst.write_raw(bytes([0x4E])); self._read_one_byte()
        self.inst.write_raw(bytes([0x00])); # No response in log
        
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # Ready Check
        self.inst.write_raw(bytes([0x82])); self._poll_for_byte(0x12) # End Cmd
        
        print("  STOP/QUERY sequence complete.")

        # --- Decode and return the value ---
        try:
            raw_magnitude = (byte1 << 8) | byte2
            scaled_magnitude = raw_magnitude / 10.0 # Our 10x scaling factor
            
            # Sign Flag: 0x01 = Negative, 0x00 = Positive
            final_value = -scaled_magnitude if byte3 == 0x01 else scaled_magnitude
            
            print(f"  Received Bytes: [0x{byte1:02X}, 0x{byte2:02X}, 0x{byte3:02X}]")
            print(f"  Decoded Field: {final_value} mT")
            return final_value
        except Exception as e:
            return f"Query Failed: Error decoding bytes: {e}"
    
    def query_field(self):
        """
        Queries the field without stopping the current.
        Returns the field reading in mT.
        """
        # print("\n  Sending QUERY sequence...")

        # --- Part 1: Send STOP command ---
        self.inst.write_raw(bytes([0x64])); self._read_one_byte() # Ready Check
        self.inst.write_raw(bytes([0x2B])); self._poll_for_byte(0x12) # Stop Cmd
        
        # --- Send QUERY command (0x0A) ---
        self.inst.write_raw(bytes([0x0A])) # The query
        
        byte1 = self._read_one_byte() # Field Mag High Byte
        if byte1 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte1])) # Echo
        
        byte2 = self._read_one_byte() # Field Mag Low Byte
        if byte2 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte2])) # Echo
        
        byte3 = self._read_one_byte() # Field Sign Flag
        if byte3 is None: return "Query Failed"
        self.inst.write_raw(bytes([byte3])) # Echo

        # print("  QUERY sequence complete.")

        # --- Decode and return the value ---
        try:
            raw_magnitude = (byte1 << 8) | byte2
            scaled_magnitude = raw_magnitude / 10.0 # Our 10x scaling factor
            
            # Sign Flag: 0x01 = Negative, 0x00 = Positive
            final_value = -scaled_magnitude if byte3 == 0x01 else scaled_magnitude
            
            # print(f"  Received Bytes: [0x{byte1:02X}, 0x{byte2:02X}, 0x{byte3:02X}]")
            # print(f"  Decoded Field: {final_value} mT")
            return final_value
        except Exception as e:
            Warning(f"Query Failed: Error decoding bytes: {e}")
            return None

    def current_map_test(self):
        currs = np.arange(-.4,.4,0.1)
        for curr in currs:
            print(f"--- Querying for {curr}A ---")
            self.set_current(curr)
            time.sleep(2)
            field = magnet.stop_and_query_field()
            print(f"  Measured Field: {field} mT")
            print("---                       ---")

    def pulse(self, amps, duration_sec):
        """
        Pulses the magnet to a specified current for a given duration. 
        BUG: Sends 0 current sometimes.
        """
        if not isinstance(duration_sec, int):
            Warning("Duration should be an integer number of seconds. Using floor...")
            duration_sec = int(duration_sec)
        print(f"\n--- Pulsing magnet to {amps}A for {duration_sec} seconds ---")
        self.set_current(amps)
        # print(f"  Holding for {duration_sec} seconds...")
        # time.sleep(duration_sec+self.startup_delay_sec)
        for i in range(int(duration_sec+self.startup_delay_sec)):
            # print(f"    {i+1} seconds elapsed...")
            print(self.query_field(),"mT")
            time.sleep(1)
        field = self.stop_and_query_field()
        print(f"  Measured Field after pulse: {field} mT")
        print("--- Pulse complete ---")

# # --- Main script to run the test ---
# if __name__ == "__main__":
    
#     magnet = HolmarcMagnet(resource_name='ASRL5::INSTR')
    
#     if magnet.connect():
#         try:
#             # --- Test 1: Set to +1.0A ---
#             # print("\n*** TEST 1: SETTING +1.0A ***")
#             # magnet.set_current(1.0)
            
#             # print("  Waiting for 1 second...")
#             # time.sleep(1.0)
            
#             # field = magnet.stop_and_query_field()
#             # print(f"  Measured Field: {field} mT")
            
#             # print("\n  Pausing for 3 seconds...")
#             # time.sleep(3.0)
            
#             # --- Test 2: Set to -4.0A ---
#             # print("\n*** TEST 2: SETTING -3.9A ***")
#             # magnet.set_current('test')
            
#             # print("  Waiting for 1 second...")
#             # time.sleep(1.0)
            
#             # field = magnet.stop_and_query_field()
#             # print(f"  Measured Field: {field} mT")
#             magnet.current_map_test()

#         finally:
#             # Ensure we always close the connection
#             magnet.disconnect()

if __name__ == "__main__":
    """This test is buggy due to a buggy pulse() method."""
    magnet = MagnetController(resource_name='ASRL5::INSTR')
    magnet.pulse(3.0, 5)
    magnet.pulse(-3.0, 5)
    magnet.disconnect()