#!/usr/bin/env python3
"""ref_fuzz.py -- test stream and expected results for tb_gpu_fuzz (Chapter 5.2, tile-layer commands added in Chapter 5.3, sprite commands in Chapter 5.4, compositor commands in Chapter 5.5).

Usage (from the repo root, once; commit the six files it writes):
    .venv/bin/python sim/py/ref_fuzz.py sim [--seed N] [--rounds N]

Writes:
    sim/fuzz_stream.hex    what the testbench sends, one entry per line:
                             00NN  one byte, inside the open frame
                             A000  open a frame: wait for READY, then CS falls
                             A001  close the frame: CS rises
                             A10K  K = 1-7: clock K more bits of random data,
                                   then CS rises (a frame cut inside a byte)
                             A003  a read frame (0xFF first): reads replies
                             B0NN  one byte on the UART (CS high)
                             1NNN  fence: poll with read frames until NNN (hex)
                                   more reply bytes have arrived
                             FFFF  end of the stream
    sim/fuzz_replies.hex   every reply byte the GPU must send, in order
    sim/fuzz_labels.txt    one line per fence: what the round did
    sim/fuzz_fb0.hex, fuzz_fb1.hex, fuzz_pal.hex   the end state (both buffers, the palette)

The stream is a fuzz test of the parser. Every round sends garbage in frames
(random bytes, headers that promise more payload than they deliver, mutated
valid packets, unknown opcodes, wrong lengths, frames cut inside a byte, empty
frames), each closed by a CS rise, sometimes UART bytes between the frames, and
then a frame of valid commands that ends with GET_INFO and sometimes FB_CRC. The expected replies, framebuffers and
palette come from host/gpu_model.py, fed frame by frame with sof=True at each
frame's first byte and at the first UART byte after SPI bytes, so the garbage's effects (some of it is, by chance, a
valid command) are part of the expectation. A round is thrown away and made
again if the garbage would make BLIT read asset bytes no upload has written,
because the GPU's asset RAM is undefined there.
"""
import argparse
import copy
import os
import random
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "host"))   # gpu_model, raster_model

import gpu_model as g  # noqa: E402
from ref_scanout import image, palette  # noqa: E402

OPEN, CLOSE, CUT, POLL, FENCE, END, UART = 0xA000, 0xA001, 0xA100, 0xA003, 0x1000, 0xFFFF, 0xB000


class Stream:
    def __init__(self, model, rng):
        self.m, self.rng = model, rng
        self.items, self.replies, self.labels = [], bytearray(), []
        self.nbytes = self.nframes = 0
        self.round_replies = 0
        self.last_spi = False            # the last byte written to the FIFO came from SPI

    def frame(self, data, end="close"):
        """One frame: open, bytes, then close ("close") or cut inside a byte ("cut")."""
        data = bytes(data)
        assert len(data) <= 257
        self.items.append(OPEN)
        self.items += list(data)
        self.items.append(CLOSE if end == "close" else CUT + self.rng.randint(1, 7))
        if data:
            self.last_spi = True
            r = self.m.feed(data, sof=True)
            self.replies += r
            self.round_replies += len(r)
        self.nbytes += len(data)
        self.nframes += 1

    def uart(self, data):
        """Bytes on the UART between frames. The first byte after SPI bytes starts
        a packet (link change). Reply-free only: replies would go to the UART."""
        data = bytes(data)
        self.items += [UART + b for b in data]
        assert self.m.feed(data, sof=self.last_spi) == b""
        self.last_spi = False
        self.nbytes += len(data)

    def poll(self):
        self.items.append(POLL)

    def fence(self, label):
        assert self.round_replies < 4096
        self.items.append(FENCE + self.round_replies)
        self.labels.append(label)
        self.round_replies = 0


def good_assets(m):
    """Asset ids whose every byte has been written by an upload."""
    return [a for a, (base, w, h) in m.assets.items() if all(m.ram_written[base:base + w * h])]


