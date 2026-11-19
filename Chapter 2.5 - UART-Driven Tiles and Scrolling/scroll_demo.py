#!/usr/bin/env python3
"""scroll_demo.py -- draw a tile map once, then scroll it at 60 frames per second (Chapter 2.5).

Usage:
    python3 host/scroll_demo.py /dev/ttyUSB1                 # Linux
    python3 host/scroll_demo.py COM5 --dx 1 --dy 0           # Windows, horizontal only
    python3 host/scroll_demo.py /dev/ttyUSB1 --seconds 20    # stop after 20 s

The FPGA side is Chapter 2.5's uart_ppu, reached with the Step 0.5 packets
'W' addr data (no reply) and 'R' addr (one-byte reply). Register map:

    0x00 LEDS       0x01 SEG        0x02 SCROLL_X   0x03 SCROLL_Y (write last)
    0x04 SCROLL_XH  0x05 ADDR_LO    0x06 ADDR_HI    0x07 DATA (VRAM[ADDR] = d, ADDR += 1)

    VRAM 0x000-0x7FF map (row*64 + col)    0x800-0xBFF tile rows (big-endian)
         0xC00-0xC0F palette (8 x 16-bit big-endian {r,g,b})
         0xC10-0xC2F sprites (8 bytes each: X hi, X lo, Y hi, Y lo, ATTR, 3 unused)

Setup sends about 6,300 bytes (half a second at 115,200 baud). After that each
frame sends one SCROLL_X and one SCROLL_Y packet, 6 bytes, plus a SCROLL_XH
packet on the two frames out of every 512 where scroll X crosses 0 or 256.
"""
import argparse
import random
import struct
import sys
import time

try:
    import serial  # pyserial
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial")

OP_W, OP_R = 0x57, 0x52
LEDS, SEG, SCROLL_X, SCROLL_Y, SCROLL_XH, ADDR_LO, ADDR_HI, DATA = range(8)
MAP_COLS, MAP_ROWS = 64, 32
MAP_W, MAP_H = MAP_COLS * 8, MAP_ROWS * 8      # 512 x 256 logical pixels
VRAM_MAP, VRAM_TILES, VRAM_PAL, VRAM_SPR = 0x000, 0x800, 0xC00, 0xC10

# Tiles from Chapter 2.3's tiles.hex: 0 empty, 1 wall, 2 body, 3 head, 4 food;
# 32-63 are the glyphs for ASCII 0x20-0x3F, so a digit's tile is its ASCII code.
EMPTY, WALL, BODY, HEAD, FOOD, STAR = 0, 1, 2, 3, 4, ord(".")

# Palette: 4 tile colors, an unused entry, 3 sprite colors. 9-bit {r, g, b}.
PALETTE = [0o001, 0o521, 0o264, 0o776, 0o000, 0o777, 0o704, 0o770]


def wr(addr, data):
    return bytes([OP_W, addr, data & 0xFF])


def vram_write(addr, payload):
    """Set ADDR once, then one DATA packet per byte (ADDR auto-increments)."""
    out = bytearray(wr(ADDR_LO, addr & 0xFF) + wr(ADDR_HI, addr >> 8))
    for b in payload:
        out += wr(DATA, b)
    return bytes(out)


