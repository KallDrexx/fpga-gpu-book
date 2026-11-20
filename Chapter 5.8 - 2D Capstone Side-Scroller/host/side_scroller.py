#!/usr/bin/env python3
"""side_scroller.py -- the Phase 5 capstone: a side-scroller driven over SPI in under 1 KB per frame (Chapter 5.8).

Usage:
    .venv/bin/python host/side_scroller.py --model [--frames 600] [--seed 1] [--log sim/side_scroller.log] [--ppm last.ppm]
    .venv/bin/python host/side_scroller.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 600] [--log FILE]

What is on the screen. Two tile layers scroll at different speeds (parallax): the near layer (layer 1) is the
streamed 256 x 96-tile level of Chapter 5.6, which moves 2 pixels a frame; the far layer (layer 0) is a small
periodic map of hills and clouds, written once, which moves at half that speed horizontally and an eighth
vertically. Twenty-six sprites run on top of both: the player, three enemies and a pool of 22 coin sprites that
shows one chain of coins at a time. The framebuffer is the HUD, in front of everything: a four-digit score and
a health bar. The HUD is one display list (list 0) that clears the back buffer, draws the panel, the digits and
the bar, and ends in FLIP; the host never draws.

What the host sends in every frame (Chapter 5.7, section 14, "driving the frame with RUN_LIST"), right after
VSYNC falls and in this order:
    1. WRITE_TILE_COLUMN / WRITE_TILE_ROW  the near layer's new column or row, if the camera exposed one
    2. MOVE_SPRITE / SET_SPRITE            only the sprites that moved (SET_SPRITE when an enemy turns round)
    3. SET_SCROLL x 2                      near layer, far layer
    4. WRITE_LIST_WORD                     the HUD digits and the health bar, only when they changed
    5. RUN_LIST 0                          always last: the list ends in FLIP, which waits for blanking
Everything sent in frame N is on the screen in frame N + 1. Nothing is sent after the RUN_LIST (the FLIP would
hold it, and READY, until blanking).

The game is deterministic: the same --seed gives the same bytes. Its only input is a scripted button (jump)
drawn from a small integer generator.

--model needs no board. It runs the host code against the Python model, one gpu.vblank() per frame, checks the
map, the sprite table, the HUD list and the error flags as it goes, and PASSes only if every frame sent less than
1024 bytes and every frame's batch, plus the HUD list's drawing, fits in the visible part of a 60 Hz frame at
the SPI clock given by --freq (and --cs-overhead-us per CS frame). --log writes every byte that
crossed the link (ref_replay.py turns that file into a simulation stream). Without --model the same loop drives
the board and waits for the VSYNC pin; it needs the Chapter 5.1 wiring and a bitstream with display lists.

Earlier files this one imports (all in host/): gpu_model.py (5.7), spi_test.py (5.2), layer_demo.py (5.3),
level_stream.py and stream_demo.py (5.6), hud_demo.py (5.7).

Log format (text, one record per line):
    # ...                 comment / header
    S <hex bytes>         one SPI command frame: one CS-low period, exactly the bytes on MOSI
    Q <n>                 the host then read n reply bytes (read frames themselves are not logged)
    F <k> <nbytes>        video frame k begins here (the host saw VSYNC fall); the S lines up to the next F
                          line belong to it and add up to nbytes. S and Q lines before the first F are the setup.
    E                     the last frame is over; what follows (the final GET_INFO) is not part of any frame
"""
import argparse
import hashlib
import math
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import hud_demo as hd  # noqa: E402  Chapter 5.7: the digit font, digit_patch()
import layer_demo as ld  # noqa: E402  Chapter 5.3: tile art, far map, colors
import spi_test  # noqa: E402  Chapter 5.2: SpiGpu, open_link
import stream_demo as sd  # noqa: E402  Chapter 5.6: the level, the camera, far_scroll
from level_stream import StreamedLayer  # noqa: E402  Chapter 5.6