def valid_packet(m, rng, allow_clear=True):
    """One random valid command (or occasionally a NOP / error-flag clear)."""
    k = rng.randint(0, 99)
    c = rng.randint(0, 255)
    if k < 8:
        return g.set_palette(rng.randint(0, 255), rng.getrandbits(24))
    if k < 24:
        return g.plot(rng.randint(-5, 325), rng.randint(-5, 245), c)
    if k < 38:
        x0, y0 = rng.randint(-20, 330), rng.randint(-20, 250)
        return g.fill_rect(x0, y0, x0 + rng.randint(-60, 60), y0 + rng.randint(-40, 40), c)
    if k < 52:
        return g.draw_line(rng.randint(-20, 340), rng.randint(-20, 260), rng.randint(-20, 340), rng.randint(-20, 260), c)
    if k < 60:
        x, y = rng.randint(-10, 320), rng.randint(-10, 240)
        return g.draw_tri(x, y, x + rng.randint(-40, 40), y + rng.randint(-40, 40), x + rng.randint(-40, 40), y + rng.randint(-40, 40), c)
    if k < 72:
        ids = good_assets(m)
        if ids:
            return g.blit(rng.choice(ids), rng.randint(-20, 330), rng.randint(-20, 250), rng.randint(0, 7))
    if k < 82:
        w, h = rng.randint(1, 12), rng.randint(1, 12)
        return g.upload_asset(rng.randint(0, 15), w, h, bytes(rng.choice([0, 0, rng.randint(1, 255)]) for _ in range(w * h)))
    if k < 84:
        return g.asset_reset()
    if k < 86 and allow_clear:
        return g.clear(c)
    if k < 88:
        return g.flip()
    if k < 91:
        r = rng.randint(0, 6)                                  # NOP, a tile-layer command (Chapter 5.3) or a compositor command (5.5)
        if r == 0:
            return g.nop(bytes(rng.randint(0, 255) for _ in range(rng.randint(0, 12))))
        if r == 1:
            return g.set_tile_layer(rng.randint(0, 3))
        if r == 2:
            return g.set_scroll(rng.randint(0, 2), rng.randint(-600, 600), rng.randint(-600, 600))
        if r == 3:
            return g.upload_tiles(rng.choice([0, 100, 16000, 16380, 65535]), bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 30))))
        if r == 5:
            return g.packet(rng.choice([g.SET_PRIORITY, g.SET_BACKDROP, g.FB_ENABLE]), bytes([rng.getrandbits(8)]))
        if r == 6:
            return g.set_priority(*[rng.randint(0, 3) for _ in range(4)])
        return g.write_tile_map(rng.randint(0, 2), rng.choice([0, 63, 4090, 4095, 60000]), [rng.getrandbits(16) for _ in range(rng.randint(0, 6))])
    if k < 94:
        return g.clear_errors(rng.choice([0x0F, 0x0E, 0x06, 0x08, 0x01, 0x02, 0x04]))
    if k < 97:
        return g.get_info()
    return g.fb_crc()


def valid_frame(m, rng, limit=200):
    """A frame of whole valid packets, at most `limit` bytes, ending with GET_INFO (and sometimes FB_CRC)."""
    out = bytearray()
    for _ in range(rng.randint(1, 7)):
        p = valid_packet(m, rng)
        if len(out) + len(p) > limit:
            break
        out += p
    out += g.get_info()
    if rng.random() < 0.35:
        out += g.fb_crc()
    return bytes(out)


def rand_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


