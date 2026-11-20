#!/usr/bin/env python3
"""ref_display_lists.py -- test stream and reference frames for display lists (Chapter 5.7).

Usage (from the repo root):
    .venv/bin/python sim/py/ref_display_lists.py --stream sim   once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_display_lists.py OUTDIR         writes OUTDIR/ref_display_lists_f0.ppm ... fa.ppm

--stream writes (commit all three files):
    sim/display_lists_stream.hex   what the testbench sends, one 16-bit hex entry per line
                                   (the entries of sim/layers_stream.hex, Chapter 5.3, and two more):
                                     00NN  one byte, inside the open frame
                                     A000  open an SPI frame: wait for READY, then CS falls
                                     A001  close the frame: CS rises
                                     3000  wait for the start of the next video frame (beam at 0, 0)
                                     2YYY  wait for the start of line YYY (hex) of the video frame in progress
                                     4000  arm the frame dumpers: the next frame start begins frame f0
                                     5000  start the HUD window: from here on every packet must be a WRITE_LIST_WORD,
                                           and the bytes of each video frame are counted
                                     5001  end the HUD window
                                     1NNN  fence: read frames until NNN (hex) more reply bytes have arrived
                                     FFFF  end of the stream
    sim/display_lists_replies.hex  27 bytes: the three GET_INFO replies the testbench must read
    sim/display_lists_bytes.hex    4 numbers (16-bit hex): the host bytes of each frame of the HUD window

The story. Setup: both framebuffers cleared, a palette, ten digit glyphs and a marker as assets, six lists
(0: a whole game frame with a four-digit score, ending in FLIP; 1: a second picture, with stale bytes
behind its end; 2: a list full of commands a list may not hold; 3: a red fill only a nested RUN_LIST
would reach; 4: a list that overflows its 512-byte slot, then a patch at offset 511; 5: a short picture,
the neighbour of 4),
packets that must change nothing and set no flag, and packets that must set flags. Then, frame k is the
k-th video frame after the setup and fk-1 is what the dumper writes for it:

    k=0  SET_AUTO_LIST 0. Nothing is drawn yet: the list starts at the next vertical blanking.
    k=1  nothing (frame f0 is the empty screen: the list's first picture shows from frame 2)
    k=2  patch the ones digit            -> 0001 on the screen two frames later (frame 4, f3)
    k=3  patch two digits                -> 0010 in frame 5
    k=4  patch the marker's x            -> frame 6
    k=5  SET_AUTO_LIST 1                 -> list 1 from frame 7
    k=6  patch list 1's color, patch list 0's hundreds digit (not running: nothing shows)
    k=7  SET_AUTO_LIST off, patch list 1's color again (no run is due: nothing shows)
    k=8  RUN_LIST 2                      -> list 2's picture in frame 9 (the engine is idle: it runs at once)
    k=9  SET_AUTO_LIST 0, RUN_LIST 4, RUN_LIST 5
                                         -> list 5's picture in frame 10; list 0 again, with its patched digits, in frame 11
    k=10 GET_INFO

Every host command of frame k reaches the GPU's parser only after the vertical blanking that ends frame k:
the list that is running stops at its FLIP, which waits for blanking, and host bytes wait behind it
(Chapter 5.7, section 3). A list started at that blanking draws the next picture, which the next
blanking puts on the screen: a patch sent in frame k is on the screen in frame k + 2. The model below
(Sched) does exactly this; the frames are what host/gpu_model.py says (Gpu.screen() after Gpu.vblank()).

The four frames of the HUD window (k=1 to 4) carry 0, 7, 14 and 7 bytes: one WRITE_LIST_WORD is 7 bytes.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
import ref_layers as rl  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END, LINE = rl.OPEN, rl.CLOSE, rl.FRAME_START, rl.ARM, rl.FENCE, rl.END, rl.LINE
WIN_START, WIN_END = 0x5000, 0x5001
PATCH_LINE = int(os.environ.get("HUD_PATCH_LINE", "20"))    # in the visible part, while the reference's run is still drawing (legal: about 10 to 400)
LABELS = ["f%x" % i for i in range(11)]
N_FRAMES = 11

from hud_demo import DIGIT_X, DIGIT_Y, MARKER_ID, PALETTE, digit_patch, glyph, list0, marker  # noqa: E402  the HUD of the demo is the test's list 0


# ---------------------------------------------------------------- the lists
def list1():
    """A second picture. Returns (the list, the same list with a stale FLIP behind it)."""
    b = g.clear(12) + g.fill_rect(40, 40, 279, 199, 8) + g.draw_line(40, 40, 279, 199, 7) + g.blit(MARKER_ID, 150, 100) + g.flip()
    return b, b + g.flip()


def list2():
    """Everything a list may not hold, between a few things it may. Ends in FLIP."""
    return (g.clear(1) + g.fill_rect(30, 30, 289, 209, 8)
            + g.run_list(3)                                                  # nested: skipped, BAD_OP
            + g.get_info() + g.fb_crc()                                      # replies: skipped
            + g.upload_asset(0, 8, 8, glyph(1))                                # uploads: skipped (it would turn digit 0's glyph into a 1)
            + g.upload_tiles(0, b"\x01\x02")
            + g.write_list_word(0, 0, 0x0707)                                # a list may not patch lists
            + g.define_list(0) + g.set_auto_list(g.AUTO_OFF)
            + g.clear_errors(0x0F)                                           # nor hide the flags
            + g.fill_rect(80, 80, 239, 159, 9) + g.flip())


def list3():
    return g.fill_rect(0, 0, 319, 239, 10)


def list5():
    return g.clear(11) + g.fill_rect(100, 60, 219, 179, 9) + g.flip()


# ---------------------------------------------------------------- the scheduler: what the GPU does, in what order
class Sched:
    """The command engine as the chapter defines it. Host packets and lists run to completion, one at a
    time; a FLIP stops everything until the next vertical blanking (tick), host bytes wait behind it, and
    at every blanking at which the automatic run is on, a run is due. Host bytes go first, then the automatic
    list. A run that is due stays due until it has started, unless SET_AUTO_LIST stops the automatic run
    first; a SET_AUTO_LIST that executes just after a blanking started does not make that blanking's run due."""

    def __init__(self, model):
        self.m, self.queue, self.stalled = model, [], False
        self.replies = bytearray()
        self.due = False
        self.armed, self.k, self.shots = False, -1, {}

    def host(self, data):
        i = 0
        while i < len(data):                       # whole packets only
            n = 2 + data[i + 1]
            self.queue.append(data[i:i + n])
            i += n
        self.pump()

    def pump(self):
        while self.queue and not self.stalled:
            before = self.m.flips
            self.replies += self.m.feed(self.queue.pop(0))
            if self.m.flips > before:
                self.stalled = True

    def tick(self):
        """The start of a video frame: the blanking just before it has put the last FLIP on the screen."""
        self.k += 1
        self.stalled = False
        self.m.vblank()
        if self.armed:
            self.shots[LABELS[len(self.shots)]] = self.m.screen()
        if self.m.auto is not None:                # the run is due if the automatic run is on as this blanking starts
            self.due = True
        self.pump()
        if self.m.auto is None:                    # SET_AUTO_LIST off cancels a run that is due
            self.due = False
        if self.due and not self.queue and not self.stalled:
            self.due = False
            before = self.m.flips
            self.m.auto_run()
            if self.m.flips > before:
                self.stalled = True


