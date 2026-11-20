#!/usr/bin/env python3
"""compositor_demo.py -- all four layers, toggled live (Chapter 5.5).

Usage:
    .venv/bin/python host/compositor_demo.py [--url ftdi://ftdi:232h/1] [--freq 5000000] [--frames 1050]
    .venv/bin/python host/compositor_demo.py --model

Needs host/layer_demo.py (Chapter 5.3) and host/sprite_demo.py (Chapter 5.4) next to it: it reuses
their tiles, maps, palette ramps and ball image.

The scene: far layer 0 (sky and hills) and near layer 1 (ground and trees) scroll as in
layer_demo.py; a player ball (sprite 0) and six flying balls move over them; the framebuffer
holds a HUD frame (index 0 everywhere else), drawn once. Layer priorities:
framebuffer 3 (front), layer 1 and sprites 1 (equal), layer 0 0. Layer 1 and the sprites share a priority, so
the item priorities decide between them: the trees of layer 1 have PRIORITY 1 (map entry bits 11:10), the player
0 and the ground tiles 0. The player walks BEHIND the trees but in front of the ground (equal item priorities:
the later source, the sprites, wins).

Every 150 frames the script changes one thing, in this order, one packet or so each time:
    0 everything on
    1 FB_ENABLE 0: the HUD frame disappears
    2 SET_TILE_LAYER 2: layer 0 off too: the backdrop colour shows where nothing else is
    3 HUD and layer 0 back, SET_TILE_LAYER 1: layer 1 off: no ground, no trees
    4 SPRITE_CTRL 0: all sprites off
    5 the trees are rewritten with PRIORITY 0 (one WRITE_TILE_MAP): the player now walks in front of them
    6 SET_PRIORITY with layer 1 at 2 and the sprites at 1: the player is behind all of layer 1
Everything arrives at the next vertical blanking, so no phase change tears. Ctrl-C stops early.

Wiring and options are those of spi_test.py. --frames 0 runs until Ctrl-C.

--model needs no board: it runs the same host code against the Python model, checks
GET_INFO reports the COMPOSITOR capability, and at the end of every phase freezes the scene (scroll 0, player at
(100, 210), in front of a tree) and checks pixels: the HUD corner, the backdrop, the tree leaf
against the player. With --ppm FILE it also writes the last frame as a picture.
"""
import argparse
import os
import sys
import time

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import layer_demo as ld  # noqa: E402
import spi_test  # noqa: E402
import sprite_demo as sd  # noqa: E402

HUD, BACKDROP = 9, 10
COLORS = dict(ld.COLORS)
COLORS[HUD] = 0xF0E060
COLORS[BACKDROP] = 0xC03090
PLAYER_PAL = 3                       # sprite_demo's yellow ramp: entries 49-63
PLAYER_Y = 210                       # the player overlaps the tree row (y 216-223)
PHASE_FRAMES = 150
NPHASES = 7
N_BALLS = 6


def tree_row(pri):
    """Layer 1, map row 27: the trees (and nothing else), with item priority 1 (pri True) or 0."""
    return [g.map_entry(ld.T_TREE, pri=1 if pri else 0) if col % 9 == 4 else 0 for col in range(64)]


