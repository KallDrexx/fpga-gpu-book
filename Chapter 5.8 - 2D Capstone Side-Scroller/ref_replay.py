#!/usr/bin/env python3
"""ref_replay.py -- replay a side_scroller.py log in the simulator: the stream and the reference frames (Chapter 5.8).

Usage (from the repo root):
    .venv/bin/python host/side_scroller.py --model --log sim/side_scroller.log       once: the log
    .venv/bin/python sim/py/ref_replay.py --stream sim                                once: writes the stream the testbench plays
    .venv/bin/python sim/py/ref_replay.py --choose                                    prints the window and why it was chosen
    .venv/bin/python sim/py/ref_replay.py OUTDIR                                      writes OUTDIR/ref_replay_f0.ppm ... f5.ppm

The log is the file side_scroller.py --log wrote (default sim/side_scroller.log, or $REPLAY_LOG). It holds the setup
and every frame's bytes, exactly as the host put them on the link (see that script for the format). Playing
all of it in the simulator would take hours, so the test replays a WINDOW of $REPLAY_COUNT (default 6) video frames
that starts at video frame $REPLAY_START. Without REPLAY_START the window is the earliest one in which all of this
happens (choose_window below):
    - a map column and a map row are written (the near layer is streamed),
    - the HUD list is patched (a coin was collected: the score changes),
    - some frame shows 20 or more sprites (the table, at the start of its blanking, holds that many on the screen), and
    - some frame has a scan line with more than 16 sprites on it (the sprite engine must drop the extras),
and the two tile layers scroll at different speeds in every frame.

--stream writes (commit all three files):
    sim/replay_stream.hex   what the testbench sends, one 16-bit hex entry per line; the entries of
                            sim/display_lists_stream.hex (Chapter 5.7), and 6NNN: the window has NNN frames
                            1. the setup, one A000 ... A001 per logged S line, a fence per logged Q line
                            2. the prefix: frames 0 to start - 2 as logged, CS frame by CS frame, with only
                               their RUN_LIST packets taken out (no waiting for the beam)
                            3. 3000 (a video frame starts), frame start - 1 as logged: the warm-up; its RUN_LIST
                               draws the picture the first dumped frame shows
                            4. 4000 (arm the dumpers), 3000, 5000 (count bytes), then frames start .. start + N - 1,
                               each after its own 3000, as logged; the dumpers write them as f0 ...
                            5. 3000 (f(N-1) completes), 5001, a GET_INFO frame and a fence for its 9 bytes, FFFF
    sim/replay_replies.hex  the reply bytes the fences read, from the model
    sim/replay_bytes.hex    the bytes of each window frame, from the F lines of the log

Why the prefix may drop the RUN_LISTs. A RUN_LIST of the HUD list ends in FLIP, which waits for a blanking:
kept, the frames before the window would have to be simulated in real time (hours). Everything else in those
frames is sent, in the logged order. The RUN_LISTs only draw the framebuffer, and the next run redraws all of it, so
leaving them out changes nothing the window shows. play() checks that before the stream is written: every RUN_LIST
of the log must run a list that starts with CLEAR and holds only framebuffer drawing and FLIP.

The expected frames are not made from the stream. They come from the whole log, played frame by frame through
host/gpu_model.py: gpu.vblank(), the frame's screen (that is what dumper f0 sees), then the frame's bytes.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model

import numpy as np  # noqa: E402

import gpu_model as g  # noqa: E402
from ppm import write_ppm  # noqa: E402

OPEN, CLOSE, FRAME_START, ARM, FENCE, END = 0xA000, 0xA001, 0x3000, 0x4000, 0x1000, 0xFFFF
WIN_START, WIN_END, DECLARE = 0x5000, 0x5001, 0x6000
LOG = os.environ.get("REPLAY_LOG", os.path.join("sim", "side_scroller.log"))
COUNT = int(os.environ.get("REPLAY_COUNT", "6"))
BUDGET = 1024


# ---------------------------------------------------------------- the log
def parse_log(path):
    """Returns (setup, frames): setup is a list of ("S", bytes) and ("Q", n); frames[k] a list of bytes (its CS frames)."""
    setup, frames, cur, want, ended = [], [], None, 0, False
    with open(path) as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            kind, _, rest = line.partition(" ")
            if ended:
                continue                           # the trailer after E: not part of any frame
            if kind == "E":
                ended = True
            elif kind == "F":
                k, n = (int(v) for v in rest.split())
                if cur is not None:
                    assert sum(len(c) for c in cur) == want, "log line %d: frame %d has %d bytes, its F line says %d" % (ln, len(frames) - 1, sum(len(c) for c in cur), want)
                assert k == len(frames), "log line %d: frame number %d, expected %d" % (ln, k, len(frames))
                cur, want = [], n
                frames.append(cur)
            elif kind == "S":
                data = bytes.fromhex(rest)
                if cur is None:
                    setup.append(("S", data))
                else:
                    cur.append(data)
            elif kind == "Q":
                assert cur is None, "log line %d: a reply read inside the frames" % ln
                setup.append(("Q", int(rest)))
            else:
                raise ValueError("log line %d: unknown record %r" % (ln, kind))
    assert cur is not None and sum(len(c) for c in cur) == want, "the log ends inside a frame"
    assert ended, "the log has no E record: it was cut short"
    return setup, frames


def packets(data):
    """Split bytes into whole packets (op, payload)."""
    i, out = 0, []
    while i < len(data):
        n = data[i + 1]
        out.append((data[i], data[i + 2:i + 2 + n]))
        i += 2 + n
    assert i == len(data), "a packet runs past the end of its frame"
    return out


# ---------------------------------------------------------------- the prefix
DRAWING = {g.NOP, g.CLEAR, g.PLOT, g.FILL_RECT, g.DRAW_LINE, g.DRAW_TRI, g.BLIT, g.FLIP}


def redraws_all(m, lid):
    """True if list lid, as the model holds it now, only draws: CLEAR first, then drawing commands and FLIP.
    Such a run leaves nothing behind that the next run of it does not overwrite."""
    try:
        ops = [op for op, _ in packets(bytes(m.lists[lid][:m.list_len[lid]]))]
    except AssertionError:                     # the list ends inside a packet
        return False
    return bool(ops) and ops[0] == g.CLEAR and set(ops) <= DRAWING


def prefix(frames):
    """The logged CS frames of `frames` with the RUN_LIST packets taken out (a CS frame left empty is not sent)."""
    out = []
    for fr in frames:
        for c in fr:
            kept = b"".join(bytes([op, len(p)]) + p for op, p in packets(c) if op != g.RUN_LIST)
            if kept:
                out.append(kept)
    return out


# ---------------------------------------------------------------- the model, played over the whole log
def sprite_stats(m):
    """(sprites on the screen, most sprites on one scan line) of the live table."""
    on, lines = 0, [0] * g.H
    for i in range(g.N_SPRITES):
        x, y, asset, pal, flags, _ = g.struct.unpack("<hhBBBB", m.live_sprites[i * 8:i * 8 + 8])
        if flags & g.SPRITE_ON and m.live_sp_enable:
            if -16 < x < g.W and -16 < y < g.H:
                on += 1
            for ln in range(max(y, 0), min(y + 16, g.H)):
                lines[ln] += 1
    return on, max(lines)


def play(setup, frames, want=()):
    """Plays the log through the model. Returns (replies, per-frame info, {k: screen for k in want}, the model).
    info[k] = dict(on=, line=, col=, row=, patch=): what frame k's table shows and what its batch wrote."""
    m = g.Gpu()
    replies, info, shots = bytearray(), [], {}
    for kind, v in setup:
        if kind == "S":
            replies += m.feed(v, sof=True)
    for k, fr in enumerate(frames):
        m.vblank()                                  # video frame k begins
        on, line = sprite_stats(m)
        ops = [op for op, _ in packets(b"".join(fr))]
        info.append(dict(on=on, line=line, col=ops.count(g.WRITE_TILE_COLUMN), row=ops.count(g.WRITE_TILE_ROW),
                         patch=ops.count(g.WRITE_LIST_WORD)))
        if k in want:
            shots[k] = m.screen()
        for c in fr:
            for op, p in packets(c):
                assert op != g.RUN_LIST or redraws_all(m, p[0]), \
                    "frame %d: RUN_LIST %d runs a list that does not just redraw the framebuffer" % (k, p[0])
            replies += m.feed(c, sof=True)
    return bytes(replies), info, shots, m