BUDGET = 1024                  # bytes per frame the capstone must stay under
FRAME_MS = 800 * 525 / 25e3    # one video frame: 800 x 525 pixel clocks at 25 MHz = 16.8 ms
VISIBLE_MS = 800 * 480 / 25e3  # the visible part of it: 15.36 ms (blanking is the last 1.44 ms)
SYS_HZ = 50e6                  # command engine clock; drawing is about one pixel per clock

# --- the scene -------------------------------------------------------------------------------
HUD_LIST = 0
PLAYER_X = 40                  # the player stays at this screen x; the world scrolls past
PARK = 256                     # a parked sprite has y = 256: below the last line, so it is on no scan line
PLAYER, ENEMY0, N_ENEMIES, COIN0, N_COINS = 0, 1, 3, 4, 22
N_USED = COIN0 + N_COINS       # 26 sprites in the table
A_PLAYER, A_ENEMY, A_COIN = 10, 11, 12          # asset ids (0-9 are the digit glyphs)
PAL_PANEL, PAL_DIGIT, PAL_HP, PAL_HPBACK = 9, 10, 11, 12
PAL_BODY, PAL_SKIN, PAL_ENEMY, PAL_EYE, PAL_COIN, PAL_COIN2 = 13, 14, 15, 16, 17, 18
PALETTE = dict(ld.COLORS)
PALETTE.update({PAL_PANEL: 0x202030, PAL_DIGIT: 0xF0E040, PAL_HP: 0x30D050, PAL_HPBACK: 0x802020,
                PAL_BODY: 0xE05030, PAL_SKIN: 0xFFE0B0, PAL_ENEMY: 0x8030B0, PAL_EYE: 0xFFFFFF,
                PAL_COIN: 0xFFC800, PAL_COIN2: 0xB08000})
BAR_X, BAR_Y0, BAR_Y1, BAR_MAX, BAR_MIN = 66, 6, 13, 40, 5
CHAINS = [(360, 22, "flat"), (900, 22, "arc"), (1250, 14, "flat")]   # (world x of the first coin, coins, shape)
COIN_STEP = 12                 # pixels between coins: closer than a coin is wide, so a chain is dense
PRIORITY = dict(fb=3, l0=0, l1=1, sp=2)          # HUD in front, then sprites, near layer, far layer


class Lcg:
    """A small deterministic random source: the same numbers on every machine."""

    def __init__(self, seed):
        self.s = (seed * 2654435761 + 1) & 0x7FFFFFFF

    def next(self):
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s >> 8


# --- art -------------------------------------------------------------------------------------
def glyph(d):
    """Digit d as an 8 x 8 asset in the HUD color (Chapter 5.7's font)."""
    rows = ["0" + r + "00" for r in hd.FONT[d]] + ["0" * 8]
    return bytes(PAL_DIGIT if c == "1" else 0 for r in rows for c in r)


def player_img():
    return bytes(PAL_SKIN if (x - 7.5) ** 2 + (y - 4.5) ** 2 <= 12 else
                 PAL_BODY if 4 <= x <= 11 and 8 <= y <= 15 else 0 for y in range(16) for x in range(16))


def enemy_img():
    def px(x, y):
        if (x, y) in ((5, 7), (10, 7)):
            return PAL_EYE
        return PAL_ENEMY if ((x - 7.5) / 8) ** 2 + ((y - 9.5) / 6.5) ** 2 < 1 else 0
    return bytes(px(x, y) for y in range(16) for x in range(16))


def coin_img():
    def px(x, y):
        r = math.hypot(x - 7.5, y - 7.5)
        return 0 if r > 7 else PAL_COIN2 if 3.5 <= r <= 4.5 else PAL_COIN
    return bytes(px(x, y) for y in range(16) for x in range(16))


# --- the game --------------------------------------------------------------------------------
def chain_coin(ci, j):
    """World (x, y) of coin j of chain ci. A flat chain is one row: all its coins share every scan line they are on."""
    x0, n, shape = CHAINS[ci]
    base = sd.terrain((x0 + 6 * COIN_STEP) >> 3) * 8 - 22
    dy = -int(30 * math.sin(math.pi * j / (n - 1))) if shape == "arc" else 0
    return x0 + COIN_STEP * j, base + dy