# ---------------------------------------------------------------- stream and frames
def build():
    """Returns (stream words, the replies, {label: screen index bytes}, model, the HUD window byte counts)."""
    m = g.Gpu()
    s = Sched(m)
    items, win = [], []

    def frame(data, line=None):
        if line is not None:
            items.append(LINE | line)
        items.extend([OPEN] + list(data) + [CLOSE])
        s.host(data)

    def start_frame():
        items.append(FRAME_START)
        s.tick()

    def fence(n):
        items.append(FENCE | n)

    l0, off = list0()
    l1, l1_old = list1()

    # the buffers: both cleared (the first FLIP waits for blanking, so the story starts at a frame start)
    frame(g.clear(0) + g.flip() + g.clear(0))
    start_frame()
    frame(b"".join(g.set_palette(i, c) for i, c in PALETTE.items()))
    for d in range(10):
        for p in g.upload_packets(d, 8, 8, glyph(d)):
            frame(p)
    for p in g.upload_packets(MARKER_ID, 8, 8, marker()):
        frame(p)
    # the lists. List 1 is first written long, then defined again shorter: the stale bytes stay behind its end.
    for lid, data in ((0, l0), (1, l1_old), (1, l1), (2, list2()), (3, list3()), (5, list5())):
        for pk in g.list_packets(lid, data):
            frame(pk)
    big = g.define_list(4) + g.append_list(4, g.nop(bytes(252))) + g.append_list(4, g.nop(bytes(252))) + g.append_list(4, g.fill_rect(0, 0, 9, 9, 3))
    frame(big)                                                  # 508 bytes, then 4 of the FILL_RECT's 11 fit; the rest is dropped
    frame(g.write_list_word(4, 511, 0x0700))                    # byte 511 of list 4 (x0's high byte, 00 again); the 07 falls off the slot:
                                                                # into list 4's byte 0 or list 5's it would be a FLIP of the wrong length
    assert m.list_len[4] == 512 and m.list_len[5] == len(list5())
    # packets that change nothing and set no flag
    frame(g.define_list(8) + g.append_list(9, g.fill_rect(0, 0, 5, 5, 1)) + g.run_list(200) + g.run_list(7)
          + g.write_list_word(8, 0, 0x0102) + g.write_list_word(0, 600, 0x0707) + g.write_list_word(1, 512, 0x0707)
          + g.write_list_word(4, 0xFFFF, 0x0707) + g.set_auto_list(8) + g.set_auto_list(100) + g.set_auto_list(254)
          + g.append_list(3, b""))
    frame(g.get_info())
    fence(9)
    # packets that must set flags: wrong lengths of the five commands, and the reserved opcodes
    frame(g.packet(g.DEFINE_LIST, b"") + g.packet(g.DEFINE_LIST, bytes(2)) + g.packet(g.APPEND_LIST, b"") + g.packet(g.RUN_LIST, b"")
          + g.packet(g.RUN_LIST, bytes([1, 1])) + g.packet(g.WRITE_LIST_WORD, bytes(4)) + g.packet(g.WRITE_LIST_WORD, bytes(6))
          + g.packet(g.SET_AUTO_LIST, b"") + g.packet(g.SET_AUTO_LIST, bytes(2)) + g.packet(0x55, bytes(3)) + g.packet(0x5F, b""))
    frame(g.get_info())
    fence(9)
    frame(g.clear_errors(0x0F))
    assert m.list_len[0] == len(l0) and m.list_len[1] == len(l1) and m.auto is None and m.errors == 0

    # ---- the story
    start_frame()                                              # k = 0
    items += [LINE | PATCH_LINE, ARM]
    s.armed = True
    frame(g.set_auto_list(0))
    start_frame()                                              # k = 1: the HUD window starts
    items += [WIN_START]
    win_mark = [0]

    def window_frame():
        n = sum(2 + 5 for _ in range(win_mark[0]))
        win.append(n)

    def patches(*pks):
        win_mark[0] = len(pks)
        if pks:
            items.append(LINE | PATCH_LINE)
        for pk in pks:
            frame(pk)
        window_frame()

    patches()
    start_frame()                                              # k = 2
    patches(digit_patch(off, 3, 1))
    start_frame()                                              # k = 3
    patches(digit_patch(off, 2, 1), digit_patch(off, 3, 0))
    start_frame()                                              # k = 4
    patches(g.write_list_word(0, off["marker"] + 3, 200))
    start_frame()                                              # k = 5
    items.append(WIN_END)
    frame(g.set_auto_list(1), PATCH_LINE)
    start_frame()                                              # k = 6
    frame(g.write_list_word(1, 2, 13 | (0x05 << 8)) + digit_patch(off, 1, 5), PATCH_LINE)   # list 1's CLEAR color, and the next opcode (FILL_RECT, 05) rewritten with itself
    start_frame()                                              # k = 7
    frame(g.set_auto_list(g.AUTO_OFF) + g.write_list_word(1, 2, 6 | (0x05 << 8)), PATCH_LINE)
    start_frame()                                              # k = 8
    frame(g.run_list(2), PATCH_LINE)
    start_frame()                                              # k = 9
    frame(g.set_auto_list(0) + g.run_list(4) + g.run_list(5), PATCH_LINE)
    start_frame()                                              # k = 10
    frame(g.get_info(), PATCH_LINE)
    fence(9)
    start_frame()                                              # k = 11: the last frame
    items.append(LINE | PATCH_LINE)
    assert len(s.shots) == N_FRAMES, len(s.shots)

    # sanity: what each patch changed, and nothing else
    def diff(a, b):
        ys, xs = np.nonzero(np.frombuffer(a, np.uint8).reshape(g.H, g.W) != np.frombuffer(b, np.uint8).reshape(g.H, g.W))
        return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None

    sh = [s.shots[l] for l in LABELS]
    assert not any(sh[0]), "frame 1 is empty"
    assert diff(sh[1], sh[2]) is None, "no patch, same picture"
    box = diff(sh[2], sh[3])
    assert box and box[0] >= DIGIT_X[3] and box[2] < DIGIT_X[3] + 8 and box[1] >= DIGIT_Y and box[3] < DIGIT_Y + 8, box
    box = diff(sh[3], sh[4])
    assert box and box[0] >= DIGIT_X[2] and box[2] < DIGIT_X[3] + 8 and box[1] >= DIGIT_Y and box[3] < DIGIT_Y + 8, box
    box = diff(sh[4], sh[5])
    assert box and box[0] >= 100 and box[1] >= 150 and box[3] < 158 and box[2] < 208, box     # the marker moved: old and new place
    assert len({bytes(x) for x in sh}) >= 7
    return items, bytes(s.replies), s.shots, m, win


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--stream":
        items, info, shots, m, win = build()
        want = bytes([0x47, 1, 0xFF, 0x03, 0x40, 0x01, 0xF0, 0x00, 0x00,
                      0x47, 1, 0xFF, 0x03, 0x40, 0x01, 0xF0, 0x00, 0x06,
                      0x47, 1, 0xFF, 0x03, 0x40, 0x01, 0xF0, 0x00, 0x0C])
        assert info == want, "unexpected GET_INFO replies: " + info.hex()
        assert win == [0, 7, 14, 7], win
        items += [END]
        with open(os.path.join(args[1], "display_lists_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(args[1], "display_lists_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in info))
        with open(os.path.join(args[1], "display_lists_bytes.hex"), "w") as f:
            f.write("".join("%04x\n" % n for n in win))
        print("wrote %s/display_lists_stream.hex (%d entries), display_lists_replies.hex and display_lists_bytes.hex; HUD window bytes per frame %s" %
              (args[1], len(items), win))
        return 0
    if len(args) == 1 and not args[0].startswith("-"):
        items, info, shots, m, win = build()
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for label, idx in shots.items():
            small = pal[np.frombuffer(idx, dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_display_lists_%s.ppm" % label), big, 255)
        print("wrote %d reference frames to %s" % (len(shots), args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
