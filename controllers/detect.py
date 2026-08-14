"""
Uses pyvisa-py to discover connected instruments and record their VISA
addresses in controllers/.env, where the drivers read them from.

Two things this has to get right, both learned the hard way:

* A VISA resource name says only "this is a serial port", which on a laptop
  also describes every Bluetooth pairing. Picking the first (or last) ASRL
  resource cheerfully wrote a Bluetooth channel into EM_ID. Candidates are
  classified by hardware id instead, and Bluetooth ports are never eligible.
* An instrument that is not found leaves its previous address alone. Writing a
  placeholder instead produced 'VNA_ID=None' in .env, which the drivers then
  passed to open_resource() as the string "None" -- a confusing failure a long
  way from its cause.

Disconnect any other USB-serial devices before running: past the Bluetooth
filter, the match is still by transport rather than by identity.

A LAN instrument cannot be discovered this way at all, and this no longer
pretends otherwise. NI-VISA lists only TCPIP resources already configured in
NI-MAX, and pyvisa-py needs the zeroconf package plus an instrument that
advertises over mDNS. So the VNA's address is configuration, not a discovery
result: put it in params.ini as

    [Devices]
    vna_address = 192.168.1.10        ; or a full TCPIP0::...::INSTR string

and it is verified with *IDN? rather than guessed at. With no address
configured and none enumerated, the VNA is reported as not configured.
"""
import configparser
import os

import pyvisa

CONFIG_FILE = 'params.ini'

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")

#: Hardware-id fragments. Bluetooth virtual ports are never an instrument;
#: a USB-attached adapter (the magnet is an FTDI part) is the thing we want.
BLUETOOTH_MARKER = 'BTHENUM'
USB_MARKERS = ('USB', 'FTDIBUS', 'VID:PID', 'VID_')


def serial_hwids():
    """COM port -> hardware id string, via pyserial."""
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed, so USB serial ports cannot be told "
              "apart from Bluetooth ones. Every ASRL resource is a candidate.")
        return {}
    return {port.device.upper(): (port.hwid or '')
            for port in list_ports.comports()}


def com_name(resource):
    """'ASRL6::INSTR' -> 'COM6'."""
    digits = ''.join(ch for ch in resource.split('::')[0] if ch.isdigit())
    return f"COM{digits}" if digits else ''


def classify(resource, hwids):
    """'usb', 'bluetooth', or 'unknown' for a serial VISA resource."""
    hwid = hwids.get(com_name(resource), '').upper()
    if BLUETOOTH_MARKER in hwid:
        return 'bluetooth'
    if any(marker in hwid for marker in USB_MARKERS):
        return 'usb'
    return 'unknown'


def pick_magnet(matches, hwids):
    """The most plausible magnet among the serial resources, or None."""
    kinds = {resource: classify(resource, hwids) for resource in matches}

    for resource, kind in kinds.items():
        if kind == 'bluetooth':
            print(f"  ignoring {resource} ({com_name(resource)}): Bluetooth "
                  f"serial port, not an instrument.")

    usable = [resource for resource in matches if kinds[resource] != 'bluetooth']
    if not usable:
        return None

    # A USB adapter beats an unidentifiable port; among equals, take the first.
    preferred = [r for r in usable if kinds[r] == 'usb'] or usable
    if len(preferred) > 1:
        listed = ', '.join(f"{r} ({com_name(r)})" for r in preferred)
        print(f"WARNING: {len(preferred)} candidate serial instruments: {listed}.")
        print(f"WARNING: assuming the Electromagnet is {preferred[0]}. "
              f"Disconnect the others and re-run if that is wrong.")
    return preferred[0]


def pick_vna(matches):
    """The first enumerated LAN resource, warning if the choice was ambiguous."""
    if not matches:
        return None
    if len(matches) > 1:
        print(f"WARNING: {len(matches)} TCPIP resources found "
              f"({', '.join(matches)}).")
        print(f"WARNING: assuming the VNA is {matches[0]}.")
    return matches[0]


def configured_vna_address():
    """[Devices]/vna_address from params.ini, normalised to a VISA string."""
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    address = config.get('Devices', 'vna_address', fallback='').strip()
    if not address:
        return None
    if '::' in address:
        return address                       # already a VISA resource string
    return f"TCPIP0::{address}::inst0::INSTR"


def verify_vna(resource):
    """
    Opens `resource` and asks *IDN?. Returns the identity string, or None with
    the reason printed -- a configured address that does not answer is worth
    saying out loud rather than writing to .env and failing later mid-sweep.
    """
    print(f"Checking configured VNA address {resource} ...")
    rm = pyvisa.ResourceManager('@py')
    try:
        inst = rm.open_resource(resource, open_timeout=5000)
        inst.timeout = 5000
        inst.read_termination = '\n'
        inst.write_termination = '\n'
        try:
            identity = inst.query('*IDN?').strip()
        finally:
            inst.close()
        print(f"  responded: {identity}")
        return identity
    except Exception as exc:
        print(f"  no response: {type(exc).__name__}: {exc}")
        return None
    finally:
        rm.close()


def read_env(path):
    """Existing KEY=VALUE pairs, so a partial detection keeps the rest."""
    values = {}
    if not os.path.exists(path):
        return values
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            value = value.strip()
            # 'None' is the placeholder older versions wrote; treat it as unset
            # so a real detection can replace it.
            if value and value != 'None':
                values[key.strip()] = value
    return values


def write_env(path, values):
    with open(path, 'w') as handle:
        for key in ('VNA_ID', 'EM_ID'):
            if values.get(key):
                handle.write(f"{key}={values[key]}\n")


rm = pyvisa.ResourceManager('@py')
instruments = rm.list_resources()
hwids = serial_hwids()

print(f"VISA resources visible: {', '.join(instruments) if instruments else 'none'}")

previous = read_env(ENV_PATH)
found = {}

magnet = pick_magnet([i for i in instruments if 'ASRL' in i], hwids)
if magnet:
    found['EM_ID'] = magnet
    print(f"Found Electromagnet at {magnet} ({com_name(magnet)})")

# The VNA: a configured address wins, because enumeration cannot see a LAN
# instrument that has not been registered with the VISA backend already.
configured = configured_vna_address()
if configured:
    if verify_vna(configured):
        found['VNA_ID'] = configured
        print(f"Found VNA at {configured}")
    else:
        print(f"The configured VNA address {configured} did not answer *IDN?. "
              f"Not writing it to .env.")
else:
    vna = pick_vna([i for i in instruments if 'TCPIP' in i])
    if vna:
        found['VNA_ID'] = vna
        print(f"Found VNA at {vna}")
    else:
        print("No VNA address configured. A LAN instrument cannot be "
              "discovered by enumeration -- set [Devices]/vna_address in "
              "params.ini to its IP.")

if not found:
    print("No instruments found. Nothing written to .env.")
    if not any('ASRL' in instrument for instrument in instruments):
        print("No serial resources at all: pyvisa-py needs pyserial to "
              "enumerate them. Check it is installed in this environment.")
    raise SystemExit(1)

for key, label in (('EM_ID', 'Electromagnet'), ('VNA_ID', 'VNA')):
    if key not in found:
        if previous.get(key):
            print(f"{label} not found; keeping the previous address "
                  f"{previous[key]}.")
        else:
            print(f"{label} not found, and no previous address is on record. "
                  f"Anything needing it will fail until it is detected.")

merged = dict(previous)
merged.update(found)
write_env(ENV_PATH, merged)

print(f"Wrote {ENV_PATH}")
