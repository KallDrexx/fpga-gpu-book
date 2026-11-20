#!/usr/bin/env python3
"""spi_test.py -- hardware check for the SPI link (Chapter 5.1, GET_INFO added in Chapter 5.2).

Send commands over SPI, read the FB_CRC back, and ask GET_INFO for the version,
capabilities and error flags.

Usage:
    .venv/bin/python host/spi_test.py [--url ftdi://ftdi:232h/1] [--freq 1000000]
    .venv/bin/python host/spi_test.py --model

Hardware: an Adafruit/FTDI FT232H board (3.3 V logic) on Pmod JB, with pyftdi
installed (`.venv/bin/pip install pyftdi`; on Linux it also needs a udev rule or
sudo to open the USB device):

    FT232H   signal    Pmod JB      Arty pin
    D0       SCK   ->  JB4          T10
    D1       MOSI  ->  JB2          Y14
    D2       MISO  <-  JB3          T11
    D3       CS    ->  JB1          W14      (pyftdi's CS0, active low)
    D4       READY <-  JB7          V16      (high = you may send)
    D5       VSYNC <-  JA4          Y17      (high during vertical blanking)
    GND      GND   --  JB5 or JB11

The script draws a few things, asks for FB_CRC after each, and compares with
the Python model in gpu_model.py. It also reads the status byte, counts VSYNC
edges for a tenth of a second (about 12 at 60 Hz) and prints "PASS: spi_test"
if every CRC matched and GET_INFO answered with the right magic byte and no
error flags (it clears them first). Exit status 0 on PASS, 1 on FAIL.

Every frame returns status and count on MISO. Command frames carry whole
packets, at most 257 bytes, and start only while READY is high. Replies are
read with read frames (first byte 0xFF), whose MOSI bytes the GPU ignores.

--model needs no board and no pyftdi: it runs the same host code against a
small Python stand-in for the link, which follows the framing rules of
Chapters 5.1 and 5.2, so you can test the host side first.
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gpu_model as g  # noqa: E402

READY, REPLY, VBLANK, OVERFLOW = 0x01, 0x04, 0x08, 0x10
MAX_PACKET = 257          # opcode + length + 255 payload bytes: READY high leaves room for one
READ = 0xFF               # first byte of a read frame


class SpiGpu:
    """The host side of the SPI link. `port` has exchange(bytes, duplex=True) -> bytes
    (one CS frame per call); `ready()` says whether the READY pin is high."""

    def __init__(self, port, ready, vsync=None):
        self.port, self.ready, self.vsync = port, ready, vsync
        self.replies = bytearray()   # reply bytes read so far and not yet used
        self.status = 0

    def _frame(self, tx):
        """One CS frame. Returns the MISO bytes: status, count, then (read frames) replies."""
        rx = bytes(self.port.exchange(bytes(tx), duplex=True))
        if rx:
            self.status = rx[0]
        if len(rx) >= 2 and tx[0] == READ:
            count = min(rx[1], len(rx) - 2)           # byte 1 says how many reply bytes follow
            self.replies += rx[2:2 + count]
        return rx

    def wait_ready(self, timeout=2.0):
        t0 = time.time()
        while not self.ready():
            if time.time() - t0 > timeout:
                raise TimeoutError("READY stayed low for %.1f s" % timeout)

    def send(self, data):
        """Send whole packets. Each frame holds one or more complete packets, at most
        257 bytes, and starts only when READY is high."""
        data = bytes(data)
        i = 0
        while i < len(data):
            j = i
            while j < len(data):                      # take packets while they fit
                n = 2 + data[j + 1] if j + 1 < len(data) else len(data) - j
                if j > i and (j - i) + n > MAX_PACKET:
                    break
                j += n
            self.wait_ready()
            self._frame(data[i:j])
            i = j

    def read_reply(self, n, timeout=2.0):
        """Poll with read frames until n reply bytes have arrived. A read frame
        needs no FIFO space, so it does not wait for READY. Returns the n bytes."""
        t0 = time.time()
        while len(self.replies) < n:
            if time.time() - t0 > timeout:
                raise TimeoutError("no %d-byte reply within %.1f s (status %02X)" % (n, timeout, self.status))
            self._frame(bytes([READ]) + bytes(1 + n - len(self.replies)))   # status, count, the rest
        out, self.replies = bytes(self.replies[:n]), self.replies[n:]
        return out

    def fb_crc(self):
        self.send(g.fb_crc())
        return g.parse_crc_reply(self.read_reply(4))

    def info(self):
        """GET_INFO (Chapter 5.2) -> dict: magic, version, caps, width, height, errors."""
        self.send(g.get_info())
        return g.parse_info(self.read_reply(9))

    def flags(self):
        names = [(OVERFLOW, "OVERFLOW")]
        return [n for bit, n in names if self.status & bit]


# ------------------------------------------------------------- stand-in link (--model)
class SimPort:
    """Plays the GPU's side of a CS frame: status, count, reply bytes."""

    def __init__(self):
        self.gpu = g.Gpu()
        self.queue = bytearray()

    def exchange(self, tx, duplex=True):
        tx = bytes(tx)
        status = 0x41 | (0x04 if self.queue else 0)       # signature 01, READY, REPLY
        count = min(len(self.queue), 16)
        out = bytearray([status, count]) + bytearray(max(0, len(tx) - 2))
        if tx and tx[0] == READ:                          # read frame: replies, MOSI ignored
            for k in range(2, min(len(tx), 2 + count)):
                out[k] = self.queue.pop(0)
        else:
            self.queue += self.gpu.feed(tx, sof=True)     # commands run after the frame; its first byte starts a packet
        return bytes(out[:len(tx)])


