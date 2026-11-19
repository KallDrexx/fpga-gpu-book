#!/usr/bin/env python3
"""ref_blit.py -- test stream and expected results for tb_gpu_blit (Chapter 4.8).

Usage (from the repo root, once; commit the six files it writes):
    .venv/bin/python sim/py/ref_blit.py sim

Writes:
    sim/blit_stream.hex    what the testbench sends, one entry per line, in
                           Chapter 4.7's format:
                             00NN  a byte to send over the UART
                             1NNN  fence: wait until NNN (hex) reply bytes
                                   have arrived in all
                             2000  from here on, send without looking at o_ready
                             2001  from here on, wait for o_ready before each byte
                             2002  start a throughput window
                             2003  end the throughput window
                             2004  zero the testbench's write counters
                             2005  check: since 2004 every pixel of the back
                                   buffer was written exactly once, and the
                                   front buffer not at all
                             2006  check: since 2004 no pixel of the back
                                   buffer was written twice, and the front
                                   buffer not at all
                             3NNN  the open window may take at most
                                   4096 * HHH + NNN clocks from its first write
                                   to its last, where 4HHH precedes the 3NNN
                             FFFF  end of the stream
    sim/blit_replies.hex   every reply byte the GPU must send, in order
    sim/blit_labels.txt    one line per FB_CRC reply: what it covers
    sim/blit_fb0.hex       framebuffer 0 at the end (76,800 lines)
    sim/blit_fb1.hex       framebuffer 1 at the end
    sim/blit_pal.hex       the palette at the end (256 lines, RRGGBB)

Both buffers start as Chapter 4.1's test image. The expected results come from
host/gpu_model.py; the asset RAM is inside the GPU, so the testbench sees only
what BLIT draws. Every asset the stream blits has been completely uploaded
first (the model counts reads of bytes no upload wrote, and this script stops
if there is one), because the content of a half-uploaded asset is not defined.

The stream, in order:
  A. the test image; eight assets of odd sizes (1x1, 13x7, 25x10 = exactly one
     full packet, 17x15, 255x1, 1x255, 80x40, 40x50 with its packets in reverse
     order) blitted with all eight flag combinations, clipped at every edge and
     corner, overlapping, replaced in place and by a larger image; malformed
     uploads and blits that must change nothing; mixed with the older commands
     and a FLIP; a reset
  B. five throughput windows, ten single BLITs checked in the counting buffer
  C. a full asset RAM: 128 x 255 + 16 x 8 = 32,768 bytes, refusals, a
     back-pressure burst behind the biggest BLIT, a sixth window, a reset
  D. 12 random assets and 340 random BLITs (inside, partly off, past every edge,
     at the int16 limits), re-uploads, an FB_CRC every 50
"""
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

(IGNORE_READY, HONOR_READY, WIN_START, WIN_END, COUNT_RESET, COUNT_ONCE, COUNT_AT_MOST,
 WIN_LIMIT, WIN_LIMIT_HI, END) = 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x3000, 0x4000, 0xFFFF
SEED = 4848
WIN_SLACK = 16      # spare clocks allowed per throughput window ...
WIN_PER_ROW = 2     # ... plus this many per sprite row
N_RANDOM = 340
GROUP = 50          # random blits per FB_CRC
ALL_FLAGS = [0, g.FLIP_X, g.FLIP_Y, g.FLIP_X | g.FLIP_Y]