def choose_window(info, count):
    """The earliest start whose window shows what the chapter wants it to show (see the module docstring)."""
    for a in range(2, len(info) - count):
        shown = info[a:a + count]                   # frames displayed: a ... a + count - 1
        wrote = info[a - 1:a + count - 1]           # batches that show in them: a - 1 ... a + count - 2
        if (any(w["col"] for w in wrote) and any(w["row"] for w in wrote) and any(w["patch"] for w in wrote)
                and any(s["on"] >= 20 for s in shown) and any(s["line"] > g.MAX_PER_LINE for s in shown)):
            return a
    raise SystemExit("no window of %d frames in this log has a column, a row, a HUD patch, 20 sprites and a 17th sprite on a line" % count)


def window_start(info):
    if "REPLAY_START" in os.environ:
        return int(os.environ["REPLAY_START"])
    return choose_window(info, COUNT)


# ---------------------------------------------------------------- stream and frames
def build(setup, frames, start, count):
    """Returns (stream words, expected reply bytes, {k: screen}, model, bytes of each window frame)."""
    assert 2 <= start and start + count <= len(frames), "the window %d..%d is not inside the %d logged frames" % (start, start + count - 1, len(frames))
    assert count <= 16, "frame labels are one hex digit"
    all_replies, info, shots, m = play(setup, frames, want=range(start, start + count))

    items = [DECLARE | count]

    def cs(data):
        items.append(OPEN)
        items.extend(data)
        items.append(CLOSE)

    # 1. the setup, as logged. Replies: the Q records say how many bytes the host read.
    want_replies = bytearray()
    for kind, v in setup:
        if kind == "S":
            cs(v)
        else:
            items.append(FENCE | v)
    # the replies of the setup: all reply bytes the model made while playing the setup
    setup_reply = sum(v for kind, v in setup if kind == "Q")
    want_replies += all_replies[:setup_reply]
    assert len(all_replies) == setup_reply, "the game read %d reply bytes but the model produced %d" % (setup_reply, len(all_replies))

    # 2. the prefix: frames 0 .. start - 2 as logged, without their RUN_LISTs
    pre = prefix(frames[:start - 1])
    for c in pre:
        cs(c)

    # 3. the warm-up frame, as logged
    items.append(FRAME_START)
    for c in frames[start - 1]:
        cs(c)
    items.append(ARM)
    # 4. the window
    items.append(FRAME_START)
    items.append(WIN_START)
    win = []
    for k in range(start, start + count):
        if k > start:
            items.append(FRAME_START)
        for c in frames[k]:
            cs(c)
        win.append(sum(len(c) for c in frames[k]))
    # 5. the last frame start, the end of counting, the check of the error flags
    items.append(FRAME_START)
    items.append(WIN_END)
    cs(g.get_info())
    items.append(FENCE | 9)
    # the model's own answer: replay the end of the window in a model that has seen exactly the bytes the stream sends
    mm = g.Gpu()
    for kind, v in setup:
        if kind == "S":
            mm.feed(v, sof=True)
    for c in pre:
        mm.feed(c, sof=True)
    for k in range(start - 1, start + count):
        mm.vblank()
        for c in frames[k]:
            mm.feed(c, sof=True)
    want_replies += mm.feed(g.get_info(), sof=True)
    assert want_replies[-1] == 0, "the replayed window sets error flags in the model: %02X" % want_replies[-1]
    items.append(END)
    return items, bytes(want_replies), shots, m, win


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    setup, frames = parse_log(LOG)
    if args[0] == "--choose":
        _, info, _, _ = play(setup, frames)
        a = window_start(info)
        print("%d frames in the log; window of %d frames starts at video frame %d (warm-up frame %d)" % (len(frames), COUNT, a, a - 1))
        for k in range(a - 1, a + COUNT):
            print("  frame %3d: sprites on screen %2d, most on one line %2d, batch wrote %d column, %d row, %d patch packets; %d bytes" %
                  (k, info[k]["on"], info[k]["line"], info[k]["col"], info[k]["row"], info[k]["patch"], sum(len(c) for c in frames[k])))
        return 0
    _, info, _, _ = play(setup, frames)
    start = window_start(info)
    items, replies, shots, m, win = build(setup, frames, start, COUNT)
    assert all(n < BUDGET for n in win), "a window frame has %s bytes: over the budget" % win
    if args[0] == "--stream":
        out = args[1]
        with open(os.path.join(out, "replay_stream.hex"), "w") as f:
            f.write("".join("%04x\n" % w for w in items))
        with open(os.path.join(out, "replay_replies.hex"), "w") as f:
            f.write("".join("%02x\n" % b for b in replies))
        with open(os.path.join(out, "replay_bytes.hex"), "w") as f:
            f.write("".join("%04x\n" % n for n in win))
        print("wrote %s/replay_stream.hex (%d entries), replay_replies.hex (%d bytes) and replay_bytes.hex; window frames %d..%d carry %s bytes" %
              (out, len(items), len(replies), start, start + COUNT - 1, win))
        return 0
    if not args[0].startswith("-"):
        pal = np.array([[(c >> 16) & 255, (c >> 8) & 255, c & 255] for c in m.pal], dtype=np.uint16)
        for i, k in enumerate(range(start, start + COUNT)):
            small = pal[np.frombuffer(shots[k], dtype=np.uint8).reshape(g.H, g.W)]
            big = small.repeat(2, axis=0).repeat(2, axis=1)
            write_ppm(os.path.join(args[0], "ref_replay_f%x.ppm" % i), big, 255)
        print("wrote %d reference frames (log frames %d..%d) to %s" % (COUNT, start, start + COUNT - 1, args[0]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