def add_link_args(ap):
    ap.add_argument("--url", default="ftdi://ftdi:232h/1")
    ap.add_argument("--freq", type=float, default=1e6, help="SCK in Hz (start at 1e6; up to 5e6 on hardware)")
    ap.add_argument("--model", action="store_true", help="no hardware: run against the Python stand-in")


def open_link(args):
    """An SpiGpu on the FT232H (or on the Python stand-in with --model)."""
    if args.model:
        link = SpiGpu(SimPort(), lambda: True)
    else:
        from pyftdi.spi import SpiController          # pip install pyftdi
        ctrl = SpiController(cs_count=1)              # only CS0 (D3): D4-D7 stay GPIO
        ctrl.configure(args.url)
        port = ctrl.get_port(cs=0, freq=args.freq, mode=0)
        gpio = ctrl.get_gpio()
        gpio.set_direction(0x30, 0x00)                # D4 READY and D5 VSYNC are inputs
        link = SpiGpu(port, lambda: bool(gpio.read(with_output=True) & 0x10))
        link.vsync = lambda: bool(gpio.read(with_output=True) & 0x20)
    return link


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_link_args(ap)
    args = ap.parse_args()
    link = open_link(args)

    model = g.Gpu()
    link.send(g.clear_errors(0x0F))                   # start from clean flags: earlier experiments may have set some
    steps = [
        ("clear", g.clear(0)),
        ("palette + rectangles", b"".join([g.set_palette(1, 0xFF0000), g.set_palette(2, 0x00FF00)]) +
         g.fill_rect(10, 10, 100, 80, 1) + g.fill_rect(60, 40, 200, 150, 2)),
        ("lines", b"".join(g.draw_line(160, 120, 160 + dx, 120 + dy, 1 + (dx + dy) % 2)
                           for dx, dy in [(100, 0), (70, 70), (0, 100), (-70, 70), (-100, 0), (-70, -70), (0, -100), (70, -70)])),
        ("triangle", g.draw_tri(20, 200, 150, 160, 90, 235, 2)),
    ]
    bad = 0
    for name, data in steps:
        link.send(data)
        model.feed(data)
        got = link.fb_crc()
        want = model.feed(g.fb_crc())
        want = g.parse_crc_reply(want)
        ok = got == want
        bad += not ok
        print("%-22s FB_CRC %08X  model %08X  %s  status %02X %s" %
              (name, got, want, "ok" if ok else "MISMATCH", link.status, " ".join(link.flags())))
    info = link.info()
    print("GET_INFO: magic %02X version %d caps %04X screen %dx%d errors %X" %
          (info["magic"], info["version"], info["caps"], info["width"], info["height"], info["errors"]))
    if info["magic"] != g.INFO_MAGIC or info["errors"]:
        bad += 1
    if link.status >> 6 != 1:
        print("status byte %02X does not start with bits 01: MISO stuck or not connected" % link.status)
        bad += 1
    if link.flags():
        bad += 1
    if link.vsync is not None:
        t0, last, edges = time.time(), link.vsync(), 0
        while time.time() - t0 < 0.1:
            now = link.vsync()
            edges += now != last
            last = now
        print("VSYNC edges in 0.1 s: %d (about 12 at 60 Hz)" % edges)
    print("PASS: spi_test" if not bad else "FAIL: spi_test (%d problems)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
