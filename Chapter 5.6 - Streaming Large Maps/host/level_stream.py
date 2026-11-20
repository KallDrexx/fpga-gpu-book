#!/usr/bin/env python3
"""level_stream.py -- the host side of streamed tile maps (Chapter 5.6).

A tile layer's map holds 64 x 64 entries, a ring: map column c % 64 holds level
column c, map row r % 64 holds level row r, for the 64 columns and 64 rows the host
keeps loaded. The screen shows at most 41 x 31 tiles. When the scroll moves, the
host writes the tiles that just came into view, one column or one row at a time,
over the ones that just left.

StreamedLayer does that bookkeeping for one layer:

    s = StreamedLayer(layer, level, width, height)    # level(column, row) -> map entry (0 outside)
    packets = s.load(sx, sy)      # fill the ring for a view whose top-left pixel is (sx, sy)
    packets = s.plan(sx, sy)      # the packets that make the ring right for the view at (sx, sy)

plan() is for the frame AFTER the one on screen: the writes arrive while the old
view is still being drawn, so a write must never overwrite a tile of the old view.
plan() asserts this (a step of up to 23 tiles in X and 33 in Y always passes)
and also that the view stays inside the level. sx and sy are LEVEL pixel positions
(no wrapping; SET_SCROLL's modulo 512 does the wrapping). With wrap_y=True the
level is exactly 64 rows high and wraps vertically on its own (nothing to stream in Y).

Used by sim/py/ref_streaming.py and host/stream_demo.py.
"""
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gpu_model as g  # noqa: E402

VIEW_W, VIEW_H = g.W, g.H     # 320 x 240 visible pixels
RING = g.MAP_W                # 64


def view_tiles(sx, sy):
    """(first column, last column, first row, last row) of the level tiles a view touches."""
    return sx >> 3, (sx + VIEW_W - 1) >> 3, sy >> 3, (sy + VIEW_H - 1) >> 3


class StreamedLayer:
    def __init__(self, layer, level, width, height, wrap_y=False):
        if wrap_y and height != RING:
            raise ValueError("wrap_y needs a level exactly 64 rows high")
        self.layer, self.level, self.width, self.height, self.wrap_y = layer, level, width, height, wrap_y
        self.c0 = self.r0 = 0         # first level column / row held in the ring
        self.view = None              # tiles of the view the screen is showing

    # -- one column / row of the ring, entries in MAP order -------------------
    def column(self, c):
        """WRITE_TILE_COLUMN for level column c: entry k is for map row k, which holds level row r with r % 64 == k."""
        ents = []
        for k in range(RING):
            r = k if self.wrap_y else self.r0 + (k - self.r0) % RING
            ents.append(self.level(c, r))
        return g.write_tile_column(self.layer, c % RING, ents)

    def row(self, r):
        """WRITE_TILE_ROW for level row r: entry k is for map column k, which holds level column c with c % 64 == k."""
        return g.write_tile_row(self.layer, r % RING,
                                [self.level(self.c0 + (k - self.c0) % RING, r) for k in range(RING)])

    def _check(self, v):
        c_lo, c_hi, r_lo, r_hi = v
        assert 0 <= c_lo and c_hi < self.width, "the view leaves the level in X"
        if not self.wrap_y:
            assert 0 <= r_lo and r_hi < self.height, "the view leaves the level in Y"

    def load(self, sx, sy):
        """Fill the whole ring for the view at (sx, sy): 64 column writes."""
        v = view_tiles(sx, sy)
        self._check(v)
        self.c0, self.r0 = v[0], (0 if self.wrap_y else v[2])
        self.view = v
        return [self.column(c) for c in range(self.c0, self.c0 + RING)]

    def plan(self, sx, sy):
        """Packets for the next frame's view: new columns first, then new rows."""
        v = view_tiles(sx, sy)
        self._check(v)
        o_c_lo, o_c_hi, o_r_lo, o_r_hi = self.view
        c_lo, c_hi, r_lo, r_hi = v
        pk = []
        # columns. A write to column c lands on the map column that holds c - 64 (going right)
        # or c + 64 (going left); that old column must not be in the view being drawn.
        if c_lo < self.c0:
            for c in range(self.c0 - 1, c_lo - 1, -1):
                assert c + RING > o_c_hi, "scrolled left too fast: column %d would overwrite one still on screen" % c
                pk.append(self.column(c))
            self.c0 = c_lo
        elif c_hi > self.c0 + RING - 1:
            for c in range(self.c0 + RING, c_hi + 1):
                assert c - RING < o_c_lo, "scrolled right too fast: column %d would overwrite one still on screen" % c
                pk.append(self.column(c))
            self.c0 = c_hi - (RING - 1)
        # rows, with the column window already moved, so a new row is complete
        if not self.wrap_y:
            if r_lo < self.r0:
                for r in range(self.r0 - 1, r_lo - 1, -1):
                    assert r + RING > o_r_hi, "scrolled up too fast: row %d would overwrite one still on screen" % r
                    pk.append(self.row(r))
                self.r0 = r_lo
            elif r_hi > self.r0 + RING - 1:
                for r in range(self.r0 + RING, r_hi + 1):
                    assert r - RING < o_r_lo, "scrolled down too fast: row %d would overwrite one still on screen" % r
                    pk.append(self.row(r))
                self.r0 = r_hi - (RING - 1)
        self.view = v
        return pk


def total_bytes(packets):
    return sum(len(p) for p in packets)
