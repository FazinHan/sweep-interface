"""
Read-only probe of the EM7000S serial link.

The question: does the EM3000S command set carry over? If the READY ping draws
a reply, the link settings and command framing are right, which settles most of
the top of the control-signal block in EM7000S.py.

NOTE -- this magnet's built-in gaussmeter is known to be broken. The field
query stages below are therefore NOT a test of the field reading; they are a
test of whether the opcode is *understood*. The useful distinction:

    three bytes come back, values junk/zero -> opcode understood, sensor dead
    nothing comes back at all               -> opcode probably not understood

So do not read a failed query as "the command set is wrong". Only the READY
ping in stage 1 is decisive here.

SAFETY -- this sends only opcodes that cannot raise the magnet current:

    0x64  ready ping   -- EM3000S answers with one byte
    0x2B  stop         -- de-energises the magnet; answers with ACK 0x12
    0x0A  field query  -- three bytes come back, each echoed to the device

The opcodes that assemble a set-current sequence -- 0x1E (start), 0x2C (set
value) and the four payload bytes -- appear nowhere in this file. The only
bytes ever written back are echoes of bytes the device itself just sent, which
is what its query protocol expects.

Everything sent and received is logged as hex, with timings.

Usage (from the repo root, with the vendor software CLOSED):
    python probe_em7000s.py [--resource ASRL6::INSTR] [--baud 19200]
"""
import argparse
import sys
import time

import pyvisa

READY = 0x64
STOP = 0x2B
QUERY_FIELD = 0x0A
ACK = 0x12

# EM3000S field decode, for checking whether a reply looks like a real reading.
FIELD_SCALE = 10.0
FIELD_SIGN_NEGATIVE = 0x01

SAFE_OPCODES = {READY: 'READY ping', STOP: 'STOP / de-energise',
                QUERY_FIELD: 'QUERY field'}


class Probe:
    def __init__(self, resource, baud):
        self.resource = resource
        self.baud = baud
        self.rm = pyvisa.ResourceManager()   # NI-VISA, as the drivers use
        self.inst = None

    def open(self):
        print(f"Opening {self.resource} at {self.baud} baud, 8-N-1 ...")
        self.inst = self.rm.open_resource(self.resource)
        self.inst.baud_rate = self.baud
        self.inst.data_bits = 8
        self.inst.parity = pyvisa.constants.Parity.none
        self.inst.stop_bits = pyvisa.constants.StopBits.one
        self.inst.write_termination = None
        self.inst.read_termination = None
        self.inst.timeout = 2000
        self.inst.clear()
        print("Port open.\n")

    def close(self):
        if self.inst is not None:
            self.inst.close()
        self.rm.close()

    # -- primitives ---------------------------------------------------------
    def send(self, byte, note=""):
        if byte not in SAFE_OPCODES:
            raise AssertionError(
                f"refusing to send 0x{byte:02X}: not a read-only opcode"
            )
        label = SAFE_OPCODES[byte]
        print(f"  SENT 0x{byte:02X}  ({label}){' ' + note if note else ''}")
        self.inst.write_raw(bytes([byte]))

    def echo(self, byte):
        """
        Echo a byte the device just sent; its query protocol expects this.

        Refuses to echo an opcode that could begin a set sequence. The echoed
        value is whatever the device chose to send, so in principle it could be
        0x1E or 0x2C; skipping the echo may stall the handshake, but a stalled
        probe is a cheaper failure than an unintended write to a magnet.
        """
        if byte in (0x1E, 0x2C):
            print(f"  ECHO SKIPPED 0x{byte:02X} -- that opcode could begin a "
                  f"set sequence. Handshake may stall; this is deliberate.")
            return
        print(f"  ECHO 0x{byte:02X}  (echoing back what the device sent)")
        self.inst.write_raw(bytes([byte]))

    def read(self, note=""):
        t0 = time.monotonic()
        try:
            value = self.inst.read_bytes(1)[0]
        except pyvisa.errors.VisaIOError:
            print(f"  RECV --    (timeout after {time.monotonic()-t0:.2f}s)"
                  f"{' ' + note if note else ''}")
            return None
        print(f"  RECV 0x{value:02X}  ({time.monotonic()-t0:.3f}s)"
              f"{' ' + note if note else ''}")
        return value

    def drain(self):
        """Read anything already sitting in the buffer."""
        self.inst.timeout = 300
        leftovers = []
        while True:
            try:
                leftovers.append(self.inst.read_bytes(1)[0])
            except pyvisa.errors.VisaIOError:
                break
        self.inst.timeout = 2000
        if leftovers:
            print("  buffer held: " + " ".join(f"0x{b:02X}" for b in leftovers))
        else:
            print("  buffer empty")
        return leftovers

    # -- stages -------------------------------------------------------------
    def stage_0_drain(self):
        print("STAGE 0 -- drain anything unsolicited")
        self.drain()
        print()

    def stage_1_ready(self):
        print("STAGE 1 -- READY ping (0x64) x3")
        print("  EM3000S: answers with exactly one byte each time.")
        replies = []
        for i in range(3):
            self.send(READY, note=f"[{i+1}/3]")
            replies.append(self.read())
        print()
        return replies

    def stage_2_bare_query(self):
        print("STAGE 2 -- bare field query (0x0A), no stop first")
        print("  EM3000S: three bytes (mag-high, mag-low, sign), each echoed.")
        self.send(QUERY_FIELD)
        received = []
        for _ in range(3):
            value = self.read()
            if value is None:
                break
            received.append(value)
            self.echo(value)
        print()
        return received

    def stage_3_full_query(self):
        print("STAGE 3 -- EM3000S query_field() sequence verbatim")
        print("  READY, STOP (expect ACK 0x12), then QUERY + 3 echoed bytes.")
        self.send(READY)
        self.read()

        self.send(STOP)
        ack = self.read(note="<- expecting 0x12 ACK")
        if ack != ACK:
            print(f"  NOTE: no 0x12 ACK (got {ack if ack is None else hex(ack)}).")

        self.send(QUERY_FIELD)
        received = []
        for _ in range(3):
            value = self.read()
            if value is None:
                break
            received.append(value)
            self.echo(value)
        print()
        return received