def garbage(m, rng):
    """One frame of garbage, 1-257 bytes, never starting with 0xFF (that would be a read frame)."""
    kind = rng.randint(0, 6)
    ops = list(range(0, 0x0D)) + [0x0D, 0x10, 0x12, 0x13, 0x14, 0x20, 0x30, 0x32, 0x35, 0x40, 0x52, 0x66, 0x7F, 0x80, 0xA5, 0xFE]
    if kind == 0:
        d = rand_bytes(rng, rng.randint(1, 257))
    elif kind == 1:                                        # a header that promises more than it delivers
        op = rng.choice(ops)
        ln = rng.choice([255, 255, 200, 100, 13, 9, 6, 5])
        d = bytes([op, ln]) + rand_bytes(rng, rng.randint(0, min(ln, 60) - 1 if ln > 1 else 0))
    elif kind == 2:                                        # mutated valid packets
        d = bytearray()
        for _ in range(rng.randint(1, 4)):
            d += valid_packet(m, rng, allow_clear=False)
        for _ in range(rng.randint(1, 3)):
            r = rng.randint(0, 3)
            if not d:
                break
            i = rng.randrange(len(d))
            if r == 0:
                d[i] ^= 1 << rng.randint(0, 7)
            elif r == 1:
                del d[i]
            elif r == 2:
                d.insert(i, rng.getrandbits(8))
            else:
                del d[i:]
        d = bytes(d[:257])
    elif kind == 3:                                        # a valid packet cut short
        p = valid_packet(m, rng, allow_clear=False)
        d = p[:rng.randint(1, max(1, len(p) - 1))]
    elif kind == 4:                                        # wrong length for a known opcode
        op = rng.choice(list(range(0, 0x0D)) + [0x10, 0x11, 0x12, 0x13, 0x20, 0x21, 0x22, 0x30, 0x31, 0x32])
        ln = rng.choice([x for x in range(0, 20) if g.LENGTHS.get(op, None) != x])
        d = g.packet(op if op != 0 else 1, rand_bytes(rng, ln))
    elif kind == 5:                                        # unknown or reserved opcode, complete
        d = g.packet(rng.choice([0x0D, 0x0E, 0x17, 0x27, 0x33, 0x3F, 0x41, 0x58, 0x70, 0x81, 0xC3, 0xFE]), rand_bytes(rng, rng.randint(0, 40)))
    else:                                                  # a long UPLOAD or BLIT claim, cut
        d = bytes([rng.choice([0x09, 0x0A, 0x03]), 255]) + rand_bytes(rng, rng.randint(0, 40))
    if not d:
        d = b"\x00"
    if d[0] == 0xFF:
        d = bytes([0x7E]) + d[1:]
    return d[:257]


def wrong_len(op, ln):
    """True if a packet of opcode op with payload length ln is a BAD_LEN packet."""
    if op == g.UPLOAD_ASSET:
        return ln < g.UPLOAD_HEADER
    if op == g.UPLOAD_TILES:
        return ln < 2
    if op == g.WRITE_TILE_MAP:
        return ln < 3 or ln % 2 == 0
    return ln != g.LENGTHS[op]