def make_map(seed=1):
    """A 64x32 map: a wall around the edge (so the wraparound seam shows),
    a coordinate label in every 8x4 block, scattered stars and a snake."""
    rnd = random.Random(seed)
    cells = [EMPTY] * (MAP_COLS * MAP_ROWS)

    def put(col, row, t):
        cells[(row % MAP_ROWS) * MAP_COLS + (col % MAP_COLS)] = t

    for i in range(MAP_COLS * MAP_ROWS):
        if rnd.random() < 0.06:
            cells[i] = STAR
    for c in range(MAP_COLS):
        put(c, 0, WALL)
        put(c, MAP_ROWS - 1, WALL)
    for r in range(MAP_ROWS):
        put(0, r, WALL)
        put(MAP_COLS - 1, r, WALL)
    for bc in range(MAP_COLS // 8):            # label "cr": block column, block row
        for br in range(MAP_ROWS // 4):
            put(bc * 8 + 3, br * 4 + 2, ord(str(bc)))
            put(bc * 8 + 4, br * 4 + 2, ord(str(br)))
    for c in range(20, 40):
        put(c, 13, BODY)
    put(40, 13, HEAD)
    put(44, 13, FOOD)
    return cells


def sprite_record(x, y, image, enabled):
    """8 bytes: X and Y as big-endian 16-bit two's complement (the FPGA keeps
    the low 10 bits), ATTR = enable in bit 7, image in bits 3:0, 3 unused."""
    return struct.pack(">hhB3x", x, y, (0x80 if enabled else 0) | (image & 15))


def setup_bytes(cells, tile_words=None):
    out = bytearray()
    pal = b"".join(struct.pack(">H", v) for v in PALETTE)
    out += vram_write(VRAM_PAL, pal)
    if tile_words is not None:
        out += vram_write(VRAM_TILES, b"".join(struct.pack(">H", w) for w in tile_words))
    out += vram_write(VRAM_MAP, bytes(cells))
    # The ship sits in the middle of the screen; sprites do not scroll.
    sprites = sprite_record(152, 112, 0, True) + sprite_record(0, 0, 0, False) * 3
    out += vram_write(VRAM_SPR, sprites)
    return bytes(out)


def scroll_bytes(x, y, last_xh):
    """The per-frame update. SCROLL_Y goes last: writing it tells the FPGA the
    group is complete, and it applies all of it at the next vertical blank."""
    out = bytearray()
    xh = (x >> 8) & 1
    if xh != last_xh:
        out += wr(SCROLL_XH, xh)
    out += wr(SCROLL_X, x) + wr(SCROLL_Y, y)
    return bytes(out), xh


def read_hex_words(path):
    words = []
    with open(path) as f:
        for line in f:
            words += [int(t, 16) for t in line.split("//")[0].split()]
    if len(words) != 512:
        sys.exit(f"{path}: expected 512 tile rows, got {len(words)}")
    return words


def check_link(port, timeout):
    """Write LEDS and read it back, so a wrong port fails at once."""
    port.reset_input_buffer()
    port.write(wr(LEDS, 0x5) + bytes([OP_R, LEDS]))
    reply = port.read(1)
    if reply != b"\x05":
        sys.exit(f"no reply from the FPGA (got {reply!r} within {timeout} s): "
                 "check the port, the baud rate and the bitstream")


def main():
    ap = argparse.ArgumentParser(description="Draw a tile map once, then scroll it over UART.")
    ap.add_argument("port", help="serial port, e.g. /dev/ttyUSB1 or COM5")
    ap.add_argument("--baud", type=int, default=115200, help="baud rate (default 115200)")
    ap.add_argument("--fps", type=float, default=60.0, help="scroll updates per second (default 60)")
    ap.add_argument("--dx", type=int, default=1, help="scroll X step per frame, pixels (default 1)")
    ap.add_argument("--dy", type=int, default=1, help="scroll Y step per frame, pixels (default 1)")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (default: run until Ctrl-C)")
    ap.add_argument("--tiles", help="also upload tile graphics from this .hex file (e.g. rtl/common/tiles.hex)")
    ap.add_argument("--timeout", type=float, default=1.0, help="seconds to wait for the link check reply")
    args = ap.parse_args()

    port = serial.Serial(args.port, args.baud, bytesize=8, parity="N", stopbits=1, timeout=args.timeout)
    with port:
        check_link(port, args.timeout)
        tiles = read_hex_words(args.tiles) if args.tiles else None
        setup = setup_bytes(make_map(), tiles)
        t0 = time.perf_counter()
        port.write(setup)
        port.flush()
        print(f"map drawn: {len(setup)} bytes in {time.perf_counter() - t0:.2f} s")

        x = y = 0
        last_xh = -1                       # force one SCROLL_XH write at the start
        period = 1.0 / args.fps
        frames = sent = late = 0
        start = next_t = time.perf_counter()
        try:
            while not args.seconds or time.perf_counter() - start < args.seconds:
                pkt, last_xh = scroll_bytes(x, y, last_xh)
                port.write(pkt)
                frames += 1
                sent += len(pkt)
                x = (x + args.dx) % MAP_W
                y = (y + args.dy) % MAP_H
                next_t += period
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    late += 1
                    if delay < -period:    # fell far behind: resynchronize
                        next_t = time.perf_counter()
        except KeyboardInterrupt:
            pass
        elapsed = time.perf_counter() - start
        link = sent * 10 / max(elapsed, 1e-9) / args.baud
        print(f"{frames} frames in {elapsed:.1f} s ({frames / max(elapsed, 1e-9):.1f} fps), "
              f"{sent / max(frames, 1):.2f} bytes per frame, link {100 * link:.1f}% busy, {late} late")


if __name__ == "__main__":
    main()
