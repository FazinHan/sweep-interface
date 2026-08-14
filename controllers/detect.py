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

A LAN instrument is searched for three ways, in order, because VISA
enumeration alone will not find one: NI-VISA lists only TCPIP resources
already registered in NI-MAX, and pyvisa-py needs the zeroconf package.

    1. [Devices]/vna_address in params.ini, if set -- a bare IP or a full
       TCPIP0::...::INSTR string. An explicit answer always wins.
    2. VISA enumeration, for a resource the backend already knows about.
    3. mDNS, which is how an LXI instrument announces itself.

Whatever turns up is confirmed with *IDN? before being written to .env, so a
stale or wrong address fails here rather than part-way through a sweep.

mDNS earns its place precisely because it is link-local multicast: an
instrument whose IP is on a different subnet from ours still answers it, even
though no unicast connection to it can succeed. That is the difference between
"there is no VNA" and "the VNA is at 10.40.64.225 and the addressing is
wrong", so a device that announces itself but will not answer *IDN? is
reported with that diagnosis rather than silently dropped.
"""
import configparser
import os

import pyvisa

from find_lxi import discover

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


def verify_vna(resource, source='configured'):
    """
    Opens `resource` and asks *IDN?. Returns the identity string, or None with
    the reason printed -- an address that does not answer is worth saying out
    loud rather than writing to .env and failing later mid-sweep.
    """
    print(f"Checking {source} VNA address {resource} ...")
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

def resolve_vna(enumerated):
    """
    Configured address, then VISA enumeration, then mDNS. Returns a verified
    resource string, or None (having explained what it saw).
    """
    configured = configured_vna_address()
    if configured:
        if verify_vna(configured):
            return configured
        print(f"  the configured address {configured} did not answer; "
              f"trying the other routes.")

    enumerated_vna = pick_vna(enumerated)
    if enumerated_vna and verify_vna(enumerated_vna, source='enumerated'):
        return enumerated_vna

    print("Asking the network for LXI instruments (mDNS)...")
    hosts = discover(verbose=False)
    if not hosts:
        print("  nothing answered.")
        return None

    for ip in sorted(hosts):
        names = ', '.join(sorted(hosts[ip]))
        print(f"  {ip} advertises: {names}")

    for ip in sorted(hosts):
        resource = f"TCPIP0::{ip}::inst0::INSTR"
        if verify_vna(resource, source='discovered'):
            return resource

    # Reached only when something announced itself but will not talk. Say why.
    print()
    print("An instrument announced itself but would not answer *IDN?.")
    print("mDNS is link-local multicast, so it gets through even when unicast")
    print("cannot -- which usually means the instrument's IP is on a different")
    print("subnet from the interface it is cabled to. Compare its address with")
    print("this machine's, and give that interface an address on the same")
    print("subnet (or set the instrument to DHCP). A VPN can also swallow")
    print("local traffic; disconnect it and try again.")
    return None


vna = resolve_vna([i for i in instruments if 'TCPIP' in i])
if vna:
    found['VNA_ID'] = vna
    print(f"Found VNA at {vna}")

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
