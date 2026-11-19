"""gpu_model.py -- packet encoders and a Python model of the GPU (Chapter 4.2,
extended with FILL_RECT in Chapter 4.3, DRAW_LINE in Chapter 4.4, FLIP in
Chapter 4.5, DRAW_TRI in Chapter 4.7 and UPLOAD_ASSET / BLIT in Chapter 4.8).

Copy to host/gpu_model.py, replacing the Chapter 4.7 version. It needs
host/raster_model.py from Chapter 4.6 next to it. The host scripts use it to
build packets, and sim/py/ref_cmd.py, ref_rect.py, ref_line.py,
ref_flip_sync.py, ref_flip.py, ref_tri_draw.py and ref_blit.py use the same model to make
the testbenches' expected framebuffer, palette, FB_CRC replies and frames, so
simulation, hardware and Python all follow one definition of the protocol.

Packet: [opcode][length][payload: length bytes]

  * length counts payload bytes only (0-255), not the opcode or itself.
  * 16-bit fields are little-endian, low byte first. Coordinates are signed
    (two's complement), so later steps can pass off-screen and negative
    corners.
  * A packet whose opcode is unknown, or whose length is wrong for its
    opcode, is read to its end and ignored. NOP accepts any length.
  * Commands run in order. FB_CRC sees every earlier command and none of
    the later ones.
  * There are two framebuffers (Step 4.5). The front buffer is the one on
    the screen, the back buffer is the one every drawing command and FB_CRC
    use. At power-up both hold the same picture (the test image on the
    board), buffer 0 is the front, and buffer 1 is the back.

Opcodes so far:

  0x00 NOP          any length   payload ignored
  0x01 SET_PALETTE  4            index, R, G, B        entry = {R, G, B}
  0x02 CLEAR        1            color                 every pixel = color
  0x03 PLOT         5            x lo, x hi, y lo, y hi, color
                                 off-screen (x < 0, x >= 320, y < 0, y >= 240)
                                 changes nothing
  0x04 FB_CRC       0            reply: 4 bytes, the CRC-32 of the 76,800
                                 framebuffer bytes in address order
                                 (y*320 + x, from 0), low byte first
  0x05 FILL_RECT    9            x0, y0, x1, y1 (signed 16-bit each), color
                                 (Step 4.3) fills every on-screen pixel with
                                 min(x0,x1) <= x <= max(x0,x1) and
                                 min(y0,y1) <= y <= max(y0,y1): both corners
                                 are inside the rectangle, the corners may
                                 come in either order, and the part off the
                                 screen is clipped away. x0 == x1 is a
                                 one-pixel-wide column, never empty.
  0x06 DRAW_LINE    9            x0, y0, x1, y1 (signed 16-bit each), color
                                 (Step 4.4) writes the on-screen points of
                                 line_points(x0, y0, x1, y1): Bresenham from
                                 (x0, y0) to (x1, y1), both endpoints
                                 included. Points off the screen are
                                 skipped one by one. The direction matters:
                                 DRAW_LINE a b and DRAW_LINE b a can differ
                                 where the line passes exactly half-way
                                 between two pixels (see line_points).
  0x07 FLIP         0            (Step 4.5) swaps the two buffers: the back
                                 buffer, with everything drawn into it so
                                 far, becomes the front buffer and goes on
                                 the screen during vertical blanking,
                                 and the old front buffer becomes the back
                                 buffer, keeping the picture it had. The
                                 GPU runs no command after FLIP until the
                                 swap has happened. There is no reply, and
                                 the model's swap is instant. The palette
                                 is not double-buffered: SET_PALETTE
                                 changes the colors on screen at once.
  0x08 DRAW_TRI     13           x0, y0, x1, y1, x2, y2 (signed 16-bit each),
                                 color
                                 (Step 4.7) writes color to every pixel of
                                 raster_model.tri_pixels(v0, v1, v2, 320, 240):
                                 the on-screen pixels that Chapter 4.6's
                                 edge functions and top-left rule cover.
                                 Either winding order draws the same pixels,
                                 pixels exactly on a shared edge belong to
                                 exactly one of the two triangles, and a
                                 degenerate triangle (zero area) or one
                                 entirely off the screen draws nothing.

  0x09 UPLOAD_ASSET 5 + n    id, w, h, offset (16-bit), then n data bytes,
                                 0 <= n <= 250   (Step 4.8)
                                 Stores a w x h image of palette indices,
                                 row by row, top row first, in the 32 KB asset
                                 RAM, under asset number id (0-15). The
                                 payload limit is 255 bytes, so an image goes
                                 up in several packets: each one says which
                                 asset it belongs to (id, w, h) and where its
                                 n bytes go (offset, counted in bytes from
                                 the start of the image). See Gpu.upload()
                                 for allocation, replacing, reset and what
                                 happens when the RAM is full.
  0x0A BLIT         6          id, x, y (signed 16-bit each), flags
                                 (Step 4.8) copies asset id into the back
                                 buffer with its top-left corner at (x, y),
                                 clipped to the screen. flags bit 0 mirrors it
                                 left-right (FLIP_X), bit 1 top-bottom
                                 (FLIP_Y), bit 2 (OPAQUE) draws index 0 too;
                                 otherwise index 0 is transparent and leaves
                                 the pixel alone. Other bits are ignored.
                                 An id that was never uploaded draws nothing.

The CRC is the one zlib.crc32 and binascii.crc32 compute: reflected
polynomial 0xEDB88320, initial value 0xFFFFFFFF, final XOR 0xFFFFFFFF.

Opcodes 0x80-0xFF are never assigned, so tests can use them as "unknown".
"""
import os
import struct
import sys
import zlib

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import raster_model  # noqa: E402  (Chapter 4.6)

