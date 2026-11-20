#!/usr/bin/env python3
"""hud_demo.py -- a heads-up display that redraws itself every frame, driven by a few bytes (Chapter 5.7).

Usage:
    .venv/bin/python host/hud_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 600]
    .venv/bin/python host/hud_demo.py --model

The scene is one display list: clear the screen, a big triangle, a stripe, a diagonal line, the HUD panel,
four BLITs of digit glyphs (the score), a marker and FLIP. The script uploads the glyphs and the list once,
runs SET_AUTO_LIST, and from then on the GPU redraws the whole picture at every vertical blanking by itself.
A score that changes sends only WRITE_LIST_WORD patches: 7 bytes for each digit that changed. (A digit patch
rewrites two bytes of its BLIT: the glyph's asset id and the low byte of its x, which stays the same. The
marker's x is a whole 16-bit word.) Each frame the script prints nothing; at the end it reports the bytes it
sent per frame, against what drawing the same picture with ordinary commands every frame would cost.

--model needs no board: it runs the same host code against the Python model (Gpu.auto_run() at each blanking),
and checks every frame against the same picture drawn directly with ordinary commands, no list involved.
With --ppm FILE it writes the last frame.

Wiring and options are those of spi_test.py. The patches are sent in the visible part of each frame, once
VSYNC has fallen: they wait behind the running list's FLIP, run at the next swap, just before the next run of
the list, and show two frames after they were sent (Chapter 5.7, section 3).
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402

PALETTE = {0: 0x000000, 1: 0x101A40, 2: 0x104020, 3: 0x505060, 4: 0xC04020, 5: 0xF0E040, 6: 0x20C0D0, 7: 0xFFFFFF,
           8: 0xE08020, 9: 0xC030C0, 10: 0xFF0000, 11: 0x20C040, 12: 0x6060FF, 13: 0xA0A0FF}

FONT = {  # 5 x 7 digits
    0: ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    1: ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    2: ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    3: ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    4: ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    5: ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    6: ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    7: ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    8: ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    9: ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
}
MARKER_ID = 10                       # asset ids 0-9 are the digit glyphs, 10 the marker
DIGIT_X = [8, 20, 32, 44]            # the four score digits, thousands to ones
DIGIT_Y = 6
MARKER_X, MARKER_Y = 100, 150
HUD_LIST = 0


def glyph(d):
    """Digit d as an 8 x 8 asset: yellow (5) on transparent (0)."""
    rows = ["0" + r + "00" for r in FONT[d]] + ["0" * 8]
    return bytes(5 if c == "1" else 0 for r in rows for c in r)


def marker():
    """An 8 x 8 diamond, white (7)."""
    return bytes(7 if abs(x - 3.5) + abs(y - 3.5) <= 4 else 0 for y in range(8) for x in range(8))


def list0(score=(0, 0, 0, 0), mx=MARKER_X):
    """The scene as list bytes, and the offset of every patchable BLIT. `score` is the four digits it starts with."""
    b, off = bytearray(), {}
    b += g.clear(1)
    b += g.draw_tri(0, 0, 319, 0, 0, 239, 2)                  # a big triangle: it takes as long as a CLEAR
    b += g.fill_rect(0, 100, 319, 119, 4)
    b += g.draw_line(0, 239, 319, 0, 7)
    b += g.fill_rect(0, 0, 63, 19, 3)                          # the HUD panel
    for i in range(4):
        off["digit%d" % i] = len(b)
        b += g.blit(score[i], DIGIT_X[i], DIGIT_Y)
    off["marker"] = len(b)
    b += g.blit(MARKER_ID, mx, MARKER_Y)
    b += g.draw_tri(200, 160, 260, 230, 180, 230, 6)
    b += g.flip()
    return bytes(b), off


def digit_patch(off, i, d, lid=HUD_LIST):
    """WRITE_LIST_WORD for digit i of the list: the BLIT's asset id (byte 2) and the low byte of its x (byte 3)."""
    return g.write_list_word(lid, off["digit%d" % i] + 2, d | (DIGIT_X[i] << 8))


def marker_patch(off, x, lid=HUD_LIST):
    """WRITE_LIST_WORD for the marker's x, a 16-bit field: a whole word."""
    return g.write_list_word(lid, off["marker"] + 3, x)


def setup_packets(with_list=True):
    """Palette, glyphs, marker and (with_list) the list; returns (packets, offsets)."""
    pk = [g.clear_errors(0x0F), g.clear(0), g.flip(), g.clear(0)]
    pk += [g.set_palette(i, c) for i, c in PALETTE.items()]
    for d in range(10):
        pk += g.upload_packets(d, 8, 8, glyph(d))
    pk += g.upload_packets(MARKER_ID, 8, 8, marker())
    body, off = list0()
    if with_list:
        pk += g.list_packets(HUD_LIST, body)
    return pk, off


