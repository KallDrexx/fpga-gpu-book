"""gpu_model.py -- packet encoders and a Python model of the GPU (Chapter 4.2,
extended with FILL_RECT in Chapter 4.3 and DRAW_LINE in Chapter 4.4).

Copy to host/gpu_model.py, replacing the Chapter 4.3 version. The host
scripts use it to build packets, and sim/py/ref_cmd.py, ref_rect.py and
ref_line.py use the same model to make the testbenches' expected
framebuffer, palette and FB_CRC replies, so simulation, hardware and Python
all follow one definition of the protocol.

Packet: [opcode][length][payload: length bytes]

  * length counts payload bytes only (0-255), not the opcode or itself.
  * 16-bit fields are little-endian, low byte first. Coordinates are signed
    (two's complement), so later steps can pass off-screen and negative
    corners.
  * A packet whose opcode is unknown, or whose length is wrong for its
    opcode, is read to its end and ignored. NOP accepts any length.
  * Commands run in order. FB_CRC sees every earlier command and none of
    the later ones.

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

The CRC is the one zlib.crc32 and binascii.crc32 compute: reflected
polynomial 0xEDB88320, initial value 0xFFFFFFFF, final XOR 0xFFFFFFFF.

Opcodes 0x80-0xFF are never assigned, so tests can use them as "unknown".
"""
import struct
import zlib

W, H = 320, 240
FB_SIZE = W * H

NOP = 0x00
SET_PALETTE = 0x01
CLEAR = 0x02
PLOT = 0x03
FB_CRC = 0x04
FILL_RECT = 0x05
DRAW_LINE = 0x06

# Payload length each opcode requires. None: any length is fine.
LENGTHS = {NOP: None, SET_PALETTE: 4, CLEAR: 1, PLOT: 5, FB_CRC: 0, FILL_RECT: 9, DRAW_LINE: 9}

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
    feed() takes any slice of the stream (packets may be split anywhere) and
    returns the reply bytes those packets produce.
    """

    def __init__(self, fb=None, pal=None):
        self.fb = bytearray(fb) if fb is not None else bytearray(FB_SIZE)
        self.pal = list(pal) if pal is not None else [0] * 256
        if len(self.fb) != FB_SIZE or len(self.pal) != 256:
            raise ValueError("fb must be 76,800 bytes and pal 256 entries")
        self._pkt = bytearray()          # the packet being received
        self.ignored = 0                 # packets skipped (unknown or bad length)
        self.rect_pixels = 0             # pixels FILL_RECT has written so far
        self.line_points = 0             # points DRAW_LINE has stepped through
        self.line_pixels = 0             # ... and the on-screen ones it wrote

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
        elif op == FB_CRC:
            return struct.pack("<I", self.crc())
        return b""

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
    print("gpu_model self-check ok")
