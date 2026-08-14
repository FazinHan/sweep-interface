"""
Turns the raw byte transcript into labelled sequences.

The rule the capture makes obvious:
    host sends a COMMAND  -> device answers with exactly 2 x command
    device sends DATA     -> host echoes the same byte straight back
    host sends DATA       -> device echoes the same byte straight back

So each OUT can be classified by what came back, and a run of exchanges with no
long pause is one logical operation. Gaps are where the operator was clicking.
"""
import re
import sys

GAP = 0.30   # seconds; anything longer starts a new logical sequence

LINE = re.compile(r'\s*([\d.]+)s\s+(OUT|IN )\s+([0-9A-F ]+)')


def load(path):
    events = []
    started = False
    for line in open(path):
        if line.startswith('     ') or 'OUT' in line or 'IN ' in line:
            match = LINE.match(line)
            if match:
                started = True
                timestamp = float(match.group(1))
                direction = match.group(2).strip()
                data = [int(b, 16) for b in match.group(3).split()]
                events.append((timestamp, direction, data))
    if not started:
        raise SystemExit("no transcript lines found")
    return events


def classify(events):
    """Pairs each OUT with the IN that followed, and labels it."""
    labelled = []
    index = 0
    while index < len(events):
        timestamp, direction, data = events[index]
        if direction == 'OUT' and len(data) == 1:
            byte = data[0]
            reply = None
            if index + 1 < len(events) and events[index + 1][1] == 'IN':
                reply = events[index + 1][2][0]
            if reply is not None and reply == (byte * 2) & 0xFF and byte != 0:
                kind = 'CMD'
            elif reply is not None and reply == byte:
                kind = 'echo'
            elif reply is None:
                kind = 'sent'
            else:
                kind = '?'
            labelled.append((timestamp, 'OUT', byte, reply, kind))
            index += 2 if reply is not None else 1
        elif direction == 'IN' and len(data) == 1:
            labelled.append((timestamp, 'IN', data[0], None, 'devdata'))
            index += 1
        else:
            index += 1
    return labelled


def main(path):
    events = load(path)
    labelled = classify(events)

    print(f"{len(events)} transfers -> {len(labelled)} exchanges\n")

    # command vocabulary
    commands = {}
    for _t, direction, byte, reply, kind in labelled:
        if direction == 'OUT' and kind == 'CMD':
            commands.setdefault(byte, 0)
            commands[byte] += 1
    print("COMMAND OPCODES SEEN (reply was exactly 2x):")
    for byte in sorted(commands):
        print(f"  0x{byte:02X} -> 0x{(byte*2)&0xFF:02X}   x{commands[byte]}")
    print()

    print("=" * 72)
    print("SEQUENCES (split on gaps > %.2fs)" % GAP)
    print("=" * 72)
    previous = None
    sequence = []
    sequences = []
    for entry in labelled:
        if previous is not None and entry[0] - previous > GAP:
            sequences.append(sequence)
            sequence = []
        sequence.append(entry)
        previous = entry[0]
    if sequence:
        sequences.append(sequence)

    for number, sequence in enumerate(sequences, 1):
        start = sequence[0][0]
        span = sequence[-1][0] - start
        print(f"\n--- sequence {number}  t={start:.3f}s  ({span:.3f}s, "
              f"{len(sequence)} exchanges) ---")
        parts = []
        for _t, direction, byte, reply, kind in sequence:
            if direction == 'OUT' and kind == 'CMD':
                parts.append(f"CMD:{byte:02X}")
            elif direction == 'OUT' and kind == 'echo':
                parts.append(f"out:{byte:02X}")
            elif direction == 'OUT':
                parts.append(f"out?:{byte:02X}")
            else:
                parts.append(f"[dev:{byte:02X}]")
        for chunk in range(0, len(parts), 12):
            print("    " + "  ".join(parts[chunk:chunk + 12]))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: python controllers/analyse_capture.py <decoded.txt>\n"
            "  where decoded.txt is the output of decode_capture.py"
        )
    main(sys.argv[1])
