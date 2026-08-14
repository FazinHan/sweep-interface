"""
Finds LAN instruments without enumerating or port-scanning.

pyvisa cannot list a TCPIP instrument it has never been told about: NI-VISA
only reports resources configured in NI-MAX, and pyvisa-py needs zeroconf.
LXI instruments (the ZNLE is one) announce themselves over mDNS instead, so
this asks the standard multicast question directly and listens.

Sends PTR queries for the service types an LXI VNA advertises, on every local
interface, and reports any A record that comes back.
"""
import socket
import struct
import sys
import time

MDNS_ADDR = '224.0.0.251'
MDNS_PORT = 5353

SERVICES = [
    '_lxi._tcp.local',
    '_vxi-11._tcp.local',
    '_scpi-raw._tcp.local',
    '_scpi-telnet._tcp.local',
    '_hislip._tcp.local',
    '_http._tcp.local',
]


def encode_name(name):
    out = b''
    for label in name.split('.'):
        if label:
            out += bytes([len(label)]) + label.encode()
    return out + b'\x00'


def build_query(name):
    header = struct.pack('>HHHHHH', 0, 0, 1, 0, 0, 0)   # standard query, 1 q
    return header + encode_name(name) + struct.pack('>HH', 12, 1)  # PTR, IN


def read_name(data, offset):
    """Reads a DNS name, following compression pointers."""
    labels = []
    jumped = False
    original = offset
    while True:
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:                       # compression pointer
            pointer = struct.unpack('>H', data[offset:offset + 2])[0] & 0x3FFF
            if not jumped:
                original = offset + 2
            offset = pointer
            jumped = True
            continue
        labels.append(data[offset + 1:offset + 1 + length].decode(errors='replace'))
        offset += 1 + length
    return '.'.join(labels), (original if jumped else offset)


def parse_records(data):
    """Yields (name, ipv4) for every A record in a response."""
    try:
        _id, _flags, qd, an, ns, ar = struct.unpack('>HHHHHH', data[:12])
    except struct.error:
        return
    offset = 12
    for _ in range(qd):
        _name, offset = read_name(data, offset)
        offset += 4
    for _ in range(an + ns + ar):
        if offset >= len(data):
            return
        name, offset = read_name(data, offset)
        if offset + 10 > len(data):
            return
        rtype, _cls, _ttl, rdlen = struct.unpack('>HHIH', data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        offset += rdlen
        if rtype == 1 and rdlen == 4:                   # A record
            yield name, socket.inet_ntoa(rdata)


def local_ipv4s():
    addresses = set()
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        addresses.add(info[4][0])
    addresses.add('0.0.0.0')
    return sorted(addresses)


def discover(timeout=2.5, verbose=True):
    """
    Returns {ipv4: {advertised names}} for hosts answering LXI/SCPI mDNS.

    Queries from every local interface: an instrument on a cable whose subnet
    does not match ours still answers, because mDNS is link-local multicast.
    That is exactly the case worth catching -- it is how a misaddressed
    instrument makes itself known when nothing else about it works.
    """
    found = {}
    for local in local_ipv4s():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.bind((local, 0))
            sock.settimeout(0.4)
        except OSError as exc:
            if verbose:
                print(f"  {local:<16} cannot bind ({exc})")
            continue

        if verbose:
            print(f"  querying from {local} ...")
        try:
            for service in SERVICES:
                try:
                    sock.sendto(build_query(service), (MDNS_ADDR, MDNS_PORT))
                except OSError:
                    pass

            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                for name, ip in parse_records(data):
                    if ip.startswith('127.'):
                        continue
                    found.setdefault(ip, set()).add(name)
        finally:
            sock.close()

    return found


def main():
    found = discover()

    print()
    if not found:
        print("No mDNS responses. The instrument either does not advertise, or")
        print("cannot reach this machine at the IP layer at all.")
        return 1

    print("Responding hosts:")
    for ip in sorted(found):
        names = ', '.join(sorted(found[ip]))
        print(f"  {ip:<16} {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