def digits(score):
    return [(score // 10 ** (3 - i)) % 10 for i in range(4)]


def score_at(frame):
    """A made-up game: 10 points every 7 frames, 100 every 50, and a bonus of 1000 once."""
    return (frame // 7) * 10 + (frame // 50) * 100 + (1000 if frame >= 300 else 0)


class Hud:
    """What the host remembers about the display: the score and the marker, and what it has to send to change them."""

    def __init__(self, off):
        self.off, self.score, self.mx = off, 0, MARKER_X

    def patches(self, frame):
        """The WRITE_LIST_WORD packets for this frame: one per digit that changed, and the marker every 25 frames."""
        new = score_at(frame)
        a, b = digits(self.score), digits(new)
        pk = [digit_patch(self.off, i, b[i]) for i in range(4) if a[i] != b[i]]
        self.score = new
        if frame % 25 == 24:
            self.mx = MARKER_X + (frame // 25) * 3
            pk.append(marker_patch(self.off, self.mx))
        return pk


def direct_frame(score, mx):
    """The same picture, made without a list: a new GPU and the scene's commands sent as ordinary commands."""
    m = g.Gpu()
    pk, _ = setup_packets(with_list=False)
    m.feed(b"".join(pk))
    m.feed(list0(digits(score), mx)[0])                               # the list's bytes as commands: ends in FLIP
    m.vblank()
    return m.screen()


def wait_vblank_end(link, timeout=0.1):
    """Wait for VSYNC (the vblank pin) to fall: the visible part of the frame has begun."""
    if link.vsync is None:
        return
    t0 = time.time()
    while link.vsync() and time.time() - t0 < timeout:
        pass


def run_model(frames):
    import spi_test
    link = spi_test.open_link(argparse.Namespace(model=True))
    m = link.port.gpu
    assert link.info()["caps"] & g.CAPS_LISTS, "GET_INFO does not report the DISPLAY_LISTS capability"
    pk, off = setup_packets()
    link.send(b"".join(pk))
    link.send(g.set_auto_list(HUD_LIST))
    hud, sizes, direct = Hud(off), [], {}
    for f in range(frames):
        data = b"".join(hud.patches(f))
        sizes.append(len(data))
        if data:
            link.send(data)
        m.auto_run()                       # the GPU's work at the next vertical blanking
        m.vblank()
        key = (hud.score, hud.mx)
        if key not in direct:                                           # drawing it directly is slow: once per picture
            direct[key] = direct_frame(*key)
        assert m.screen() == direct[key], "frame %d: the list's picture differs from the directly drawn one" % f
    assert link.info()["errors"] == 0
    return link, m, sizes


def report(sizes, off_len):
    n = len(sizes)
    total = sum(sizes)
    print("%d frames: %d bytes sent in all, %.2f per frame on average, at most %d; %d frames sent nothing" %
          (n, total, total / n, max(sizes), sum(1 for s in sizes if s == 0)))
    print("the list is %d bytes, sent once; drawing the same picture with ordinary commands every frame would cost %d bytes each, %d in all" %
          (off_len, off_len, off_len * n))


def main():
    import spi_test
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, help="frames to run (default 600 on the board, 120 with --model: the model takes 0.2 s a frame)")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    args = ap.parse_args()
    body_len = len(list0()[0])
    frames = args.frames or (120 if args.model else 600)

    if args.model:
        link, m, sizes = run_model(frames)
        report(sizes, body_len)
        if args.ppm:
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n320 240\n255\n")
                for c in m.screen():
                    v = PALETTE.get(c, 0)
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        assert max(sizes) <= 4 * 7 + 7, "a frame sent more than its patches"
        print("PASS: hud_demo (model)")
        return 0

    link = spi_test.open_link(args)
    info = link.info()
    if not info["caps"] & g.CAPS_LISTS:
        print("FAIL: hud_demo (GET_INFO capabilities %04X: no DISPLAY_LISTS bit; is the Chapter 5.7 bitstream loaded?)" % info["caps"])
        return 1
    pk, off = setup_packets()
    link.send(b"".join(pk))
    link.send(g.set_auto_list(HUD_LIST))
    print("list sent and running; counting")
    hud, sizes, t0 = Hud(off), [], time.time()
    import layer_demo as ld                      # Chapter 5.3: wait_vblank_start
    try:
        for f in range(frames):
            ld.wait_vblank_start(link)
            wait_vblank_end(link)                # send in the visible part, not in blanking
            data = b"".join(hud.patches(f))
            sizes.append(len(data))
            if data:
                link.send(data)
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    info = link.info()
    link.send(g.set_auto_list(g.AUTO_OFF))
    if not sizes:
        print("FAIL: hud_demo (no frames)")
        return 1
    report(sizes, body_len)
    print("%.1f s (%.1f frames per second); errors %X" % (dt, len(sizes) / dt if dt else 0, info["errors"]))
    ok = info["errors"] == 0
    print("PASS: hud_demo" if ok else "FAIL: hud_demo (error flags set)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
