#!/usr/bin/env python3
"""reg_test.py -- host-side check for Step 0.5 (FIFO and register protocol).

Talks to the Go Board's register interface over its UART. Copy to
host/reg_test.py.

    python3 host/reg_test.py /dev/ttyUSB1               # Linux
    python3 host/reg_test.py COM5                       # Windows
    python3 host/reg_test.py /dev/ttyUSB1 --leds 0x9    # end with LEDs 1 and 4 lit

The protocol (single bytes, as the FPGA sees them):

    WRITE:  'W' (0x57), addr, data     no reply
    READ:   'R' (0x52), addr           reply: one byte, the register's value

There are 16 registers, 0x00 to 0x0F. Register 0 drives the LEDs (bit 0 is
LED 1) and register 1 the 7-segment display. Writes to 0x10 and up are
ignored; reads of them return 0x00.

The test:
  1. walks a light across the four LEDs, reading register 0 back each step,
  2. writes random values to all 16 registers, many times over, and reads
     every one back (the writes and reads are sent back-to-back, so the
     FPGA's FIFO and parser run at full speed),
  3. checks that address 0x10 reads as 00 and that writing it does not
     change register 0,
  4. leaves register 0 = --leds (default 0x5: LEDs 1 and 3) and register 1 =
     --display (default 0x5A), and reads both back.

Prints exactly one line starting with PASS or FAIL and exits 0 on PASS, 1 on
FAIL. Needs pyserial (pip install pyserial).
"""

import argparse
import random
import sys
import time

try:
    import serial
except ImportError:
    print("FAIL reg_test: pyserial is not installed (pip install pyserial)")
    sys.exit(1)

OP_WRITE = 0x57  # 'W'
OP_READ = 0x52   # 'R'
NUM_REGS = 16


class RegError(Exception):
    pass


def write_regs(port, pairs):
    """Send WRITE packets for (addr, value) pairs, back-to-back."""
    port.write(bytes(b for a, v in pairs for b in (OP_WRITE, a & 0xFF, v & 0xFF)))


def read_regs(port, addrs):
    """Send READ packets for addrs back-to-back; return the replies."""
    addrs = list(addrs)
    port.write(bytes(b for a in addrs for b in (OP_READ, a & 0xFF)))
    got = port.read(len(addrs))
    if len(got) != len(addrs):
        raise RegError(f"sent {len(addrs)} READ(s), got {len(got)} reply byte(s) back")
    return list(got)


def write_reg(port, addr, value):
    write_regs(port, [(addr, value)])


def read_reg(port, addr):
    return read_regs(port, [addr])[0]


def expect(what, got, want):
    if got != want:
        raise RegError(f"{what}: read {got:02X}, expected {want:02X}")


def run(port, rng, rounds, leds, display):
    # 1. A light walking across the LEDs.
    for v in (0x1, 0x2, 0x4, 0x8, 0x4, 0x2, 0x1, 0x0):
        write_reg(port, 0, v)
        expect("register 0 during the LED walk", read_reg(port, 0), v)
        time.sleep(0.15)

    # 2. Random values in every register, read back.
    model = [0] * NUM_REGS
    for r in range(rounds):
        order = list(range(NUM_REGS))
        rng.shuffle(order)
        pairs = [(a, rng.randrange(256)) for a in order]
        write_regs(port, pairs)
        for a, v in pairs:
            model[a] = v
        got = read_regs(port, range(NUM_REGS))
        for a in range(NUM_REGS):
            expect(f"round {r}, register {a:#04x}", got[a], model[a])

    # 3. Out-of-range addresses.
    expect("address 0x10", read_reg(port, 0x10), 0x00)
    write_reg(port, 0x10, model[0] ^ 0xFF)
    expect("register 0 after writing 0x10", read_reg(port, 0), model[0])
    expect("address 0x10 after writing it", read_reg(port, 0x10), 0x00)

    # 4. Leave something visible.
    write_regs(port, [(0, leds), (1, display)])
    expect("register 0 (LEDs)", read_reg(port, 0), leds)
    expect("register 1 (display)", read_reg(port, 1), display)
    return rounds * NUM_REGS


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the FPGA register interface over UART.")
    ap.add_argument("port", help="serial port, e.g. /dev/ttyUSB1 or COM5")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (default 115200)")
    ap.add_argument("--rounds", type=int, default=50, help="write/read-back rounds (default 50)")
    ap.add_argument("--leds", type=lambda s: int(s, 0), default=0x5,
                    help="final value of register 0 (default 0x5)")
    ap.add_argument("--display", type=lambda s: int(s, 0), default=0x5A,
                    help="final value of register 1 (default 0x5A)")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: random)")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for replies")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1 << 32)
    rng = random.Random(seed)

    try:
        port = serial.Serial(args.port, args.baud, bytesize=8, parity="N",
                             stopbits=1, timeout=args.timeout)
    except serial.SerialException as e:
        print(f"FAIL reg_test: cannot open {args.port}: {e}")
        return 1

    with port:
        time.sleep(0.1)
        port.reset_input_buffer()
        try:
            n = run(port, rng, args.rounds, args.leds & 0xFF, args.display & 0xFF)
        except RegError as e:
            print(f"FAIL reg_test: {e} (seed {seed})")
            return 1

        # Nothing extra may arrive: WRITEs have no reply.
        port.timeout = 0.2
        extra = port.read(16)
        if extra:
            print(f"FAIL reg_test: {len(extra)} unexpected extra byte(s): {extra.hex(' ')}")
            return 1

    lit = [str(i + 1) for i in range(4) if (args.leds >> i) & 1]
    print(f"PASS reg_test: {n} register writes read back (seed {seed}); "
          f"LEDs lit: {', '.join(lit) if lit else 'none'}; display shows {args.display & 0xFF:02X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