class Stream:
    """The testbench's stream, fed through the model as it is built."""

    def __init__(self, model):
        self.model = model
        self.items = []
        self.replies = bytearray()
        self.labels = []
        self.nbytes = 0
        self.blits = 0
        self.uploads = 0

    def send(self, data):
        self.items.extend(data)
        self.replies += self.model.feed(data)
        self.nbytes += len(data)

    def blit(self, a, x, y, flags=0):
        self.send(g.blit(a, x, y, flags))
        self.blits += 1

    def upload(self, a, w, h, data, order="seq"):
        """A whole image. order 'seq': packet after packet, offset 0 first.
        'rev': an allocating header-only packet first, then the data packets
        from the last to the first (the offset-0 one, which must not allocate
        a second time, comes last)."""
        pk = g.upload_packets(a, w, h, data)
        self.uploads += 1
        if order == "rev":
            self.send(g.upload_asset(a, w, h, b""))
            pk = pk[::-1]
        for p in pk:
            self.send(p)

    def crc(self, label):
        self.send(g.fb_crc())
        self.labels.append(label)

    def directive(self, d):
        self.items.append(d)

    def fence(self):
        self.items.append(0x1000 + len(self.replies))

    def window(self, a, x, y, flags):
        """One BLIT alone in a throughput window; the limit counts every pixel of the sprite."""
        _, w, h = self.model.assets[a]
        limit = w * h + WIN_PER_ROW * h + WIN_SLACK
        before = self.model.blit_drawn
        self.crc("before a throughput window (everything earlier has finished)")   # nothing else may write inside the window
        self.fence()
        self.directive(WIN_START)
        self.directive(WIN_LIMIT_HI + (limit >> 12))
        self.directive(WIN_LIMIT + (limit & 0xFFF))
        self.blit(a, x, y, flags)
        self.crc(f"throughput window: asset {a} ({w} x {h} = {w * h} pixels) at ({x}, {y}) flags {flags}")
        self.fence()
        self.directive(WIN_END)
        assert self.model.blit_drawn > before, "a window must contain at least one write"


def make_img(rng, w, h, zero=0.3):
    """w*h palette indices, a fraction `zero` of them the transparent 0."""
    return bytes(0 if rng.random() < zero else rng.randrange(1, 256) for _ in range(w * h))


def tag_img(w, h, base=1):
    """A readable test image: every pixel differs from its neighbours in x and y,
    so a one-pixel flip error, a wrong stride or a wrong offset shows. Every
    seventh pixel is transparent."""
    return bytes(0 if (x * 3 + y * 5) % 7 == 0 else 1 + (base + x + 17 * y) % 255 for y in range(h) for x in range(w))


def rand_pos(rng, w, h):
    """A random screen position for a w x h sprite: mostly overlapping the screen,
    often across an edge or corner, sometimes far away."""
    k = rng.random()
    if k < 0.30:
        return rng.randint(0, max(0, 320 - w)), rng.randint(0, max(0, 240 - h))
    if k < 0.65:
        return rng.randint(-w - 1, 320), rng.randint(-h - 1, 240)
    if k < 0.85:
        x = rng.choice((rng.randint(-w + 1, 0), rng.randint(320 - w, 319)))
        y = rng.choice((rng.randint(-h + 1, 0), rng.randint(240 - h, 239)))
        return (x, rng.randint(-h - 1, 240)) if rng.random() < 0.5 else (rng.randint(-w - 1, 320), y)
    special = (-32768, 32767, -32768 + rng.randrange(300), 32767 - rng.randrange(300), rng.randrange(-32768, 32768))
    return rng.choice(special), rng.choice(special)


