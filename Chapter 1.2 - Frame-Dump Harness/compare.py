#!/usr/bin/env python3
"""compare.py -- compare simulated frames against reference images (Chapter 1.2).

Two ways to call it:

  compare.py GOT.ppm EXPECTED.ppm [--tolerance N] [--diff DIFF.ppm]
      Compare one pair of images.

  compare.py --set DIR NAME [--tolerance N]
      Compare every DIR/ref_NAME_<label>.ppm with DIR/frame_NAME_<label>.ppm.
      On a mismatch, also writes DIR/diff_NAME_<label>.ppm.

A pixel matches when every channel differs by at most N (default 0, exact).
The first mismatch is reported in scan order (top line first, left to right),
which is the order the hardware drew it. A diff image shows mismatched pixels
in bright magenta over a dimmed copy of the expected image.

Prints detail lines, then exactly one line starting with PASS or FAIL.
Exit status is 0 on PASS, 1 on FAIL.
"""
import argparse
import glob
import os
import sys

sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree

import numpy as np  # noqa: E402

from ppm import read_ppm, write_ppm  # noqa: E402


def compare(got_path, exp_path, tolerance=0, diff_path=None):
    """Return (ok, message). Prints nothing."""
    if not os.path.exists(got_path):
        return False, f"{got_path} not found (did the simulation write it?)"
    name = os.path.basename(got_path)
    try:
        got, got_max = read_ppm(got_path)
        exp, exp_max = read_ppm(exp_path)
    except (ValueError, IndexError) as e:
        return False, f"{name}: unreadable PPM ({e})"
    if got.shape != exp.shape:
        return False, (f"{name}: size {got.shape[1]}x{got.shape[0]}, "
                       f"expected {exp.shape[1]}x{exp.shape[0]}")
    if got_max != exp_max:
        return False, f"{name}: maxval {got_max}, expected {exp_max}"

    err = np.abs(got.astype(np.int32) - exp.astype(np.int32)).max(axis=2)
    bad = err > tolerance
    nbad = int(bad.sum())
    h, w = bad.shape
    if nbad == 0:
        return True, f"{name}: {w}x{h} match" + (f" (tolerance {tolerance})" if tolerance else "")

    ys, xs = np.nonzero(bad)          # nonzero() returns scan order
    y0, x0 = int(ys[0]), int(xs[0])
    msg = (f"{name}: {nbad} of {w * h} pixels differ; first at (x={x0}, y={y0}): "
           f"got {tuple(int(v) for v in got[y0, x0])}, "
           f"expected {tuple(int(v) for v in exp[y0, x0])}; "
           f"differences span x={xs.min()}..{xs.max()}, y={ys.min()}..{ys.max()}")

    if diff_path:
        diff = exp // 3
        diff[bad] = (exp_max, 0, exp_max)
        write_ppm(diff_path, diff, exp_max)
        msg += f"; diff image {diff_path}"
    return False, msg


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("got", nargs="?", help="simulated frame (PPM)")
    ap.add_argument("expected", nargs="?", help="reference frame (PPM)")
    ap.add_argument("--set", nargs=2, metavar=("DIR", "NAME"),
                    help="compare all DIR/ref_NAME_*.ppm against DIR/frame_NAME_*.ppm")
    ap.add_argument("--tolerance", type=int, default=0,
                    help="largest per-channel difference still counted as a match")
    ap.add_argument("--diff", help="write a diff image here on mismatch (pair mode)")
    args = ap.parse_args()

    if args.set:
        d, name = args.set
        refs = sorted(glob.glob(os.path.join(d, f"ref_{name}_*.ppm")))
        if not refs:
            print(f"FAIL: compare {name}: no {d}/ref_{name}_*.ppm (run ref_{name}.py first)")
            return 1
        pairs = []
        for ref in refs:
            label = os.path.basename(ref)[len(f"ref_{name}_"):-len(".ppm")]
            got = os.path.join(d, f"frame_{name}_{label}.ppm")
            diff = os.path.join(d, f"diff_{name}_{label}.ppm")
            pairs.append((label, got, ref, diff))
        title = f"compare {name}"
    elif args.got and args.expected:
        pairs = [(None, args.got, args.expected, args.diff)]
        title = "compare"
    else:
        ap.error("give GOT and EXPECTED, or --set DIR NAME")

    failed = []
    for label, got, ref, diff in pairs:
        ok, msg = compare(got, ref, args.tolerance, diff)
        print(("  ok    " if ok else "  FAIL  ") + msg)
        if not ok:
            failed.append(label or os.path.basename(got))

    if failed:
        print(f"FAIL: {title} ({len(failed)} of {len(pairs)} frames differ: {', '.join(failed)})")
        return 1
    n = f"{len(pairs)} frame" + ("s match" if len(pairs) != 1 else " matches")
    print(f"PASS: {title} ({n} exactly)" if args.tolerance == 0 else
          f"PASS: {title} ({n} within {args.tolerance})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