def scene_packets():
    pk = [g.set_tile_layer(0), g.set_scroll(0, 0, 0), g.set_scroll(1, 0, 0), g.sprite_ctrl(False)]
    pk += sd.palette_packets()                                     # sprite ramps at 16-79 (and entry 0)
    pk += [g.set_palette(i, c) for i, c in COLORS.items()]         # then the world colors
    gfx = bytearray(g.TILE_BYTES)
    for t in range(8):
        gfx[t * 64:t * 64 + 64] = bytes(ld.tile_pixels(t))
    pk += g.tile_packets(bytes(gfx[:8 * 64]))
    pk += g.map_packets(0, ld.far_map())
    near = ld.near_map()
    near[27 * 64:28 * 64] = tree_row(True)
    pk += g.map_packets(1, near)
    pk += g.upload_packets(0, 16, 16, sd.ball(False))
    pk += [g.set_sprite(0, 0, PLAYER_Y, 0, PLAYER_PAL)]
    pk += [g.set_sprite(1 + i, 0, 100 + 6 * i, 0 if i % 2 else 1, 1 + i % 3) for i in range(N_BALLS)]
    # the framebuffer: a frame of color HUD around a transparent (index 0) middle
    pk += [g.clear(0), g.fill_rect(0, 0, 319, 7, HUD), g.fill_rect(0, 232, 319, 239, HUD),
           g.fill_rect(0, 0, 5, 239, HUD), g.fill_rect(314, 0, 319, 239, HUD), g.flip()]
    pk += [g.set_backdrop(BACKDROP), g.set_priority(fb=3, l0=0, l1=1, sp=1), g.set_tile_layer(3), g.sprite_ctrl(True)]
    return pk


def phase_packets(k):
    """What the script sends when phase k starts (k = 0 is the state the scene already has)."""
    if k == 0:
        return []
    if k == 1:
        return [g.fb_enable(False)]
    if k == 2:
        return [g.set_tile_layer(2)]
    if k == 3:
        return [g.fb_enable(True), g.set_tile_layer(1)]
    if k == 4:
        return [g.set_tile_layer(3), g.sprite_ctrl(False)]
    if k == 5:
        return [g.sprite_ctrl(True), g.write_tile_map(1, 27 * 64, tree_row(False))]
    return [g.set_priority(fb=3, l0=0, l1=2, sp=1)]


def phase_state(k):
    """(fb on, layer 0 on, layer 1 on, sprites on, trees have item priority 1, sprites' layer priority is not below layer 1's) of phase k."""
    return (k not in (1, 2), k != 2, k != 3, k != 4, k < 5, k < 6)