# ------------------------------------------------------------------ the stream
def build(model):
    rng = random.Random(SEED)
    s = Stream(model)
    s.directive(HONOR_READY)
    s.crc("the 4.1 test image before any command (FB_CRC itself)")
    s.fence()

    # ---------------- A. directed
    img8 = bytes([0, 1, 2, 3, 4, 5, 0, 6, 7, 0, 0, 8, 9, 10, 11, 0,
                  12, 13, 14, 0, 15, 16, 17, 18, 0, 0, 19, 20, 21, 22, 23, 24] * 2)
    s.upload(0, 8, 8, img8)
    for i, f in enumerate(ALL_FLAGS):
        s.blit(0, 20 + 40 * i, 20, f)
    for i, f in enumerate(ALL_FLAGS):
        s.blit(0, 20 + 40 * i, 40, f | g.OPAQUE)
    s.crc("asset 0 (8x8, single packet): the four flips, transparent and opaque")

    s.send(g.fill_rect(10, 70, 200, 100, 0x41))
    for i, f in enumerate(range(8)):
        s.blit(0, 12 + 24 * i, 72 + 4 * (i % 2), f)
    s.blit(0, 300, 100, 0xF8 | g.FLIP_X)                  # reserved bits are ignored
    s.crc("transparency over a background: all eight flag combinations")

    s.upload(1, 1, 1, b"\x77")
    s.upload(2, 1, 1, b"\x00")
    for x, y in ((0, 0), (319, 239), (319, 0), (0, 239), (160, 120)):
        s.blit(1, x, y, 0)
        s.blit(2, x + (1 if x == 0 else -1), y + (1 if y == 0 else -1), g.OPAQUE)
        s.blit(1, x, y + (1 if y == 0 else -1), g.FLIP_X | g.FLIP_Y)
    for x, y in ((320, 0), (-1, 0), (0, 240), (0, -1), (320, 240), (-1, -1), (32767, 32767), (-32768, -32768)):
        s.blit(1, x, y, g.OPAQUE)
    s.blit(2, 100, 150, 0)                                  # transparent: nothing
    s.blit(2, 101, 150, g.OPAQUE)                           # opaque 0: a pixel of index 0
    s.crc("1x1 assets at the four corners and just off screen; index 0 opaque and transparent")

    s.upload(3, 13, 7, tag_img(13, 7))
    s.upload(4, 25, 10, tag_img(25, 10, 50))                # 250 bytes: one packet at the 255-byte limit
    assert len(g.upload_asset(4, 25, 10, tag_img(25, 10, 50))) == 2 + 255
    s.upload(5, 17, 15, tag_img(17, 15, 100))               # 255 bytes: 250 + 5
    s.upload(6, 255, 1, tag_img(255, 1, 30))
    s.upload(7, 1, 255, tag_img(1, 255, 60))
    s.upload(8, 80, 40, tag_img(80, 40, 20))                # 3,200 bytes: 13 packets
    s.upload(9, 40, 50, tag_img(40, 50, 77), order="rev")   # 2,000 bytes, packets in reverse order
    for a, (x, y) in zip((3, 4, 5), ((10, 130), (60, 130), (100, 130))):
        for i, f in enumerate(ALL_FLAGS):
            s.blit(a, x + 150 * (i % 2) + (i // 2) * 2, y + 30 * (i // 2), f)
    s.crc("assets 3 (13x7), 4 (25x10, one 255-byte packet), 5 (17x15, two packets), four flips each")
    s.blit(6, 30, 200, 0)
    s.blit(6, 30, 202, g.FLIP_X)
    s.blit(7, 5, 5, 0)
    s.blit(7, 10, 5, g.FLIP_Y)
    s.blit(8, 200, 140, 0)
    s.blit(8, 230, 170, g.FLIP_X | g.FLIP_Y)
    s.blit(9, 150, 190, g.FLIP_Y)
    s.blit(9, 190, 190, g.FLIP_X)
    s.crc("assets 6 (255x1), 7 (1x255), 8 (80x40, 13 packets), 9 (40x50, packets in reverse order)")
    s.fence()

    # clipping: every edge, every corner, all four flips, for the 13x7 asset 3
    w, h = 13, 7
    xs = (-13, -12, -6, 0, 300, 307, 308, 313, 319, 320)
    ys = (-7, -6, -3, 0, 230, 233, 234, 236, 239, 240)
    s.send(g.clear(0x20))
    for i, x in enumerate(xs):
        s.blit(3, x, 100 + 0 * i, ALL_FLAGS[i % 4] | (g.OPAQUE if i % 3 == 0 else 0))
        s.blit(3, x, 110, ALL_FLAGS[(i + 1) % 4])
    for i, y in enumerate(ys):
        s.blit(3, 100, y, ALL_FLAGS[i % 4])
        s.blit(3, 200, y, ALL_FLAGS[(i + 2) % 4] | g.OPAQUE)
    s.crc("asset 3 clipped one pixel at a time across every edge (left, right, top, bottom)")
    for fx in (-12, -1, 308, 319):
        for fy in (-6, -1, 234, 239):
            s.blit(5, fx, fy, ALL_FLAGS[(fx + fy) % 4])
    for f in range(8):
        s.blit(8, -40 + f, -20 + f * 5, f)
        s.blit(8, 280 - f, 220 - f * 5, f)
    s.crc("assets 5 and 8 straddling every corner, all flag combinations")

    # overlap: later sprites go on top, transparent pixels show what is under
    s.send(g.clear(0x30))
    for k in range(10):
        s.blit(4, 110 + 6 * k, 50 + 3 * k, ALL_FLAGS[k % 4] | (g.OPAQUE if k % 5 == 4 else 0))
        s.blit(9, 130 + 4 * k, 40 + 5 * k, ALL_FLAGS[(k + 1) % 4])
    s.crc("ten overlapping sprites of two assets: order matters, index 0 shows what is below")

    # replace asset 3 in place with another shape of the same size, asset 5 by a bigger one
    s.upload(3, 7, 13, tag_img(7, 13, 140))
    s.blit(3, 20, 20, 0)
    s.blit(3, 40, 20, g.FLIP_X | g.FLIP_Y)
    s.upload(5, 20, 20, tag_img(20, 20, 160))               # another size: new space
    s.blit(5, 70, 20, 0)
    s.blit(5, 100, 20, g.FLIP_Y)
    for a, x in ((0, 140), (4, 160), (8, 200), (9, 250), (6, 10), (7, 300)):    # the others must be intact
        s.blit(a, x, 60 if a != 6 else 230, ALL_FLAGS[a % 4])
    s.crc("asset 3 replaced in place (7x13), asset 5 replaced by 20x20 in new space, every other asset intact")
    s.upload(8, 40, 80, tag_img(40, 80, 33))                # same size, another shape
    s.upload(1, 4, 4, tag_img(4, 4, 90))                    # 1x1 becomes 4x4
    s.blit(8, 5, 120, 0)
    s.blit(1, 60, 160, g.FLIP_X)
    s.blit(2, 70, 160, g.OPAQUE)                            # asset 2 untouched
    s.crc("asset 8 replaced by a 40x80 image of the same size, asset 1 grown to 4x4")
    s.fence()

    # malformed: none of these may change anything the next blits show
    s.send(g.packet(g.UPLOAD_ASSET, bytes([3, 7, 13, 0])))                    # 4 bytes: too short
    s.send(g.upload_asset(16, 4, 4, bytes(16)))                              # id out of range
    s.send(g.upload_asset(255, 4, 4, bytes(16)))
    s.send(g.upload_asset(14, 0, 5, b""))                                     # w = 0
    s.send(g.upload_asset(14, 5, 0, b""))                                     # h = 0
    s.send(g.upload_asset(14, 4, 4, bytes(17)))                               # more data than w*h
    s.send(g.upload_asset(3, 7, 13, bytes([99] * 20), 80))                    # offset + n past the end
    s.send(g.upload_asset(3, 7, 13, bytes([99] * 20), 90))                    # w x h matches, runs 19 bytes past the end
    s.send(g.upload_asset(3, 13, 7, bytes([99] * 20), 30))                    # w, h do not match asset 3
    s.send(g.upload_asset(3, 8, 8, bytes([99] * 20), 10))
    s.send(g.upload_asset(12, 5, 5, bytes([99] * 20), 5))                     # asset 12 was never uploaded
    s.send(g.packet(g.UPLOAD_ASSET, bytes([0, 0, 0, 0, 0, 1, 2, 3])))         # reset with data: ignored
    s.send(g.packet(g.UPLOAD_ASSET, b""))
    s.send(g.upload_asset(15, 255, 255, b""))                                 # larger than the whole RAM: refused
    s.send(g.upload_asset(15, 255, 255, bytes([5] * 100), 100))
    s.blit(14, 10, 10, 0)                                                     # never uploaded
    s.blit(12, 10, 10, g.OPAQUE)
    s.blit(15, 10, 10, g.OPAQUE)                                              # refused upload: not an asset
    s.blit(16, 10, 10, g.OPAQUE)
    s.blit(255, 10, 10, g.OPAQUE)
    s.send(g.packet(g.BLIT, bytes([0, 10, 0, 10, 0])))                        # length 5
    s.send(g.packet(g.BLIT, bytes([0, 10, 0, 10, 0, 4, 0])))                  # length 7
    s.send(g.packet(g.BLIT, b""))
    s.send(g.nop(g.blit(0, 50, 50, g.OPAQUE)))                                # inside a NOP
    s.send(g.packet(0x87, g.blit(0, 50, 50, g.OPAQUE)))                       # inside an unknown packet
    s.crc("malformed UPLOAD_ASSET and BLIT packets, refused uploads, unknown ids (nothing may change or draw)")
    for a, x in ((3, 20), (4, 40), (5, 70), (8, 5)):                          # still the same pictures
        s.blit(a, x + 120, 180, ALL_FLAGS[a % 4])
    s.crc("after the malformed packets: assets 3, 4, 5 and 8 still draw their images")
    s.fence()

    # ordering with the older commands, no fences; BLIT goes to the back buffer
    s.send(g.fill_rect(20, 150, 120, 200, 0x31))
    s.blit(3, 40, 160, g.OPAQUE)
    s.send(g.plot(44, 164, 0x33))
    s.blit(3, 41, 162, 0)
    s.send(g.draw_tri(20, 150, 120, 200, 20, 200, 0x32))
    s.send(g.draw_line(20, 150, 120, 200, 0x35))
    s.blit(4, 60, 170, g.FLIP_X)
    s.send(g.fill_rect(60, 175, 70, 180, 0x36))
    s.blit(5, 100, 140, g.FLIP_Y)
    s.send(g.set_palette(0x32, 0xA1B2C3))
    s.crc("BLIT mixed with FILL_RECT, PLOT, DRAW_TRI, DRAW_LINE, SET_PALETTE, in order, no fences")
    s.send(g.flip())
    s.blit(8, 100, 20, 0)
    s.blit(9, 140, 60, g.FLIP_X)
    s.crc("after FLIP: BLITs go to the new back buffer")
    s.send(g.flip())
    s.crc("after a second FLIP: the first buffer again, with the BLIT-free picture")
    s.fence()

    # reset: every asset is forgotten
    s.send(g.asset_reset())
    s.blit(0, 5, 5, g.OPAQUE)
    s.blit(8, 5, 50, g.OPAQUE)
    s.blit(1, 5, 90, g.OPAQUE)
    s.crc("after the reset: every earlier asset id is empty, BLIT draws nothing")
    s.upload(8, 3, 3, bytes([1, 2, 3, 4, 5, 6, 7, 8, 9]))                    # the freed space is usable again
    s.upload(0, 80, 40, tag_img(80, 40, 11))
    s.blit(8, 10, 10, g.FLIP_X)
    s.blit(0, 100, 100, 0)
    s.crc("assets uploaded again after the reset")
    s.fence()

    # ---------------- B. throughput windows and counting checks
    s.send(g.clear(0))
    s.window(0, 100, 100, 0)                                  # 80 x 40 on screen
    s.upload(1, 1, 1, b"\x55")
    s.window(1, 5, 5, 0)                                      # 1 x 1
    s.upload(6, 255, 1, tag_img(255, 1, 30))
    s.window(6, 30, 200, g.FLIP_X)                            # 255 x 1
    s.upload(7, 1, 255, tag_img(1, 255, 60))
    s.window(7, 300, -10, g.FLIP_X | g.FLIP_Y | g.OPAQUE)    # 1 x 255, partly off screen
    s.upload(9, 40, 50, make_img(rng, 40, 50, 0.5))
    s.window(9, -20, 200, g.OPAQUE)                           # 40 x 50, partly off screen

    shapes = [(0, 12, 30, 0), (0, 200, 150, g.FLIP_X), (9, 100, 20, g.FLIP_Y), (9, -10, -10, g.FLIP_X | g.FLIP_Y),
              (6, 50, 100, 0), (7, 310, 230, g.FLIP_Y), (8, 400, 400, 0), (0, 300, 220, g.OPAQUE | g.FLIP_X),
              (9, 300, -45, g.OPAQUE), (0, 240, 200, 0)]
    s.upload(8, 3, 3, bytes([1, 2, 3, 4, 5, 6, 7, 8, 9]))
    for i, (a, x, y, f) in enumerate(shapes):
        s.directive(COUNT_RESET)
        s.blit(a, x, y, f)
        s.crc(f"counting check: one BLIT of asset {a} at ({x}, {y}) flags {f}: no pixel written twice")
        s.fence()
        s.directive(COUNT_AT_MOST)
    s.fence()

    # ---------------- C. a full asset RAM
    s.send(g.asset_reset())
    s.send(g.upload_asset(15, 255, 255, b""))                                 # larger than the RAM, even empty
    s.blit(15, 0, 0, g.OPAQUE)
    big = make_img(rng, 128, 255, 0.25)
    s.upload(7, 128, 255, big)                                                # 32,640 bytes: 131 packets
    s.upload(8, 16, 8, tag_img(16, 8, 5))                                     # the last 128 bytes: the RAM is full
    s.upload(9, 1, 1, b"\x05")                                                # refused: no room
    s.upload(3, 2, 1, b"\x06\x07")                                            # refused
    s.upload(8, 1, 2, b"\x07\x08")                                            # another size, no room: asset 8 stays as it was
    s.send(g.upload_asset(8, 2, 1, b"\x09"))                                  # (the same size as the refusal above)
    s.send(g.upload_asset(8, 4, 32, b"\x01", 5))                              # continuing packet, same size, other shape: ignored
    s.blit(9, 2, 2, g.OPAQUE)
    s.blit(3, 2, 4, g.OPAQUE)
    s.blit(7, 10, -100, 0)
    s.blit(7, 150, 20, g.FLIP_X)
    s.blit(7, 190, 130, g.FLIP_Y)
    s.blit(7, 260, 60, g.FLIP_X | g.FLIP_Y)
    s.blit(7, -64, 100, g.OPAQUE)
    s.blit(8, 300, 200, 0)
    s.blit(8, 300, 220, g.FLIP_X | g.FLIP_Y)
    s.crc("a full RAM: 128x255 and 16x8 stored, further uploads refused, both assets intact")
    s.upload(8, 8, 16, make_img(rng, 8, 16, 0.25))                            # same size, other shape: replaced in place
    s.blit(7, 30, 100, g.FLIP_X)
    s.blit(8, 20, 10, g.OPAQUE)
    s.blit(8, 40, 10, g.FLIP_Y)
    s.crc("asset 8 replaced in place by an 8x16 image in the full RAM, asset 7 intact")
    s.fence()
    s.window(7, 180, 150, g.FLIP_Y)                                           # 128 x 255, partly off screen
    s.send(g.clear(0x10))
    s.directive(IGNORE_READY)
    s.blit(7, 40, 60, g.FLIP_X | g.OPAQUE)                                    # 32,640 clocks
    s.send(g.fill_rect(0, 0, 319, 239, 0x49))                                 # erased by... nothing: it must run after the BLIT
    for i in range(20):
        s.blit(8, rng.randrange(-20, 320), rng.randrange(-10, 240), rng.randrange(8))
    s.crc("back-pressure: the biggest BLIT, a full-screen FILL_RECT and 20 BLITs sent right behind it")
    s.fence()
    s.directive(HONOR_READY)
    s.send(g.asset_reset())
    s.upload(9, 5, 4, tag_img(5, 4, 3))
    s.blit(7, 0, 0, g.OPAQUE)
    s.blit(9, 150, 100, g.FLIP_X)
    s.crc("after the second reset: asset 7 is gone, asset 9 draws in the freed RAM")
    s.fence()

    # ---------------- D. random
    s.send(g.asset_reset())
    ids = rng.sample(range(g.N_ASSETS), 12)
    sizes = {}
    for k, a in enumerate(ids):
        if k % 6 == 5:
            w, h = rng.choice(((rng.randint(100, 255), rng.randint(1, 4)), (rng.randint(1, 4), rng.randint(100, 255))))
        elif k % 6 == 4:
            w, h = rng.randint(30, 70), rng.randint(30, 70)
        else:
            w, h = rng.randint(1, 40), rng.randint(1, 40)
        sizes[a] = (w, h)
        s.upload(a, w, h, make_img(rng, w, h, rng.choice((0.0, 0.2, 0.5))), order=rng.choice(("seq", "seq", "rev")))
    s.crc("12 random assets uploaded (no blits yet)")
    stats = {"off": 0, "partial": 0, "invalid": 0, "reupload": 0, "rejected": 0}
    for i in range(N_RANDOM):
        have = sorted(model.assets)
        if rng.random() < 0.04:
            a = rng.choice([x for x in range(g.N_ASSETS) if x not in model.assets] + [16, 200])
            stats["invalid"] += 1
            s.blit(a, rng.randrange(320), rng.randrange(240), rng.randrange(8))
        else:
            a = rng.choice(have)
            _, w, h = model.assets[a]
            x, y = rand_pos(rng, w, h)
            f = rng.randrange(8)
            vis = x < 320 and y < 240 and x + w > 0 and y + h > 0
            stats["off"] += not vis
            stats["partial"] += vis and (x < 0 or y < 0 or x + w > 320 or y + h > 240)
            s.blit(a, x, y, f)
        if i % 37 == 11:                                          # replace an asset with a new image
            a = rng.choice(sorted(model.assets))
            _, w, h = model.assets[a]
            if rng.random() < 0.5:
                nw, nh = w, h                                     # same size: in place
            elif rng.random() < 0.5:
                nw, nh = h, w                                     # same size, other shape
            else:
                nw, nh = rng.randint(1, 45), rng.randint(1, 45)   # new space
            before = model.upload_rejected
            s.upload(a, nw, nh, make_img(rng, nw, nh, 0.3), order=rng.choice(("seq", "rev")))
            stats["reupload"] += 1
            stats["rejected"] += model.upload_rejected - before
        if i % 41 == 5:
            s.send(g.fill_rect(rng.randrange(-20, 300), rng.randrange(-20, 220), rng.randrange(20, 340), rng.randrange(20, 260), rng.randrange(256)))
        if i % 53 == 20:
            s.send(g.draw_tri(*(rng.randrange(-30, 350) if k % 2 == 0 else rng.randrange(-30, 270) for k in range(6)), rng.randrange(256)))
        if i % 61 == 30:
            s.send(g.plot(rng.randrange(-5, 325), rng.randrange(-5, 245), rng.randrange(256)))
        if i % 67 == 8:
            s.send(g.packet(0x80 + rng.randrange(128), bytes(rng.randrange(256) for _ in range(rng.randrange(16)))))
        if i % 73 == 40:
            s.send(g.draw_line(rng.randrange(320), rng.randrange(240), rng.randrange(320), rng.randrange(240), rng.randrange(256)))
        if i % GROUP == GROUP - 1:
            s.crc(f"random BLITs {i - GROUP + 2}-{i + 1}")
            if i in (149, 249):
                s.send(g.flip())
                s.crc(f"after a FLIP behind random BLIT {i + 1}")
                s.fence()
    s.fence()
    # every asset once more in all eight flag combinations: nothing was overwritten
    s.send(g.clear(0))
    for k, a in enumerate(sorted(model.assets)):
        _, w, h = model.assets[a]
        for f in range(8):
            s.blit(a, 8 + (k % 6) * 52 + (f % 4) * 3, 8 + (k // 6) * 100 + (f // 4) * 40, f)
    s.crc("every asset still in the RAM blitted with all eight flag combinations")
    s.fence()
    s.directive(END)
    assert model.blit_unwritten == 0, "the stream blitted a part of an asset that was never uploaded"
    stats["sizes"] = [sizes[a] for a in ids]
    return s, stats


def main():
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} OUTDIR    (normally: sim)")
    outdir = sys.argv[1]
    os.makedirs(outdir, exist_ok=True)
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in palette()]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    s, stats = build(model)
    assert len(s.items) < 262144, "stream too long for the testbench's array"

    def write(name, lines, comment):
        with open(os.path.join(outdir, name), "w") as f:
            f.write(f"// {comment} (made by sim/py/ref_blit.py)\n")
            f.write("\n".join(lines) + "\n")

    write("blit_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_gpu_blit stream: 00NN byte, 1NNN fence, 20xx directive, 3NNN/4HHH window limit, FFFF end")
    write("blit_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("blit_fb0.hex", [f"{v:02x}" for v in model.bufs[0]], "expected framebuffer 0, address y*320+x")
    write("blit_fb1.hex", [f"{v:02x}" for v in model.bufs[1]], "expected framebuffer 1, address y*320+x")
    write("blit_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    with open(os.path.join(outdir, "blit_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_blit: {len(s.items)} stream entries, {s.nbytes} bytes, {s.blits} BLITs "
          f"({model.blit_count} of an uploaded asset, {model.blit_drawn} pixels), {s.uploads} images uploaded, "
          f"{len(s.replies) // 4} FB_CRC replies, {model.flips} FLIPs -> {outdir}/blit_*")
    print(f"  random phase: {stats['off']} fully off screen, {stats['partial']} partly off, {stats['invalid']} unknown ids, "
          f"{stats['reupload']} re-uploads ({stats['rejected']} refused); refused uploads in all: {model.upload_rejected}")


if __name__ == "__main__":
    main()
