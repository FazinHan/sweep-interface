import pyvisa, os

"""
Uses pyvisa-py to discover connected instruments.
Disconnect all instruments except the VNA
and electromagnet before running.
"""

rm = pyvisa.ResourceManager('@py')

instruments = rm.list_resources()

ids = {'EM_ID': None, 'VNA_ID': None}

for instr in instruments:
    if "ASRL" in instr:
        ids['EM_ID'] = instr
        print(f"Found Electromagnet at {ids['EM_ID']}")
    elif "TCPIP" in instr:
        ids['VNA_ID'] = instr
        print(f"Found VNA at {ids['VNA_ID']}")

if not ids['EM_ID'] and not ids['VNA_ID']:
    print(f"No instruments found. Exiting.")
    exit()

with open(os.path.join(os.path.dirname(__file__), ".env"), 'w') as f:
    f.write(f"VNA_ID={ids.get('VNA_ID')}\n")
    f.write(f"EM_ID={ids.get('EM_ID')}\n")