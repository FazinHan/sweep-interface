"""
Decodes a USBPcap capture of the vendor software driving the EM7000S.

Two layers to peel:

* USBPcap pseudo-header (27 bytes, 28 for control which appends a stage byte).
  headerLen at offset 0 says where the payload starts, so we trust that rather
  than a hard-coded size.
* FTDI framing. Every IN (device->host) bulk transfer is prefixed with two
  modem/line status bytes even when no data followed, so a poll that returned
  nothing shows up as a 2-byte packet and must be dropped.

Prints the FTDI control requests (which settle baud and framing) and then a
chronological transcript of the actual byte exchange.
"""
import struct
import sys
from collections import OrderedDict

# FTDI vendor requests (bmRequestType 0x40)
FTDI_REQUESTS = {
    0x00: 'RESET',
    0x01: 'SET_MODEM_CTRL',
    0x02: 'SET_FLOW_CTRL',
    0x03: 'SET_BAUD_RATE',
    0x04: 'SET_DATA',
    0x05: 'GET_MODEM_STATUS',
    0x06: 'SET_EVENT_CHAR',
    0x07: 'SET_ERROR_CHAR',
    0x09: 'SET_LATENCY_TIMER',
    0x0A: 'GET_LATENCY_TIMER',
}

PARITY = {0: 'none', 1: 'odd', 2: 'even', 3: 'mark', 4: 'space'}
STOP_BITS = {0: '1', 1: '1.5', 2: '2'}


def read_pcap(path):
    """Yields (timestamp, payload_bytes) for each record."""
    with open(path, 'rb') as handle:
        header = handle.read(24)
        magic = struct.unpack('<I', header[:4])[0]
        if magic == 0xa1b2c3d4:
            endian, scale = '<', 1_000_000
        elif magic == 0xd4c3b2a1:
            endian, scale = '>', 1_000_000
        elif magic == 0xa1b23c4d:
            endian, scale = '<', 1_000_000_000
        else:
            raise SystemExit(f"not a pcap file (magic {magic:#x})")
        link = struct.unpack(endian + 'I', header[20:24])[0]
        print(f"pcap linktype {link} "
              f"({'USBPCAP' if link == 249 else 'unexpected'})\n")

        while True:
            record = handle.read(16)
            if len(record) < 16:
                return
            ts_sec, ts_frac, incl, _orig = struct.unpack(endian + 'IIII', record)
            data = handle.read(incl)
            if len(data) < incl:
                return
            yield ts_sec + ts_frac / scale, data


def parse(packet):
    """USBPcap pseudo-header -> dict, or None if it is too short."""
    if len(packet) < 27:
        return None
    header_len = struct.unpack('<H', packet[0:2])[0]
    irp_id = struct.unpack('<Q', packet[2:10])[0]
    function = struct.unpack('<H', packet[14:16])[0]
    info = packet[16]
    endpoint = packet[21]
    transfer = packet[22]
    data_length = struct.unpack('<I', packet[23:27])[0]
    stage = packet[27] if header_len >= 28 and len(packet) > 27 else None
    return {
        'irp': irp_id, 'function': function, 'info': info,
        'endpoint': endpoint, 'transfer': transfer,
        'data_length': data_length, 'stage': stage,
        'payload': packet[header_len:],
        # info bit 0: 0 = host->device submission, 1 = completion coming back
        'is_completion': bool(info & 0x01),
    }


def decode_baud(value, index):
    """
    FTDI divisor -> baud. FT-X parts run from a 48 MHz clock; the low 14 bits
    are the integer divisor and the top 3 bits select an eighths fraction.
    """
    divisor = value | ((index & 0x0100) << 8)
    integer = divisor & 0x3FFF
    fraction_code = (divisor >> 14) & 0x07
    fraction = [0, 0.5, 0.25, 0.125, 0.375, 0.625, 0.75, 0.875][fraction_code]
    if integer == 0:
        return 3_000_000
    if integer == 1 and fraction == 0:
        return 2_000_000
    return 48_000_000 / (16 * (integer + fraction))


def main(path):
    controls = []
    transcript = []
    seen = OrderedDict()

    for timestamp, packet in read_pcap(path):
        info = parse(packet)
        if info is None:
            continue

        if info['transfer'] == 0x02:                      # control
            if info['stage'] == 0 and len(info['payload']) >= 8:
                setup = info['payload'][:8]
                controls.append((timestamp, setup))
            continue

        if info['transfer'] != 0x03:                      # bulk only
            continue

        payload = info['payload']
        inbound = bool(info['endpoint'] & 0x80)

        if inbound:
            if not info['is_completion'] or len(payload) <= 2:
                continue                                  # status-only poll
            data = payload[2:]                            # strip status bytes
        else:
            if info['is_completion'] or not payload:
                continue                                  # OUT data is on submit
            data = payload

        # No de-duplication: submit/complete are already separated above, and
        # dropping "repeats" would silently eat a payload that legitimately
        # sends the same byte twice -- exactly the case being decoded here.
        transcript.append((timestamp, 'IN ' if inbound else 'OUT', bytes(data)))

    print("=" * 72)
    print("FTDI CONTROL REQUESTS")
    print("=" * 72)
    for timestamp, setup in controls:
        bm, request, value, index, length = struct.unpack('<BBHHH', setup)
        if bm != 0x40:
            continue
        name = FTDI_REQUESTS.get(request, f'UNKNOWN(0x{request:02X})')
        extra = ''
        if request == 0x03:
            extra = f"  -> {decode_baud(value, index):.0f} baud"
        elif request == 0x04:
            bits = value & 0x0F
            parity = PARITY.get((value >> 8) & 0x07, '?')
            stop = STOP_BITS.get((value >> 11) & 0x07, '?')
            extra = f"  -> {bits} data bits, parity {parity}, {stop} stop"
        elif request == 0x01:
            dtr = 'ON' if value & 0x0001 else 'off'
            rts = 'ON' if value & 0x0002 else 'off'
            extra = f"  -> DTR {dtr}, RTS {rts}"
        elif request == 0x09:
            extra = f"  -> {value} ms"
        print(f"  {name:<18} wValue=0x{value:04X} wIndex=0x{index:04X}{extra}")

    print()
    print("=" * 72)
    print(f"BYTE TRANSCRIPT  ({len(transcript)} transfers with data)")
    print("=" * 72)
    if not transcript:
        print("  nothing captured")
        return

    start = transcript[0][0]
    for timestamp, direction, data in transcript:
        hexed = ' '.join(f'{b:02X}' for b in data)
        print(f"  {timestamp - start:8.3f}s  {direction}  {hexed}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else 'em7000s.pcap')