def motion(frame):
    """Packets for one video frame: the layers scroll, the player walks, the balls fly."""
    x0, x1 = ld.scroll_for(frame)
    pk = [g.set_scroll(0, x0, 0), g.set_scroll(1, x1, 0), g.move_sprite(0, (2 * frame) % 300 + 4, PLAYER_Y)]
    pk += [g.move_sprite(1 + i, (3 * frame * (1 + i % 2) + 50 * i) % 340 - 20, 100 + 6 * i + (frame // 3 + 5 * i) % 4) for i in range(N_BALLS)]
    return b"".join(pk)


def run_model(frames=None):
    link = spi_test.open_link(argparse.Namespace(model=True))
    m = link.port.gpu
    info = link.info()
    assert info["caps"] & 0x40, "GET_INFO does not report the COMPOSITOR capability"
    link.send(b"".join(scene_packets()))
    m.vblank()
    ball = sd.ball(False)
    frame = 0
    for k in range(NPHASES):
        link.send(b"".join(phase_packets(k)))
        m.vblank()
        for _ in range(30):                      # run some frames the way the demo does
            link.send(motion(frame))
            m.vblank()
            frame += 1
        link.send(g.set_scroll(0, 0, 0) + g.set_scroll(1, 0, 0) + g.move_sprite(0, 100, PLAYER_Y)
                  + b"".join(g.move_sprite(1 + i, 200 + 12 * i, 40) for i in range(N_BALLS)))
        m.vblank()
        s = m.screen()
        fb_on, l0_on, l1_on, sp_on, trees_pri, sp_wins = phase_state(k)
        # the top-left corner: the HUD in front of everything, else layer 0's sky, else the backdrop
        assert s[0] == (HUD if fb_on else ld.SKY if l0_on else BACKDROP), "phase %d: top-left corner is %d" % (k, s[0])
        # a pixel in the middle of the sky: layer 0, or the backdrop where layer 0 is off
        assert s[60 * 320 + 200] == (ld.SKY if l0_on else BACKDROP), "phase %d: sky pixel is %d" % (k, s[60 * 320 + 200])
        # a pixel of the player (local 2, 7), beside the tree in map column 13: empty in layer 1, so the
        # sprite (layer priority 1) beats layer 0 (priority 0) in every phase
        ply = (ball[7 * 16 + 2] + 16 * PLAYER_PAL) & 255
        assert (s[217 * 320 + 102] == ply) == sp_on, "phase %d: the player (sprites %s) shows %d" % (k, sp_on, s[217 * 320 + 102])
        # the leaf of that tree at (107, 218) against the player's pixel (local 7, 8) there: the leaf wins when
        # the tree's item priority is above the player's (1 against 0, equal layer priorities) or when layer 1 has the higher layer priority
        px = (ball[8 * 16 + 7] + 16 * PLAYER_PAL) & 255
        if l1_on and sp_on:
            want = px if (sp_wins and not trees_pri) else ld.LEAF
        elif l1_on:
            want = ld.LEAF
        elif sp_on:
            want = px
        else:
            want = None                                         # layer 0's hills: not checked here
        if want is not None:
            assert s[218 * 320 + 107] == want, "phase %d: pixel (107,218) is %d, expected %d" % (k, s[218 * 320 + 107], want)
    last = m.screen()
    assert link.info()["errors"] == 0
    return link, last


def wait_vblank_start(link, timeout=0.1):
    """Wait for VSYNC (the vblank pin) to rise; without the pin, sleep one frame."""
    if link.vsync is None:
        time.sleep(1 / 60)
        return
    t0 = time.time()
    while link.vsync() and time.time() - t0 < timeout:   # leave the current blanking
        pass
    while not link.vsync() and time.time() - t0 < timeout:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--frames", type=int, default=PHASE_FRAMES * NPHASES, help="video frames to run (0: until Ctrl-C)")
    ap.add_argument("--ppm", help="with --model: write the last frame to this P6 file")
    args = ap.parse_args()

    if args.model:
        link, frame = run_model()
        if args.ppm:
            pal = link.port.gpu.pal
            with open(args.ppm, "wb") as f:
                f.write(b"P6\n320 240\n255\n")
                for c in frame:
                    v = pal[c]
                    f.write(bytes([v >> 16 & 255, v >> 8 & 255, v & 255]))
        print("PASS: compositor_demo (model)")
        return 0

    link = spi_test.open_link(args)
    info = link.info()
    if not info["caps"] & 0x40:
        print("FAIL: compositor_demo (GET_INFO capabilities %04X: no COMPOSITOR bit; is the Chapter 5.5 bitstream loaded?)" % info["caps"])
        return 1
    link.send(g.clear_errors(0x0F))
    link.send(b"".join(scene_packets()))
    print("scene uploaded; a phase every %d frames (Ctrl-C to stop)" % PHASE_FRAMES)
    frame, t0 = 0, time.time()
    try:
        while args.frames == 0 or frame < args.frames:
            wait_vblank_start(link)
            k = (frame // PHASE_FRAMES) % NPHASES
            if frame % PHASE_FRAMES == 0:
                if k == 0 and frame:                                       # a new round: undo phases 5 and 6
                    link.send(g.write_tile_map(1, 27 * 64, tree_row(True)) + g.set_priority(fb=3, l0=0, l1=1, sp=1))
                link.send(b"".join(phase_packets(k)))
                print("phase %d" % k)
            link.send(motion(frame))
            frame += 1
    except KeyboardInterrupt:
        pass
    dt = time.time() - t0
    info = link.info()
    print("%d frames in %.1f s (%.1f per second; about 60 expected with the VSYNC pin); errors %X" %
          (frame, dt, frame / dt if dt else 0, info["errors"]))
    ok = info["errors"] == 0
    print("PASS: compositor_demo" if ok else "FAIL: compositor_demo (error flags set)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