W, H = 320, 240
FB_SIZE = W * H

NOP = 0x00
SET_PALETTE = 0x01
CLEAR = 0x02
PLOT = 0x03
FB_CRC = 0x04
FILL_RECT = 0x05
DRAW_LINE = 0x06
FLIP = 0x07
DRAW_TRI = 0x08
UPLOAD_ASSET = 0x09
BLIT = 0x0A

FLIP_X, FLIP_Y, OPAQUE = 1, 2, 4      # BLIT flags
ASSET_RAM = 32768                       # bytes of asset RAM
N_ASSETS = 16                           # asset ids 0-15
UPLOAD_HEADER = 5                       # id, w, h, offset lo, offset hi
UPLOAD_CHUNK = 255 - UPLOAD_HEADER      # data bytes that fit in one packet

# Payload length each opcode requires. None: any length is fine.
LENGTHS = {NOP: None, SET_PALETTE: 4, CLEAR: 1, PLOT: 5, FB_CRC: 0, FILL_RECT: 9, DRAW_LINE: 9, FLIP: 0, DRAW_TRI: 13,
           UPLOAD_ASSET: None, BLIT: 6}  # UPLOAD_ASSET: at least 5 (see execute)

# Bytes in the reply to each opcode (only FB_CRC replies).
REPLY_LEN = {FB_CRC: 4}


# ---------------------------------------------------------------- packets
def packet(op, payload=b""):
    """Any packet, valid or not: [op][len(payload)][payload]."""
    payload = bytes(payload)
    if not 0 <= op <= 255 or len(payload) > 255:
        raise ValueError("opcode must be a byte and payload at most 255 bytes")
    return bytes([op, len(payload)]) + payload


def nop(payload=b""):
    return packet(NOP, payload)


