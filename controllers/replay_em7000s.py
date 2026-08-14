"""
Replays the EM7000S driver against the byte exchanges captured from the vendor
software, without touching hardware.

A fake VISA instrument stands in for the magnet: it answers every byte exactly
as the real device did (doubling commands, echoing payload bytes, 0x19 for
commit, echoing 0x27 for stop) and records what the driver sent. The recorded
stream is then compared byte-for-byte with the sequences in the USB captures.

This is the regression test for the control signals: if someone edits an
opcode, the payload layout or the current table, the driver stops reproducing
the vendor application's bytes and this fails.

    python controllers/replay_em7000s.py      # exits non-zero on failure
"""
import sys

from EM7000S import MagnetController, expected_reply


class FakeInst:
    """
    Answers exactly as the captured EM7000S does, tracking the same context
    the device evidently does: the five bytes after a channel-select are
    payload and get echoed; everything else is a command and gets doubled,
    with the commit (0x19) and stop (echo) exceptions. 0x0A demonstrates why
    context matters -- as a payload byte the capture shows it echoed, as the
    commit it draws 0x19.
    """
    def __init__(self):
        self.sent = []
        self._pending = []
        self._payload_left = 0

    def write_raw(self, data):
        for byte in data:
            self.sent.append(byte)
            if self._payload_left > 0:
                self._payload_left -= 1
                self._pending.append(byte)               # payload: echo
            else:
                if 0x28 <= byte <= 0x2B:                  # channel select
                    self._payload_left = 5
                self._pending.append(expected_reply(byte))

    def read_bytes(self, n):
        assert n == 1
        if not self._pending:
            raise AssertionError("driver read with nothing pending")
        return bytes([self._pending.pop(0)])


def driver(coils):
    m = MagnetController.__new__(MagnetController)   # skip pyvisa in __init__
    m.coils = coils
    m.inst = FakeInst()
    return m


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"      sent    : {' '.join(f'{b:02X}' for b in got)}")
        print(f"      captured: {' '.join(f'{b:02X}' for b in want)}")
    return ok


results = []

# Capture #1, t=0.000 (and capture #2 seq 50): one coil, +1.00 A
m = driver(1)
m.set_current(1.00)
results.append(check(
    "1 coil, +1.00 A  (capture #1 t=0.0, capture #2 t=112.9)",
    m.inst.sent,
    [0x64, 0x1E, 0x28, 0x0A, 0x0A, 0x00, 0x01, 0x00, 0x0A]))

# Capture #2, seq 1: four coils, +3.00 A
m = driver(4)
m.set_current(3.00)
payload = [0x1F, 0x7D, 0x00, 0x01, 0x00]
results.append(check(
    "4 coils, +3.00 A (capture #2 t=0.0)",
    m.inst.sent,
    [0x64, 0x1E, 0x1F, 0x20, 0x21]
    + [0x28] + payload + [0x29] + payload + [0x2A] + payload + [0x2B] + payload
    + [0x0A]))

# Capture #2, seq 36: four coils, -3.00 A (sign byte flips, magnitude same)
m = driver(4)
m.set_current(-3.00)
payload = [0x1F, 0x7D, 0x00, 0x00, 0x00]
results.append(check(
    "4 coils, -3.00 A (capture #2 t=64.6)",
    m.inst.sent,
    [0x64, 0x1E, 0x1F, 0x20, 0x21]
    + [0x28] + payload + [0x29] + payload + [0x2A] + payload + [0x2B] + payload
    + [0x0A]))

# STOP: the single captured byte
m = driver(4)
m.stop()
results.append(check("STOP (4 presses in capture #2)", m.inst.sent, [0x27]))

# +0.25 A: table endpoint, must reproduce capture #2 seq 17 exactly (555)
m = driver(4)
m.set_current(0.25)
payload = [0x02, 0x2B, 0x00, 0x01, 0x00]
results.append(check(
    "4 coils, +0.25 A (capture #2 t=30.4)",
    m.inst.sent,
    [0x64, 0x1E, 0x1F, 0x20, 0x21]
    + [0x28] + payload + [0x29] + payload + [0x2A] + payload + [0x2B] + payload
    + [0x0A]))

# Remaining captured currents through the table: exact counts
from EM7000S import _counts_for_amps
for amps, want in [(0.5, 1198), (2.0, 5301)]:
    got = _counts_for_amps(amps)
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  table at {amps} A -> {got} "
          f"(captured {want})")
    results.append(ok)
print(f"      extrapolation: 4.2 A -> {_counts_for_amps(4.2)} counts, "
      f"0.05 A -> {_counts_for_amps(0.05)} counts")

# Range: 4.2 A allowed, 4.3 refused
m = driver(1)
try:
    m.set_current(4.3)
    print("FAIL  4.3 A was not refused")
    results.append(False)
except AssertionError:
    print("PASS  4.3 A refused (limit 4.2)")
    results.append(True)

# set_field must raise, field queries must return None without touching bytes
m = driver(1)
try:
    m.set_field(100)
    results.append(False); print("FAIL  set_field did not raise")
except NotImplementedError:
    results.append(True); print("PASS  set_field raises (Amps only)")
assert m.query_field() is None
m2 = driver(1)
assert m2.stop_and_query_field() is None and m2.inst.sent == [0x27]
print("PASS  query_field -> None; stop_and_query_field stops and returns None")
results.append(True)

print()
print(f"{sum(bool(r) for r in results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
