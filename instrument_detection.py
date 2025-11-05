import pyvisa

"""
Uses pyvisa-py to discover connected instruments.
Disconnect all instruments except the VNA
and electromagnet before running.
"""

rm = pyvisa.ResourceManager('@py')

instruments = rm.list_resources()

for instr in instruments:
    if "ASRL" in instr:
        EM_ID = instr
        print(f"Found Electromagnet at {EM_ID}")
    elif "TCPIP" in instr:
        VNA_ID = instr
        print(f"Found VNA at {VNA_ID}")

with open(".env", 'w') as f:
    f.write(f"VNA_ID={VNA_ID}\n")
    f.write(f"EM_ID={EM_ID}\n")