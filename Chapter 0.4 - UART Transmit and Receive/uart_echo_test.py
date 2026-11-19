#!/usr/bin/env python3
"""uart_echo_test.py -- host-side check for Step 0.4 (UART transmit and receive).

Sends random bytes to the Go Board's UART echo design and checks that every
one comes back unchanged and in order. Copy to host/uart_echo_test.py.

    python3 host/uart_echo_test.py /dev/ttyUSB1          # Linux
    python3 host/uart_echo_test.py COM5                  # Windows
    python3 host/uart_echo_test.py /dev/ttyUSB1 --count 10000 --chunk 64

The bytes are sent in chunks (64 by default) and each chunk is read back
before the next is sent. Inside a chunk the PC sends back-to-back, which
tests the echo at full speed; between chunks the FPGA catches up, so a tiny
baud-rate difference between the PC's adapter and the FPGA cannot pile up
over 10,000 bytes and overflow the echo's one-byte buffer.

Prints exactly one line starting with PASS or FAIL (plus the last byte sent,
which the 7-segment display should show) and exits 0 on PASS, 1 on FAIL.
Needs pyserial (pip install pyserial).
"""

import argparse
import random
import sys
import time

try:
    import serial
except ImportError:
    print("FAIL uart_echo_test: pyserial is not installed (pip install pyserial)")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check the FPGA UART echo with random bytes.")
    ap.add_argument("port", help="serial port, e.g. /dev/ttyUSB1 or COM5")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (default 115200)")
    ap.add_argument("--count", type=int, default=10000, help="bytes to send (default 10000)")
    ap.add_argument("--chunk", type=int, default=64, help="bytes per chunk (default 64)")
    ap.add_argument("--seed", type=int, default=None, help="random seed (default: random)")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for each chunk")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1 << 32)
    rng = random.Random(seed)
    data = bytes(rng.randrange(256) for _ in range(args.count))

    try:
        port = serial.Serial(args.port, args.baud, bytesize=8, parity="N",
                             stopbits=1, timeout=args.timeout)
    except serial.SerialException as e:
        print(f"FAIL uart_echo_test: cannot open {args.port}: {e}")
        return 1

    with port:
        time.sleep(0.1)
        port.reset_input_buffer()          # drop anything left over from before

        start = time.monotonic()
        for off in range(0, len(data), args.chunk):
            sent = data[off:off + args.chunk]
            port.write(sent)
            got = port.read(len(sent))     # returns early only on timeout
            if got != sent:
                if len(got) < len(sent):
                    what = f"only {len(got)} of {len(sent)} bytes came back"
                else:
                    what = "wrong data came back"
                for i, (a, b) in enumerate(zip(sent, got)):
                    if a != b:
                        what += f"; first mismatch at byte {off + i}: sent {a:02X}, got {b:02X}"
                        break
                print(f"FAIL uart_echo_test: chunk at byte {off}: {what} (seed {seed})")
                return 1
        elapsed = time.monotonic() - start

        # Nothing extra may arrive afterwards.
        port.timeout = 0.2
        extra = port.read(16)
        if extra:
            print(f"FAIL uart_echo_test: {len(extra)} unexpected extra byte(s) after the test: {extra.hex(' ')}")
            return 1

    last = data[-1] if data else 0
    print(f"PASS uart_echo_test: {len(data)} random bytes echoed in {elapsed:.1f} s "
          f"(seed {seed}); display should show {last:02X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