class Batch:
    """One frame's packets, each tagged with what it is for (the report counts bytes by tag)."""

    def __init__(self):
        self.pk = []

    def add(self, tag, p):
        self.pk.append((tag, bytes(p)))

    def data(self):
        return b"".join(p for _, p in self.pk)

    def by_tag(self):
        out = {}
        for t, p in self.pk:
            out[t] = out.get(t, 0) + len(p)
        return out


class Game:
    """The host's whole memory of the game, and what it must send to change the picture. No I/O in here."""

    def __init__(self, seed=1):
        self.rng = Lcg(seed)
        self.seed = seed
        self.cam = sd.Camera()
        self.cam.y = min(max(sd.terrain(g.W // 16) * 8 - 150, 0), sd.MAX_SY)     # start where the ground is
        self.near = StreamedLayer(1, sd.level, sd.LEVEL_W, sd.LEVEL_H)
        self.py, self.vy, self.air = self.ground(self.cam.x + PLAYER_X), 0, False
        self.enemies = [[560 + 380 * i + self.rng.next() % 80, 1 - 2 * (i & 1)] for i in range(N_ENEMIES)]
        self.home = [e[0] for e in self.enemies]
        self.collected = [set() for _ in CHAINS]
        self.score, self.hp, self.cool = 0, BAR_MAX, 0
        self.cur = [None] * N_USED               # what the sprite table (as far as the host knows) holds: (x, y, flags)
        self.table = bytearray(g.N_SPRITES * g.SPRITE_BYTES)
        self.list_bytes, self.off = self.hud_list()
        self.shown_digits = [0, 0, 0, 0]
        self.shown_hp = BAR_MAX

    # ---- the HUD list ----
    def hud_list(self):
        """List 0: the whole HUD frame. Returns (bytes, {name: offset of the packet that a patch changes})."""
        b, off = bytearray(), {}
        b += g.clear(0)
        b += g.fill_rect(0, 0, 119, 19, PAL_PANEL)
        for i in range(4):
            off["digit%d" % i] = len(b)
            b += g.blit(0, hd.DIGIT_X[i], hd.DIGIT_Y)
        b += g.fill_rect(BAR_X, BAR_Y0, BAR_X + BAR_MAX - 1, BAR_Y1, PAL_HPBACK)
        off["bar"] = len(b)
        b += g.fill_rect(BAR_X, BAR_Y0, BAR_X + BAR_MAX - 1, BAR_Y1, PAL_HP)
        b += g.flip()
        return bytes(b), off

    # ---- setup (sent once) ----
    def setup(self):
        pk = [g.clear_errors(0x0F), g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0)]
        pk += [g.set_palette(i, c) for i, c in PALETTE.items()]
        gfx = bytearray(g.TILE_BYTES)
        for t in range(8):
            gfx[t * 64:t * 64 + 64] = bytes(ld.tile_pixels(t))
        pk += g.tile_packets(bytes(gfx[:8 * 64]))
        pk += g.map_packets(0, ld.far_map())                       # the far layer is periodic: written once, never streamed
        pk += self.near.load(self.cam.x, self.cam.y)                # the near layer: the ring for the first view
        for d in range(10):
            pk += g.upload_packets(d, 8, 8, glyph(d))
        pk += g.upload_packets(A_PLAYER, 16, 16, player_img())
        pk += g.upload_packets(A_ENEMY, 16, 16, enemy_img())
        pk += g.upload_packets(A_COIN, 16, 16, coin_img())
        for i in range(N_USED):                                    # every sprite starts parked
            asset = A_PLAYER if i == PLAYER else A_ENEMY if i < COIN0 else A_COIN
            pk.append(g.set_sprite(i, 0, PARK, asset, 0, g.SPRITE_ON))
            self.cur[i] = (0, PARK, g.SPRITE_ON)
            self.table[i * 8:i * 8 + 8] = g.sprite_entry(0, PARK, asset, 0, g.SPRITE_ON)
        pk += [g.sprite_ctrl(1), g.set_priority(**PRIORITY), g.set_tile_layer(3)]
        pk += g.list_packets(HUD_LIST, self.list_bytes)
        pk += [g.clear(0), g.flip(), g.clear(0)]                   # both buffers start clear; the FLIP waits for blanking
        return pk

    # ---- the world ----
    def ground(self, wx):
        """Top y (world) of a player standing at world x."""
        return sd.terrain((wx + 8) >> 3) * 8 - 16

    def step_world(self, sx, sy):
        """Advance the player, enemies and coins one frame; the camera is already at (sx, sy)."""
        px = sx + PLAYER_X
        gy = self.ground(px)
        if not self.air:
            self.py += max(-4, min(4, gy - self.py))
            if self.rng.next() % 37 == 0:                          # the scripted button
                self.air, self.vy = True, -9
        else:
            self.py += self.vy
            self.vy += 1
            if self.vy > 0 and self.py >= gy:
                self.py, self.vy, self.air = gy, 0, False
        for e in self.enemies:
            e[0] += e[1]
        for i, e in enumerate(self.enemies):
            if abs(e[0] - self.home[i]) >= 40:
                e[1] = -e[1]
        if self.cool:
            self.cool -= 1
        for i, e in enumerate(self.enemies):
            ey = self.ground(e[0])
            if not self.cool and abs(e[0] - px) < 12 and abs(ey - self.py) < 12:
                self.hp, self.cool = max(BAR_MIN, self.hp - 5), 45
        ci = self.active_chain(sx)
        if ci is not None:
            for j in range(CHAINS[ci][1]):
                if j in self.collected[ci]:
                    continue
                cx, cy = chain_coin(ci, j)
                if abs(cx - px) < 14 and abs(cy - self.py) < 14:
                    self.collected[ci].add(j)
                    self.score = min(9999, self.score + 10)
        return px

    def active_chain(self, sx):
        """The coin pool shows the first chain that has not scrolled off the left edge."""
        for ci, (x0, n, _) in enumerate(CHAINS):
            if x0 + COIN_STEP * (n - 1) + 16 > sx:
                return ci
        return None

    # ---- one frame ----
    def targets(self, sx, sy):
        """(x, y, flip) of every sprite for this camera; a sprite that is off the screen goes to the parking spot."""
        def on(wx, wy):
            return -16 < wx - sx < g.W and -16 < wy - sy < g.H
        t = [(PLAYER_X, self.py - sy, False)]
        for i, e in enumerate(self.enemies):
            ey = self.ground(e[0])
            t.append((e[0] - sx, ey - sy, e[1] < 0) if on(e[0], ey) else (0, PARK, e[1] < 0))
        ci = self.active_chain(sx)
        for j in range(N_COINS):
            if ci is not None and j < CHAINS[ci][1] and j not in self.collected[ci]:
                cx, cy = chain_coin(ci, j)
                if on(cx, cy):
                    t.append((cx - sx, cy - sy, False))
                    continue
            t.append((0, PARK, False))
        return t

    def sprite_packets(self, sx, sy):
        out = []
        for i, (x, y, flip) in enumerate(self.targets(sx, sy)):
            flags = g.SPRITE_ON | (g.SPRITE_FLIP_X if flip else 0)
            asset = A_PLAYER if i == PLAYER else A_ENEMY if i < COIN0 else A_COIN
            if (x, y, flags) == self.cur[i]:
                continue
            if flags != self.cur[i][2]:
                out.append(g.set_sprite(i, x, y, asset, 0, flags))        # the flags changed: the whole 8-byte entry
                self.table[i * 8:i * 8 + 8] = g.sprite_entry(x, y, asset, 0, flags)
            else:
                out.append(g.move_sprite(i, x, y))                         # only x and y: 5 bytes + 2
                self.table[i * 8:i * 8 + 4] = g.sprite_entry(x, y, asset, 0, flags)[:4]
            self.cur[i] = (x, y, flags)
        return out

    def hud_packets(self):
        pk = []
        new = hd.digits(self.score)
        for i in range(4):
            if new[i] != self.shown_digits[i]:
                pk.append(hd.digit_patch(self.off, i, new[i], HUD_LIST))
        if self.hp != self.shown_hp:
            pk.append(g.write_list_word(HUD_LIST, self.off["bar"] + 6, BAR_X + self.hp - 1))   # the bar's x1 field
        self.shown_digits, self.shown_hp = new, self.hp
        return pk

    def frame(self, k):
        """The batch of video frame k."""
        sx, sy = self.cam.step()
        fx, fy = sd.far_scroll(sx, sy)
        b = Batch()
        for p in self.near.plan(sx, sy):
            b.add("map", p)
        self.step_world(sx, sy)
        for p in self.sprite_packets(sx, sy):
            b.add("sprites", p)
        b.add("scroll", g.set_scroll(1, sx, sy))
        b.add("scroll", g.set_scroll(0, fx, fy))
        for p in self.hud_packets():
            b.add("hud", p)
        b.add("run", g.run_list(HUD_LIST))
        return b


def generate(seed, frames):
    """(game, setup packets, batches): the whole run, with no link involved. Same seed, same bytes."""
    game = Game(seed)
    setup = game.setup()
    batches = [game.frame(k) for k in range(frames)]
    return game, setup, batches


def digest(setup, batches):
    h = hashlib.sha256(b"".join(setup))
    for b in batches:
        h.update(b.data())
    return h.hexdigest()


# --- the log ---------------------------------------------------------------------------------
class Log:
    def __init__(self, header):
        self.lines = ["# side_scroller log 1", "# " + header]

    def frame_start(self, k, n):
        self.lines.append("F %d %d" % (k, n))

    def spi(self, data):
        self.lines.append("S " + bytes(data).hex())

    def reply(self, n):
        self.lines.append("Q %d" % n)

    def end(self):
        self.lines.append("E")

    def save(self, path):
        with open(path, "w") as f:
            f.write("\n".join(self.lines) + "\n")


class LogPort:
    """Wraps the SPI port: every command frame (CS low period) is logged as it goes out. Read frames (first
    byte FF) are not: how many of them the host needs depends on timing, so the log records Q records instead."""

    def __init__(self, port, log):
        self.port, self.log = port, log

    def exchange(self, tx, duplex=True):
        tx = bytes(tx)
        if tx and tx[0] != spi_test.READ:
            self.log.spi(tx)
        return self.port.exchange(tx, duplex=duplex)

    def __getattr__(self, name):          # gpu, and anything else the wrapped port has
        return getattr(self.port, name)


class LoggedLink(spi_test.SpiGpu):
    def __init__(self, base, log):
        super().__init__(LogPort(base.port, log), base.ready, base.vsync)
        self.log = log

    def read_reply(self, n, timeout=2.0):
        out = super().read_reply(n, timeout)
        self.log.reply(n)
        return out


# --- the budget ------------------------------------------------------------------------------
class RecordPort:
    def __init__(self):
        self.frames = []

    def exchange(self, tx, duplex=True):
        self.frames.append(bytes(tx))
        return bytes(len(tx))


def cs_frames(data):
    """The CS-low periods the link will use for these bytes (SpiGpu.send's own packing)."""
    port = RecordPort()
    spi_test.SpiGpu(port, lambda: True).send(data)
    return [len(f) for f in port.frames]


def draw_ms(list_bytes):
    """Time the HUD list needs on the command engine: about one pixel per clock plus a few clocks per command."""
    px, i, n = 0, 0, 0
    while i < len(list_bytes):
        op, ln = list_bytes[i], list_bytes[i + 1]
        p = list_bytes[i + 2:i + 2 + ln]
        if op == g.CLEAR:
            px += g.FB_SIZE
        elif op == g.FILL_RECT:
            x0, y0, x1, y1 = (int.from_bytes(p[a:a + 2], "little", signed=True) for a in (0, 2, 4, 6))
            px += (abs(x1 - x0) + 1) * (abs(y1 - y0) + 1)
        elif op == g.BLIT:
            px += 64
        n += 1
        i += 2 + ln
    return (px + 20 * n) / SYS_HZ * 1e3


def send_ms(nbytes, spi_hz, n_cs, cs_overhead_us):
    return nbytes * 8 / spi_hz * 1e3 + n_cs * cs_overhead_us / 1e3


def report(setup, batches, list_bytes, spi_hz, cs_us):
    """Prints the byte statistics. Returns True if the budget holds."""
    sizes = [len(b.data()) for b in batches]
    n = len(sizes)
    tags = ["map", "sprites", "scroll", "hud", "run"]
    tot = {t: 0 for t in tags}
    mx = {t: 0 for t in tags}
    for b in batches:
        d = b.by_tag()
        for t in tags:
            tot[t] += d.get(t, 0)
            mx[t] = max(mx[t], d.get(t, 0))
    setup_bytes = sum(len(p) for p in setup)
    print("%d frames: %.1f bytes per frame on average, at most %d, at least %d (budget %d)" %
          (n, sum(sizes) / n, max(sizes), min(sizes), BUDGET))
    print("  by kind, mean / max per frame: " + ", ".join("%s %.1f / %d" % (t, tot[t] / n, mx[t]) for t in tags))
    print("  one-off setup (palette, art, far map, first 64 columns, lists): %d bytes = %.1f ms at %.1f MHz" %
          (setup_bytes, send_ms(setup_bytes, spi_hz, len(cs_frames(b"".join(setup))), cs_us), spi_hz / 1e6))
    print("  at 60 frames a second the average is %.0f bytes a second; at most %d sprite packets in one frame" %
          (sum(sizes) / n * 60, max(sum(1 for t, p in b.pk if t == "sprites") for b in batches)))
    ok = max(sizes) < BUDGET
    # time: the slowest batch (bytes on the wire plus a fixed cost per CS frame), then the list draws, all before blanking
    def t_of(b):
        return send_ms(len(b.data()), spi_hz, len(cs_frames(b.data())), cs_us)
    worst = max(batches, key=t_of)
    frames = cs_frames(worst.data())
    t_send = t_of(worst)
    t_draw = draw_ms(list_bytes)
    fits = t_send + t_draw < VISIBLE_MS
    print("  slowest frame: %d bytes in %d CS frames = %.2f ms on the wire (%.0f us overhead per CS frame) + %.2f ms for the HUD list"
          " = %.2f ms of the %.2f ms visible part (%s)" %
          (len(worst.data()), len(frames), t_send, cs_us, t_draw, t_send + t_draw, VISIBLE_MS, "fits" if fits else "TOO SLOW"))
    after_run = all(b.pk[-1][0] == "run" and sum(1 for t, _ in b.pk if t == "run") == 1 for b in batches)
    print("  nothing is sent after the RUN_LIST: %s; largest CS frame %d bytes (READY allows %d)" %
          (after_run, max(max(cs_frames(b.data())) for b in batches), spi_test.MAX_PACKET))
    return ok and fits and after_run


# --- running it ------------------------------------------------------------------------------
def check_model(m, game, k):
    """After the batch of frame k has been sent: does the model hold what the host believes it sent?"""
    sx, sy = game.cam.x, game.cam.y
    c_lo, c_hi, r_lo, r_hi = game.near.view
    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            assert m.tmap[1][(r % 64) * 64 + c % 64] == sd.level(c, r), "frame %d: map wrong at level tile (%d, %d)" % (k, c, r)
    assert m.scroll[1] == [sx % 512, sy % 512], "frame %d: near scroll" % k
    assert m.scroll[0] == [v % 512 for v in sd.far_scroll(sx, sy)], "frame %d: far scroll" % k
    assert bytes(m.sprites[:N_USED * 8]) == bytes(game.table[:N_USED * 8]), "frame %d: sprite table differs" % k
    lst = m.lists[HUD_LIST]
    for i, d in enumerate(hd.digits(game.score)):
        assert lst[game.off["digit%d" % i] + 2] == d, "frame %d: HUD digit %d" % (k, i)
    assert int.from_bytes(lst[game.off["bar"] + 6:game.off["bar"] + 8], "little") == BAR_X + game.hp - 1, "frame %d: health bar" % k


def run_model_checked(args, setup, batches, log):
    """The model run with the checks. The game is re-generated alongside (it is deterministic) so check_model
    can compare the model with the host's memory at every frame."""
    base = spi_test.open_link(argparse.Namespace(model=True))
    link = LoggedLink(base, log)
    m = base.port.gpu
    assert link.info()["caps"] & g.CAPS_LISTS, "GET_INFO does not report DISPLAY_LISTS"
    game = Game(args.seed)
    link.send(b"".join(game.setup()))
    assert link.info()["errors"] == 0, "setup set an error flag"
    for k, b in enumerate(batches):
        m.vblank()                                  # video frame k begins: the shadows latched at the blanking
        mine = game.frame(k)
        assert mine.data() == b.data(), "frame %d: a second run of the game produced other bytes" % k
        data = mine.data()
        log.frame_start(k, len(data))
        link.send(data)
        check_model(m, game, k)
    log.end()
    info = link.info()
    assert info["errors"] == 0, "error flags %X after the run" % info["errors"]
    return link, m


def run_board(args, game, setup, batches, log):
    base = spi_test.open_link(args)
    link = LoggedLink(base, log) if log else base
    info = link.info()
    if not info["caps"] & g.CAPS_LISTS:
        print("FAIL: side_scroller (GET_INFO capabilities %04X: no DISPLAY_LISTS bit; is the Chapter 5.7 bitstream loaded?)" % info["caps"])
        return None
    link.send(b"".join(setup))
    print("setup sent; running %d frames" % len(batches))
    t0, late, t_draw = time.time(), 0, draw_ms(game.list_bytes)
    try:
        for k, b in enumerate(batches):
            t = time.time()
            while not link.vsync() and time.time() - t < 0.1:     # blanking...
                pass
            while link.vsync() and time.time() - t < 0.2:         # ...and its end: VSYNC fell
                pass
            t1 = time.time()
            data = b.data()
            if log:
                log.frame_start(k, len(data))
            link.send(data)
            # late: the batch plus the list's drawing did not end before blanking (or blanking has already begun)
            late += (time.time() - t1) * 1e3 + t_draw > VISIBLE_MS or link.vsync()
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    if log:
        log.end()
    info = link.info()
    print("%.1f s (%.1f frames per second); %d frames late (batch + HUD drawing ran into blanking); errors %X" %
          (dt, (k + 1) / dt if dt else 0, late, info["errors"]))
    return info["errors"] == 0 and late == 0


def write_ppm(m, path):
    with open(path, "wb") as f:
        f.write(b"P6\n320 240\n255\n")
        for c in m.screen():
            v = PALETTE.get(c, 0)
            f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, default=600, help="frames to play (600 is ten seconds)")
    ap.add_argument("--seed", type=int, default=1, help="seed of the scripted button")
    ap.add_argument("--log", help="write every SPI byte, with frame markers, to this file")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    ap.add_argument("--cs-overhead-us", type=float, default=1000.0,
                    help="time one CS-low period costs besides its bytes (default 1000: a USB transfer through the FT232H; an MCU needs about 5)")
    args = ap.parse_args()

    game, setup, batches = generate(args.seed, args.frames)
    d1 = digest(setup, batches)
    _, setup2, batches2 = generate(args.seed, args.frames)
    assert digest(setup2, batches2) == d1, "the game is not deterministic"
    print("seed %d: %d frames, byte stream sha256 %s" % (args.seed, args.frames, d1[:16]))

    log = Log("seed %d frames %d sha256 %s" % (args.seed, args.frames, d1)) if args.log else None
    ok = report(setup, batches, game.list_bytes, args.freq, args.cs_overhead_us)
    if args.model:
        link, m = run_model_checked(args, setup, batches, log or Log(""))
        if args.ppm:
            m.vblank()
            write_ppm(m, args.ppm)
        if log:
            log.save(args.log)
            print("log written to %s (%d records)" % (args.log, len(log.lines)))
        print("PASS: side_scroller (model)" if ok else "FAIL: side_scroller (budget)")
        return 0 if ok else 1
    res = run_board(args, game, setup, batches, log)
    if log:
        log.save(args.log)
    if res is None:
        return 1
    ok = ok and res
    print("PASS: side_scroller" if ok else "FAIL: side_scroller")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
