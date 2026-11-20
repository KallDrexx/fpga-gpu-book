#!/usr/bin/env python3
"""spi_info.py -- ask the GPU who it is (Chapter 5.2).

Usage:
    .venv/bin/python host/spi_info.py [--url ...] [--freq 1000000] [--clear] [--resync]
    .venv/bin/python host/spi_info.py --model

Sends GET_INFO and prints the reply: protocol version, capability names, screen
size and the sticky error flags. --clear then sends CLEAR_ERRORS 0x0F and asks
again. --resync first clears the flags and sends the worst frame there is (a
PLOT promising 255 bytes, cut off), then checks that the next frame works
anyway and that TRUNC is set.
Wiring and options are those of spi_test.py.
"""
import argparse
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402
import spi_test  # noqa: E402

CAPS = ["DRAW", "FLIP", "TRI", "BLIT", "TILES", "SPRITES", "COMPOSITOR", "TILE_STREAM", "DISPLAY_LISTS", "SPI", "3D"]
FLAGS = [(g.ERR_OVERFLOW, "OVERFLOW"), (g.ERR_BAD_LEN, "BAD_LEN"), (g.ERR_BAD_OP, "BAD_OP"), (g.ERR_TRUNC, "TRUNC")]


def show(info):
    caps = [n for i, n in enumerate(CAPS) if info["caps"] >> i & 1]
    flags = [n for bit, n in FLAGS if info["errors"] & bit]
    print("magic %02X  protocol version %d  screen %dx%d" % (info["magic"], info["version"], info["width"], info["height"]))
    print("capabilities %04X: %s" % (info["caps"], " ".join(caps) or "-"))
    print("errors %X: %s" % (info["errors"], " ".join(flags) or "none"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    spi_test.add_link_args(ap)
    ap.add_argument("--clear", action="store_true", help="clear the error flags afterwards")
    ap.add_argument("--resync", action="store_true", help="send a cut-off packet first")
    args = ap.parse_args()
    link = spi_test.open_link(args)
    if args.resync:
        link.send(g.clear_errors(0x0F))                    # so the TRUNC below is from this test
        link.wait_ready()
        link._frame(bytes([g.PLOT, 255]) + bytes(20))     # promises 255 payload bytes, delivers 20, then CS rises
    info = link.info()
    show(info)
    ok = info["magic"] == g.INFO_MAGIC
    if args.resync:
        print("after the cut-off packet the next frame %s; TRUNC is %s" %
              ("worked" if ok else "did NOT work", "set" if info["errors"] & g.ERR_TRUNC else "NOT set"))
        ok = ok and bool(info["errors"] & g.ERR_TRUNC)
    if args.clear:
        link.send(g.clear_errors(0x0F))
        info = link.info()
        show(info)
        ok = ok and info["errors"] == 0
    print("PASS: spi_info" if ok else "FAIL: spi_info")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