def decode_field(received):
    if len(received) != 3:
        return None
    b1, b2, b3 = received
    magnitude = ((b1 << 8) | b2) / FIELD_SCALE
    return -magnitude if b3 == FIELD_SIGN_NEGATIVE else magnitude


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource', default='ASRL6::INSTR')
    parser.add_argument('--baud', type=int, default=19200)
    args = parser.parse_args()

    print(__doc__.split('Usage')[0])
    print("=" * 70)

    probe = Probe(args.resource, args.baud)
    try:
        probe.open()
    except Exception as exc:
        print(f"Could not open {args.resource}: {exc}\n")
        print("If this says access denied, the vendor software still has the "
              "port open. Close it completely and re-run.")
        return 1

    try:
        probe.stage_0_drain()
        ready = probe.stage_1_ready()
        bare = probe.stage_2_bare_query()
        full = probe.stage_3_full_query()
    finally:
        probe.close()
        print("Port closed.")

    print("=" * 70)
    print("SUMMARY")
    answered = [r for r in ready if r is not None]
    print(f"  READY ping      : {len(answered)}/3 answered"
          + (f" -> {[hex(r) for r in answered]}" if answered else ""))
    print(f"  bare QUERY      : {len(bare)}/3 bytes"
          + (f" -> {[hex(b) for b in bare]}" if bare else ""))
    print(f"  full QUERY seq  : {len(full)}/3 bytes"
          + (f" -> {[hex(b) for b in full]}" if full else ""))

    for label, data in (("bare", bare), ("full", full)):
        field = decode_field(data)
        if field is not None:
            print(f"  {label} decoded as EM3000S would: {field:g} mT "
                  f"(value is meaningless - gaussmeter is known broken)")

    print("\nREADING THIS")
    if not answered:
        print("  The READY ping drew nothing. That is the decisive result:")
        print("  either the command set differs, or the baud rate does.")
        print("  Next step is a baud sweep, then a USB capture.")
    else:
        print("  The READY ping was answered, so the link settings and command")
        print("  framing carry over from the EM3000S. That pins down the top")
        print("  of the control-signal block.")
        if len(bare) == 3 or len(full) == 3:
            print("  The field query opcode is understood too (three bytes came")
            print("  back); the values are junk because the sensor is dead.")
        else:
            print("  The field query drew no reply. With a broken gaussmeter")
            print("  that is expected and says little about the opcode.")

    print("\n  Either way, the current->payload mapping cannot come from here:")
    print("  with no working gaussmeter there is no feedback path. That needs a")
    print("  USB capture of the vendor app setting known currents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