def build(model, rng, rounds):
    s = Stream(model, rng)
    m = model

    def plain(label, frames):
        """A scripted round: (data, end) frames, then the valid check frame."""
        for f in frames:
            s.frame(*f) if isinstance(f, tuple) else s.frame(f)
        s.frame(g.get_info() + g.fb_crc())
        s.fence(label)

    # ---- scripted rounds ----
    plain("fresh GPU: GET_INFO and FB_CRC", [])
    p = g.plot(10, 10, 200)
    for cut in range(1, len(p)):
        plain(f"PLOT cut after {cut} of {len(p)} bytes, then a valid PLOT", [p[:cut], g.plot(11 + cut, 10, 100 + cut)])
    f = g.fill_rect(5, 5, 60, 40, 77)
    for cut in (1, 2, 5, 10, len(f) - 1):
        plain(f"FILL_RECT cut after {cut} bytes", [f[:cut]])
    plain("unknown opcodes and wrong lengths", [g.packet(0x81, b"\x01\x02"), g.packet(g.PLOT, b"\x00"), g.packet(0x17), g.packet(g.CLEAR, b"")])
    # every unknown opcode once, and every known one with each wrong length 0-15, in frames of whole packets
    unk = b"".join(g.packet(op, rand_bytes(rng, rng.randint(0, 3))) for op in range(0x0D, 0x100) if not (0x10 <= op <= 0x13 or 0x20 <= op <= 0x22 or 0x30 <= op <= 0x32))
    bad = b"".join(g.packet(op, rand_bytes(rng, ln)) for op in list(range(1, 0x0D)) + [0x10, 0x11, 0x12, 0x13, 0x20, 0x21, 0x22, 0x30, 0x31, 0x32] for ln in range(16)
                   if wrong_len(op, ln))
    for name, data in (("every unknown opcode", unk), ("every known opcode with wrong lengths", bad)):
        frames, cur, i = [], b"", 0
        while i < len(data):                       # split at packet boundaries, at most 257 bytes a frame
            n = 2 + data[i + 1]
            if len(cur) + n > 257:
                frames.append(cur)
                cur = g.nop() if data[i] == 0xFF else b""   # a frame must not start with 0xFF
            cur += data[i:i + n]
            i += n
        plain(name, frames + [cur])
    # Chapter 5.3: each tile-command length rule on its own, after a flag clear, so that
    # GET_INFO shows exactly the flag this one packet sets (other rounds set BAD_LEN anyway)
    for op, ln in ((g.UPLOAD_TILES, 1), (g.WRITE_TILE_MAP, 2), (g.WRITE_TILE_MAP, 4), (g.WRITE_TILE_MAP, 8),
                   (g.SET_SCROLL, 6), (g.SET_TILE_LAYER, 0)):
        plain(f"opcode {op:02X} with wrong length {ln} alone", [g.clear_errors(0x0F) + g.packet(op, rand_bytes(rng, ln))])
    # Chapter 5.4: the same for the sprite commands
    for op, ln in ((g.SET_SPRITE, 8), (g.SET_SPRITE, 10), (g.SET_SPRITE, 0), (g.MOVE_SPRITE, 4), (g.MOVE_SPRITE, 6), (g.SPRITE_CTRL, 0), (g.SPRITE_CTRL, 2)):
        plain(f"opcode {op:02X} with wrong length {ln} alone", [g.clear_errors(0x0F) + g.packet(op, rand_bytes(rng, ln))])
    # Chapter 5.5: the same for the compositor commands
    for op, ln in ((g.SET_PRIORITY, 0), (g.SET_PRIORITY, 2), (g.SET_BACKDROP, 0), (g.SET_BACKDROP, 3), (g.FB_ENABLE, 0), (g.FB_ENABLE, 2)):
        plain(f"opcode {op:02X} with wrong length {ln} alone", [g.clear_errors(0x0F) + g.packet(op, rand_bytes(rng, ln))])
    plain("compositor commands with legal lengths and any argument set no flag",
          [g.clear_errors(0x0F) + g.packet(g.SET_PRIORITY, bytes([0x00])) + g.packet(g.SET_PRIORITY, bytes([0xFF])) + g.set_backdrop(255)
           + g.packet(g.FB_ENABLE, bytes([0xFE])) + g.fb_enable(True) + g.set_priority()])
    plain("sprite commands with legal lengths and ignored arguments set no flag",
          [g.clear_errors(0x0F) + g.set_sprite(64, 1, 2, 3) + g.move_sprite(255, 1, 2) + g.set_sprite(63, 1, 2, 15, 255, 255)
           + g.packet(g.SPRITE_CTRL, bytes([0xFE])) + g.sprite_ctrl(True) + g.move_sprite(0, -1, -1)])
    plain("tile commands with legal lengths and ignored arguments set no flag",
          [g.clear_errors(0x0F) + g.upload_tiles(16383, b"\x01\x02") + g.write_tile_map(2, 0, [1]) + g.write_tile_map(0, 4095, [1, 2])
           + g.set_scroll(5, 1, 1) + g.upload_tiles(0, b"") + g.write_tile_map(1, 0, [])])
    plain("clear all flags", [g.clear_errors(0x0F)])
    plain("a length byte of 255 and nothing behind it", [bytes([g.PLOT, 255]) + bytes(20), bytes([g.UPLOAD_ASSET, 255, 1, 2, 3, 0, 0])])
    plain("empty frames and a frame cut inside a byte", [(b"", "close"), (g.plot(1, 2, 3), "cut"), (b"", "cut"), (g.plot(2, 2, 4)[:3], "cut")])
    plain("CLEAR then a frame that ends mid-packet", [g.clear(9) + g.plot(5, 5, 1)[:4]])
    pk = g.upload_asset(3, 9, 9, bytes(range(1, 82)))
    plain("UPLOAD_ASSET cut inside its data, then the whole image", [pk[:2 + 5 + 30], b"".join(g.upload_packets(4, 9, 9, bytes(range(1, 82)))) + g.blit(4, 100, 100, 4)])
    # the UART: a link change starts a packet; frames that write nothing do not
    s.frame(g.plot(30, 30, 5)[:4])
    s.uart(g.plot(31, 30, 6))
    plain("SPI frame cut inside a PLOT, then a PLOT on the UART", [])
    s.frame(g.plot(32, 30, 7))
    s.uart(g.plot(33, 30, 8) + g.fill_rect(40, 40, 44, 44, 9))
    plain("whole SPI frame, then UART packets", [])
    s.uart(g.plot(34, 30, 10)[:3])
    plain("UART cut inside a PLOT, then an SPI frame", [])
    q = g.plot(35, 30, 11)
    s.uart(q[:3])
    s.poll()
    s.frame(b"")
    s.uart(q[3:])
    plain("UART PLOT with a read frame and an empty frame in the middle", [])
    plain("clear all flags again", [g.clear_errors(0x0F)])
    # ---- random rounds ----
    for r in range(rounds):
        saved = (copy.deepcopy(m), len(s.items), len(s.replies), len(s.labels), s.nbytes, s.nframes, s.last_spi)
        for attempt in range(50):
            s.round_replies = 0
            unwritten = m.blit_unwritten
            ng = rng.choice([0, 1, 1, 1, 2, 2, 3])
            kinds = []
            for _ in range(ng):
                x = rng.random()
                if x < 0.12:
                    s.frame(b"")
                    kinds.append("empty")
                elif x < 0.22:
                    s.poll()
                    kinds.append("poll")
                elif x < 0.32 and s.round_replies == 0:       # no reply pending, so none can go to the UART
                    d = bytearray()
                    while len(d) < 40:
                        p = valid_packet(m, rng, allow_clear=False)
                        if p[0] not in (g.FB_CRC, g.GET_INFO):
                            d += p
                    if rng.random() < 0.4:
                        d = d[:rng.randint(1, len(d) - 1)]       # cut: the next SPI frame abandons it
                    s.uart(d)
                    kinds.append(f"uart{len(d)}")
                else:
                    d = garbage(m, rng)
                    end = "cut" if rng.random() < 0.3 else "close"
                    s.frame(d, end)
                    kinds.append(f"garbage{len(d)}{'c' if end == 'cut' else ''}")
            v = valid_frame(m, rng)
            s.frame(v)
            if m.blit_unwritten == unwritten and s.round_replies <= 40:
                s.fence(f"round {r}: {' '.join(kinds) or 'no garbage'}, valid frame of {len(v)} bytes")
                break
            m2, n_items, n_rep, n_lab, s.nbytes, s.nframes, s.last_spi = saved      # redo with other garbage
            m = s.m = copy.deepcopy(m2)
            del s.items[n_items:]
            del s.replies[n_rep:]
            del s.labels[n_lab:]
        else:
            raise SystemExit("could not make a round")
    s.items.append(END)
    return s, m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--seed", type=int, default=5252)
    ap.add_argument("--rounds", type=int, default=120)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    rng = random.Random(a.seed)
    pal0 = [(int(r) << 16) | (int(gg) << 8) | int(b) for r, gg, b in palette()]
    model = g.Gpu(fb=image().reshape(-1).tobytes(), pal=pal0)
    s, model = build(model, rng, a.rounds)
    assert len(s.items) < 262144, "stream too long for the testbench's array"

    def write(name, lines, comment):
        with open(os.path.join(a.outdir, name), "w") as f:
            f.write(f"// {comment} (made by sim/py/ref_fuzz.py)\n")
            f.write("\n".join(lines) + "\n")

    write("fuzz_stream.hex", [f"{v:04x}" for v in s.items],
          "tb_gpu_fuzz stream: 00NN byte, A000 open, A001 close, A10K cut after K bits, A003 poll, B0NN UART byte, 1NNN fence, FFFF end")
    write("fuzz_replies.hex", [f"{v:02x}" for v in s.replies], "expected reply bytes, in order")
    write("fuzz_fb0.hex", [f"{v:02x}" for v in model.bufs[0]], "expected framebuffer 0, address y*320+x")
    write("fuzz_fb1.hex", [f"{v:02x}" for v in model.bufs[1]], "expected framebuffer 1, address y*320+x")
    write("fuzz_pal.hex", [f"{v:06x}" for v in model.pal], "expected palette, RRGGBB")
    with open(os.path.join(a.outdir, "fuzz_labels.txt"), "w") as f:
        f.write("".join(f"{lab[:120]}\n" for lab in s.labels))
    print(f"ref_fuzz: {len(s.items)} stream entries, {s.nbytes} bytes in {s.nframes} frames, "
          f"{len(s.labels)} fences, {len(s.replies)} reply bytes, {model.ignored} ignored packets, "
          f"{model.flips} FLIPs, {len(model.assets)} assets -> {a.outdir}/fuzz_*")


if __name__ == "__main__":
    main()