def set_palette(index, rgb):
    """rgb is 0xRRGGBB. Sent as index, R, G, B."""
    return packet(SET_PALETTE, bytes([index & 255, (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255]))


def clear(color):
    return packet(CLEAR, bytes([color & 255]))


def plot(x, y, color):
    """x, y are signed 16-bit (-32768..32767), sent low byte first."""
    return packet(PLOT, struct.pack("<hhB", x, y, color & 255))


def fb_crc():
    return packet(FB_CRC)


def fill_rect(x0, y0, x1, y1, color):
    """Corners are signed 16-bit and inclusive, in either order."""
    return packet(FILL_RECT, struct.pack("<hhhhB", x0, y0, x1, y1, color & 255))


def flip():
    """Swap the front and back buffers at the next vertical blanking."""
    return packet(FLIP)


def draw_tri(x0, y0, x1, y1, x2, y2, color):
    """Corners are signed 16-bit, in either winding order."""
    return packet(DRAW_TRI, struct.pack("<hhhhhhB", x0, y0, x1, y1, x2, y2, color & 255))


def tri_covered(x0, y0, x1, y1, x2, y2):
    """The pixels DRAW_TRI writes, as (x, y) in raster order (Chapter 4.6)."""
    return raster_model.tri_pixels((x0, y0), (x1, y1), (x2, y2), W, H)


def upload_asset(asset_id, w, h, data=b"", offset=0):
    """One UPLOAD_ASSET packet: n = len(data) <= 250 bytes of asset_id's
    w x h image, starting offset bytes into it."""
    data = bytes(data)
    if len(data) > UPLOAD_CHUNK:
        raise ValueError("a packet carries at most 250 data bytes: use upload_packets()")
    return packet(UPLOAD_ASSET, bytes([asset_id & 255, w & 255, h & 255]) + struct.pack("<H", offset) + data)


def upload_packets(asset_id, w, h, data):
    """A whole image as a list of packets: the first (offset 0) allocates the
    asset and carries the first 250 bytes, the rest append at their offsets."""
    data = bytes(data)
    if len(data) != w * h:
        raise ValueError("data must be w * h bytes")
    return [upload_asset(asset_id, w, h, data[o:o + UPLOAD_CHUNK], o) for o in range(0, len(data), UPLOAD_CHUNK)]


def asset_reset():
    """w = h = 0: forget every asset and free the whole asset RAM (its bytes are not erased)."""
    return upload_asset(0, 0, 0)


def blit(asset_id, x, y, flags=0):
    """x, y are signed 16-bit: the screen position of the sprite's top-left corner."""
    return packet(BLIT, struct.pack("<BhhB", asset_id & 255, x, y, flags & 255))


def blit_points(w, h, x, y, flags):
    """Every sprite pixel of a BLIT, on screen or not, as (X, Y, source index),
    where source index = row * w + column in the stored image. Sprite pixel
    (sx, sy) lands at X = x + sx, Y = y + sy; FLIP_X reads column w - 1 - sx
    instead, FLIP_Y row h - 1 - sy."""
    for sy in range(h):
        row = (h - 1 - sy) if flags & FLIP_Y else sy
        for sx in range(w):
            col = (w - 1 - sx) if flags & FLIP_X else sx
            yield x + sx, y + sy, row * w + col


def clip_rect(x0, y0, x1, y1):
    """The on-screen part of FILL_RECT's rectangle, as inclusive
    (xa, ya, xb, yb) with xa <= xb and ya <= yb, or None if nothing of it is
    on the screen."""
    xa, xb = min(x0, x1), max(x0, x1)
    ya, yb = min(y0, y1), max(y0, y1)
    if xb < 0 or xa > W - 1 or yb < 0 or ya > H - 1:
        return None
    return max(xa, 0), max(ya, 0), min(xb, W - 1), min(yb, H - 1)


def draw_line(x0, y0, x1, y1, color):
    """Endpoints are signed 16-bit and both drawn; (x0, y0) is the start."""
    return packet(DRAW_LINE, struct.pack("<hhhhB", x0, y0, x1, y1, color & 255))


def line_points(x0, y0, x1, y1):
    """Every point of DRAW_LINE's line, in drawing order, on screen or not.

    Bresenham's algorithm in its all-octant form, with integer adds and
    compares only. dx >= 0 and dy <= 0 are the distances (dy negated), sx and
    sy the directions. err is the error of the diagonal neighbour: for
    E(u, v) = DX*v - DY*u, with u and v the steps taken so far in x and y and
    DX = dx, DY = -dy, err = E(u + 1, v + 1). Each clock:

        e2 = 2*err
        step x  if e2 >= dy      (the diagonal is at least as close to the
                                  true line as the pure y step)
        step y  if e2 <= dx      (the diagonal is at least as close as the
                                  pure x step)

    Both tests use the same e2, so a clock may step x, y or both. A tie
    (two candidates exactly as far from the line) goes to the diagonal: the
    minor coordinate rounds half-way values toward (x1, y1). That is why the
    result depends on which endpoint is first. The line has
    max(|x1-x0|, |y1-y0|) + 1 points and ends exactly on (x1, y1).
    """
    dx, sx = abs(x1 - x0), (1 if x0 < x1 else -1)
    dy, sy = -abs(y1 - y0), (1 if y0 < y1 else -1)
    err = dx + dy
    x, y = x0, y0
    while True:
        yield x, y
        if x == x1 and y == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def line_points_major(x0, y0, x1, y1):
    """The same points by the other common formulation: step the major axis
    every clock and keep the decision variable
    D = 2*dmin*(k + 1) - dmaj*(2*m + 1) after k major and m minor steps,
    which is 2*dmaj times (the true minor coordinate at the next major step
    minus m + 1/2). Step the minor axis too when D >= 0, so a half-way value
    again rounds toward (x1, y1). The self-check below shows the two forms
    agree pixel for pixel."""
    ax, ay = abs(x1 - x0), abs(y1 - y0)
    sx, sy = (1 if x1 > x0 else -1), (1 if y1 > y0 else -1)
    steep = ay > ax
    dmaj, dmin = (ay, ax) if steep else (ax, ay)
    d = 2 * dmin - dmaj
    x, y = x0, y0
    pts = [(x, y)]
    for _ in range(dmaj):
        if d >= 0:
            if steep:
                x += sx
            else:
                y += sy
            d -= 2 * dmaj
        d += 2 * dmin
        if steep:
            y += sy
        else:
            x += sx
        pts.append((x, y))
    return pts


def parse_crc_reply(reply):
    """The 4 reply bytes of FB_CRC -> int."""
    return struct.unpack("<I", bytes(reply))[0]


# ---------------------------------------------------------------- CRC-32
def crc32(data):
    """CRC-32 as zlib computes it (and as FB_CRC must)."""
    return zlib.crc32(bytes(data)) & 0xFFFFFFFF


def crc32_bitwise(data):
    """The same CRC, one bit at a time, the way the hardware does it."""
    crc = 0xFFFFFFFF
    for b in bytes(data):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


# ---------------------------------------------------------------- the model
class Gpu:
    """Byte-stream model of the command processor.

    fb is 76,800 palette indices (address y*320 + x), pal 256 ints 0xRRGGBB.
    Both buffers start as copies of fb. Drawing commands and FB_CRC use the
    back buffer, which is also what the attribute `fb` returns, so code
    written before Step 4.5 keeps working. `front_fb` is the buffer on the
    screen, and `flips` counts FLIPs.
    feed() takes any slice of the stream (packets may be split anywhere) and
    returns the reply bytes those packets produce.
    """

    def __init__(self, fb=None, pal=None):
        first = bytearray(fb) if fb is not None else bytearray(FB_SIZE)
        self.pal = list(pal) if pal is not None else [0] * 256
        if len(first) != FB_SIZE or len(self.pal) != 256:
            raise ValueError("fb must be 76,800 bytes and pal 256 entries")
        self.bufs = [bytearray(first), bytearray(first)]
        self.front = 0                   # index of the buffer on the screen
        self.flips = 0                   # FLIPs executed so far
        self._pkt = bytearray()          # the packet being received
        self.ignored = 0                 # packets skipped (unknown or bad length)
        self.rect_pixels = 0             # pixels FILL_RECT has written so far
        self.line_points = 0             # points DRAW_LINE has stepped through
        self.line_pixels = 0             # ... and the on-screen ones it wrote
        self.tri_count = 0               # DRAW_TRIs executed
        self.tri_drawn = 0               # pixels DRAW_TRI has written so far
        self.ram = bytearray(ASSET_RAM)  # the asset RAM (Step 4.8)
        self.ram_written = bytearray(ASSET_RAM)   # 1 where an upload has written since allocation
        self.assets = {}                 # id -> (base, w, h); a missing id was never uploaded
        self.next_free = 0               # first unallocated byte of the asset RAM
        self.upload_rejected = 0         # valid-looking uploads refused (RAM full, size mismatch ...)
        self.blit_count = 0              # BLITs of an uploaded asset
        self.blit_drawn = 0              # pixels BLIT has written so far
        self.blit_unwritten = 0          # source bytes read that no upload had written: tests must keep this 0

    @property
    def fb(self):
        """The back buffer: where drawing commands and FB_CRC look."""
        return self.bufs[1 - self.front]

    @property
    def front_fb(self):
        """The buffer on the screen."""
        return self.bufs[self.front]

    def feed(self, data):
        out = bytearray()
        for b in bytes(data):
            self._pkt.append(b)
            if len(self._pkt) >= 2 and len(self._pkt) == 2 + self._pkt[1]:
                out += self.execute(self._pkt[0], bytes(self._pkt[2:]))
                self._pkt.clear()
        return bytes(out)

    def execute(self, op, payload):
        """Run one complete packet; return its reply bytes."""
        if op == UPLOAD_ASSET and len(payload) < UPLOAD_HEADER:
            self.ignored += 1
            return b""
        if op not in LENGTHS or (LENGTHS[op] is not None and len(payload) != LENGTHS[op]):
            self.ignored += 1
            return b""
        if op == SET_PALETTE:
            i, r, g, b = payload
            self.pal[i] = (r << 16) | (g << 8) | b
        elif op == CLEAR:
            self.fb[:] = bytes([payload[0]]) * FB_SIZE
        elif op == PLOT:
            x, y, c = struct.unpack("<hhB", payload)
            if 0 <= x < W and 0 <= y < H:
                self.fb[y * W + x] = c
        elif op == FILL_RECT:
            x0, y0, x1, y1, c = struct.unpack("<hhhhB", payload)
            r = clip_rect(x0, y0, x1, y1)
            if r is not None:
                xa, ya, xb, yb = r
                row = bytes([c]) * (xb - xa + 1)
                for y in range(ya, yb + 1):
                    self.fb[y * W + xa: y * W + xb + 1] = row
                self.rect_pixels += len(row) * (yb - ya + 1)
        elif op == DRAW_LINE:
            x0, y0, x1, y1, c = struct.unpack("<hhhhB", payload)
            for x, y in line_points(x0, y0, x1, y1):
                self.line_points += 1
                if 0 <= x < W and 0 <= y < H:
                    self.fb[y * W + x] = c
                    self.line_pixels += 1
        elif op == DRAW_TRI:
            x0, y0, x1, y1, x2, y2, c = struct.unpack("<hhhhhhB", payload)
            self.tri_count += 1
            for x, y in tri_covered(x0, y0, x1, y1, x2, y2):
                self.fb[y * W + x] = c
                self.tri_drawn += 1
        elif op == UPLOAD_ASSET:
            self.upload(payload)
        elif op == BLIT:
            self.blit(payload)
        elif op == FLIP:
            self.front = 1 - self.front
            self.flips += 1
        elif op == FB_CRC:
            return struct.pack("<I", self.crc())
        return b""

    def upload(self, payload):
        """UPLOAD_ASSET (Step 4.8), payload at least 5 bytes: id, w, h,
        offset (16-bit), then n data bytes.

        * w = h = 0: reset. Every asset is forgotten and the RAM is empty
          again (its bytes are not erased). Only with n = 0; otherwise ignored.
        * Otherwise the packet is ignored (and changes nothing) unless
          id < 16, w and h are both 1-255, and offset + n <= w * h.
        * offset = 0 allocates. If asset id exists and already holds w * h
          bytes, it keeps its place (the image is replaced in place, its w
          and h may change). Otherwise the asset is placed at the first free
          byte, which then moves up by w * h; if that does not fit in 32,768
          bytes the packet is refused, and the table and RAM stay as they
          were. The space of an asset that is replaced by one of another size
          is not given back until the next reset.
        * offset > 0 continues an upload: ignored unless asset id exists and
          its w and h are the ones in this packet.
        * The n data bytes are written at base + offset.
        """
        a, w, h = payload[0], payload[1], payload[2]
        off = payload[3] | (payload[4] << 8)
        data = payload[5:]
        n = len(data)
        if w == 0 and h == 0:
            if n == 0:
                self.assets.clear()
                self.next_free = 0
            return
        if w == 0 or h == 0 or a >= N_ASSETS or off + n > w * h:
            return
        if off == 0:
            old = self.assets.get(a)
            if old is not None and old[1] * old[2] == w * h:
                base = old[0]                       # same size: replace in place
            elif self.next_free + w * h <= ASSET_RAM:
                base = self.next_free
                self.next_free += w * h
                self.ram_written[base:base + w * h] = bytes(w * h)
            else:
                self.upload_rejected += 1
                return
            self.assets[a] = (base, w, h)
        else:
            cur = self.assets.get(a)
            if cur is None or cur[1] != w or cur[2] != h:
                self.upload_rejected += 1
                return
            base = cur[0]
        self.ram[base + off: base + off + n] = data
        self.ram_written[base + off: base + off + n] = b"\x01" * n

    def blit(self, payload):
        """BLIT (Step 4.8): see the opcode table at the top of this file."""
        a, x, y, flags = struct.unpack("<BhhB", payload)
        if a not in self.assets:
            return
        base, w, h = self.assets[a]
        self.blit_count += 1
        for X, Y, i in blit_points(w, h, x, y, flags):
            if 0 <= X < W and 0 <= Y < H:
                if not self.ram_written[base + i]:
                    self.blit_unwritten += 1
                c = self.ram[base + i]
                if c != 0 or flags & OPAQUE:
                    self.fb[Y * W + X] = c
                    self.blit_drawn += 1

    def crc(self):
        return crc32(self.fb)


if __name__ == "__main__":
    # Self-check: the bitwise CRC matches zlib, and the check value of the
    # standard "123456789" test string is 0xCBF43926.
    assert crc32(b"123456789") == crc32_bitwise(b"123456789") == 0xCBF43926
    g = Gpu()
    assert g.feed(fb_crc()) == struct.pack("<I", zlib.crc32(bytes(FB_SIZE)))
    # FILL_RECT: inclusive corners in either order, clipped to the screen.
    assert clip_rect(5, 7, 5, 7) == (5, 7, 5, 7)              # one pixel
    assert clip_rect(30, 20, 10, 40) == (10, 20, 30, 40)       # inverted x
    assert clip_rect(-32768, -32768, 32767, 32767) == (0, 0, W - 1, H - 1)
    assert clip_rect(320, 0, 400, 10) is None and clip_rect(-5, 0, -1, 10) is None
    g.feed(fill_rect(319, 239, -100, 0, 9))
    assert g.fb[0] == 9 and g.fb[FB_SIZE - 1] == 9 and g.rect_pixels == FB_SIZE
    # DRAW_LINE: endpoints included, ties toward the end point, so the
    # direction matters, and the two formulations agree everywhere.
    assert list(line_points(5, 7, 5, 7)) == [(5, 7)]
    assert list(line_points(0, 0, 4, 2)) == [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2)]
    assert list(line_points(4, 2, 0, 0)) == [(4, 2), (3, 1), (2, 1), (1, 0), (0, 0)]
    for x1 in range(-24, 25):
        for y1 in range(-24, 25):
            p = list(line_points(3, -2, x1, y1))
            assert p == line_points_major(3, -2, x1, y1), (x1, y1)
            assert p[-1] == (x1, y1) and len(p) == max(abs(x1 - 3), abs(y1 + 2)) + 1
    assert len(list(line_points(-32768, 32767, 32767, -32768))) == 65536
    g.feed(clear(0) + draw_line(-10, 120, 400, 120, 5))       # clipped point by point
    assert g.fb.count(5) == W and g.line_points == 411 and g.line_pixels == W
    # FLIP: drawing goes to the back buffer, FB_CRC covers the back buffer,
    # and a flip puts the drawn picture on the screen while the old one stays
    # behind the drawing. FLIP with a payload is ignored.
    g = Gpu()
    blank = zlib.crc32(bytes(FB_SIZE)) & 0xFFFFFFFF
    g.feed(clear(7) + draw_line(0, 0, 9, 0, 5))
    assert g.front_fb.count(0) == FB_SIZE and g.fb.count(7) == FB_SIZE - 10 and g.flips == 0
    drawn = g.crc()
    assert drawn != blank
    g.feed(flip())
    assert g.flips == 1 and g.front_fb.count(7) == FB_SIZE - 10 and g.fb.count(0) == FB_SIZE
    assert g.feed(fb_crc()) == struct.pack("<I", blank)        # FB_CRC: the new back buffer
    g.feed(packet(FLIP, b"\x00") + packet(FLIP, b"\x00\x00"))   # wrong length: ignored
    assert g.flips == 1 and g.ignored == 2
    g.feed(flip())
    assert g.flips == 2 and g.crc() == drawn and g.front_fb.count(0) == FB_SIZE
    # DRAW_TRI: Chapter 4.6's worked triangle covers 10 pixels, either winding,
    # corners in any order; zero area and off-screen draw nothing; a quad split
    # along a diagonal covers every pixel once; corners may be at the int16 limits.
    g = Gpu()
    g.feed(draw_tri(0, 0, 0, 4, 4, 0, 9))
    assert g.tri_count == 1 and g.tri_drawn == 10 and g.fb.count(9) == 10
    assert g.fb[0] == 9 and g.fb[3] == 9 and g.fb[4] == 0 and g.fb[4 * W] == 0
    g.feed(clear(0) + draw_tri(0, 0, 4, 0, 0, 4, 9))              # clockwise
    assert g.fb.count(9) == 10 and g.tri_drawn == 20
    g.feed(clear(0) + draw_tri(5, 5, 5, 5, 5, 5, 1) + draw_tri(0, 0, 9, 9, 3, 3, 1)
           + draw_tri(-50, -50, -1, -1, -1, -50, 1) + draw_tri(320, 0, 400, 0, 320, 50, 1))
    assert g.fb.count(1) == 0 and g.tri_count == 6 and g.tri_drawn == 20
    g.feed(draw_tri(0, 0, 0, 4, 4, 0, 5) + draw_tri(4, 0, 0, 4, 4, 4, 6))
    assert g.fb.count(5) + g.fb.count(6) == 16 and g.fb.count(6) == 6   # a 4x4 square, drawn once
    g.feed(clear(0) + draw_tri(-32768, -32768, 32767, -32768, 32767, 32767, 1)
           + draw_tri(-32768, -32768, 32767, 32767, -32768, 32767, 2))
    assert g.fb.count(1) + g.fb.count(2) == FB_SIZE and g.fb.count(1) > 0 and g.fb.count(2) > 0
    g.feed(packet(DRAW_TRI, bytes(12)) + packet(DRAW_TRI, bytes(14)))        # wrong length: ignored
    assert g.ignored == 2 and g.tri_count == 10
    # UPLOAD_ASSET and BLIT: a 3 x 2 image, transparent index 0, four flips,
    # clipping, several packets, replacing in place, a full RAM, and reset.
    g = Gpu()
    img = bytes([1, 0, 2,
                 3, 4, 0])
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, 0))
    px = lambda gg, x, y: gg.fb[y * W + x]
    assert [px(g, 10 + i, 20) for i in range(3)] == [1, 0, 2] and [px(g, 10 + i, 21) for i in range(3)] == [3, 4, 0]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_X))
    assert [px(g, 10 + i, 20) for i in range(3)] == [2, 0, 1] and [px(g, 10 + i, 21) for i in range(3)] == [0, 4, 3]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_Y))
    assert [px(g, 10 + i, 20) for i in range(3)] == [3, 4, 0] and [px(g, 10 + i, 21) for i in range(3)] == [1, 0, 2]
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, 10, 20, FLIP_X | FLIP_Y | OPAQUE))
    assert [px(g, 10 + i, 21) for i in range(3)] == [2, 0, 1] and [px(g, 10 + i, 20) for i in range(3)] == [0, 4, 3]
    assert g.blit_drawn == 6 and g.blit_unwritten == 0
    g = Gpu()
    g.feed(b"".join(upload_packets(5, 3, 2, img)) + blit(5, -1, -1) + blit(5, 319, 239) + blit(5, 400, 0) + blit(5, -32768, 32767))
    assert px(g, 0, 0) == 4 and g.fb.count(0) == FB_SIZE - 2 and px(g, 319, 239) == 1 and g.blit_count == 4
    # a 300-byte image goes up in two packets (250 + 50), in either order after the allocating one
    big = bytes(range(1, 101)) * 3
    pk = upload_packets(1, 100, 3, big)
    assert len(pk) == 2 and len(pk[0]) == 2 + 255 and pk[1][2:7] == bytes([1, 100, 3, 250, 0])
    g = Gpu()
    g.feed(upload_asset(1, 100, 3, b"") + pk[1] + pk[0] + blit(1, 0, 0))
    assert bytes(g.fb[0:100]) == big[:100] and bytes(g.fb[2 * W: 2 * W + 100]) == big[200:300] and g.blit_unwritten == 0
    # malformed uploads change nothing; BLIT of an unknown id draws nothing
    g = Gpu()
    g.feed(packet(UPLOAD_ASSET, bytes(4)) + upload_asset(16, 2, 2, bytes(4)) + upload_asset(0, 0, 3, b"") + upload_asset(0, 3, 0, b"")
           + upload_asset(0, 2, 2, bytes(5)) + upload_asset(0, 2, 2, b"\x01", 4) + upload_asset(0, 2, 2, b"\x01", 1)
           + packet(UPLOAD_ASSET, bytes([0, 0, 0, 0, 0, 7])) + blit(0, 0, 0, OPAQUE) + packet(BLIT, bytes(5)) + packet(BLIT, bytes(7)))
    assert g.assets == {} and g.next_free == 0 and g.blit_count == 0 and g.fb.count(0) == FB_SIZE and g.ignored == 3
    # replacing: same size keeps its place (even as a different shape), another size moves to new space
    g = Gpu()
    g.feed(b"".join(upload_packets(2, 4, 4, bytes([1] * 16)) + upload_packets(3, 2, 2, bytes([2] * 4))))
    assert g.assets == {2: (0, 4, 4), 3: (16, 2, 2)} and g.next_free == 20
    g.feed(b"".join(upload_packets(2, 8, 2, bytes([5] * 16))))
    assert g.assets[2] == (0, 8, 2) and g.next_free == 20
    g.feed(b"".join(upload_packets(2, 3, 3, bytes([6] * 9))))
    assert g.assets[2] == (20, 3, 3) and g.next_free == 29 and g.assets[3] == (16, 2, 2)
    g.feed(upload_asset(3, 4, 4, b"\x09", 5))                      # w, h do not match asset 3: refused
    assert g.upload_rejected == 1 and g.ram[16:20] == bytes([2] * 4)
    # a full RAM: 128 x 255 + 16 x 8 fill it exactly, one more byte is refused and nothing changes
    g = Gpu()
    g.feed(upload_asset(7, 128, 255, b"\x03") + upload_asset(8, 16, 8, b"\x04") + upload_asset(9, 1, 1, b"\x05"))
    assert g.assets[7] == (0, 128, 255) and g.assets[8] == (32640, 16, 8) and 9 not in g.assets
    assert g.next_free == ASSET_RAM and g.upload_rejected == 1
    g.feed(upload_asset(7, 255, 128, b"\x03") + upload_asset(8, 1, 2, b"\x01"))   # same size: in place; smaller: needs new space
    assert g.assets[7] == (0, 255, 128) and g.assets[8] == (32640, 16, 8) and g.upload_rejected == 2
    g.feed(asset_reset() + upload_asset(9, 1, 1, b"\x05"))
    assert 7 not in g.assets and g.assets == {9: (0, 1, 1)} and g.next_free == 1
    print("gpu_model self-check ok")
