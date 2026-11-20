#!/usr/bin/env python3
"""ref_mac.py -- test vectors for tb_mac_unit (Chapter 6.1).

Usage (from the repo root, once; commit the two files it writes):
    .venv/bin/python sim/py/ref_mac.py sim

Writes:
    sim/mac_in.hex   one multiply-accumulate beat per line, 10 hex digits  F G AAAA BBBB
                       F    flags: bit 0 = clear (first product of a sum), bit 1 = last (final product)
                       G    idle clocks (i_valid = 0) the testbench inserts BEFORE this beat, 0..15
                       AAAA i_a, 16-bit two's complement
                       BBBB i_b, 16-bit two's complement
    sim/mac_exp.hex  one 4-digit hex result per sum, in order (the sum that ends at the n-th 'last' beat).

The model (mac_ref below) is the specification: exact integer sum of the products, add half an
output LSB, floor-shift right by FRAC (round half up), saturate to OUT_W bits.
Line 0 of mac_in.hex is always a single-product sum; the testbench uses it to measure the latency.
"""
import os
import random
import sys

sys.dont_write_bytecode = True

A_W = B_W = 16
FRAC = 14
OUT_W = 16
ACC_W = 48
OUT_MAX = (1 << (OUT_W - 1)) - 1
OUT_MIN = -(1 << (OUT_W - 1))
IN_MAX = (1 << (A_W - 1)) - 1
IN_MIN = -(1 << (A_W - 1))
N_RANDOM = 100000
SEED = 6161


def wrap(x, bits):
    """Two's complement wrap, as the accumulator does."""
    x &= (1 << bits) - 1
    return x - (1 << bits) if x >> (bits - 1) else x


def mac_ref(terms, frac=FRAC, out_w=OUT_W, acc_w=ACC_W):
    """terms: list of (a, b) signed ints. Returns the out_w-bit signed result:
    sum the products in an acc_w-bit accumulator, round half up, drop frac bits, saturate."""
    acc = 0
    for a, b in terms:
        acc = wrap(acc + a * b, acc_w)
    r = (acc + (1 << (frac - 1))) >> frac          # Python >> floors: arithmetic shift
    lo, hi = -(1 << (out_w - 1)), (1 << (out_w - 1)) - 1
    return max(lo, min(hi, r))


def rnd_range(rng, bits):
    """Signed value with a random magnitude of up to `bits` bits."""
    b = rng.randint(0, bits)
    lo, hi = -(1 << b), (1 << b) - 1
    return max(IN_MIN, min(IN_MAX, rng.randint(lo, hi)))


def terms_for_sum(rng, s):
    """Products (a, b) whose exact sum is s, for |s| up to about 2^30. Order and a/b roles shuffled."""
    q = round(s / 16384)
    b1 = max(IN_MIN, min(IN_MAX, q))
    b2 = q - b1
    rem = s - 16384 * q                           # |rem| <= 8192 fits one factor
    t = [(16384, b1)]
    if b2:
        t.append((16384, b2))
    if rem:
        t.append((rem, 1))
    rng.shuffle(t)
    return [(b, a) if rng.random() < 0.5 else (a, b) for a, b in t]


def random_sum(rng):
    kind = rng.randrange(8)
    n = rng.choice([1, 1, 2, 3, 3, 4, 4, 4, 5, 8])
    if kind <= 2:                                  # full range: saturates a lot
        return [(rng.randint(IN_MIN, IN_MAX), rng.randint(IN_MIN, IN_MAX)) for _ in range(n)]
    if kind <= 4:                                  # varied magnitudes: mostly does not saturate
        return [(rnd_range(rng, 15), rnd_range(rng, 15)) for _ in range(n)]
    if kind == 5:                                  # Q2.14 matrix entry x coordinate, plus translation
        t = [(rng.randint(-32768, 32767) >> rng.randrange(3), rnd_range(rng, 15)) for _ in range(3)]
        t.append((rnd_range(rng, 15), 0x4000))
        return t
    if kind == 6:                                  # exact rounding ties (sum = k + 0.5 LSB)
        k = rng.randint(-40000, 40000) if rng.random() < 0.8 else rng.randint(-60000, 60000)
        return terms_for_sum(rng, k * 16384 + 8192 + rng.choice([0, 0, 0, 1, -1]))
    k = rng.choice([OUT_MAX, OUT_MIN, OUT_MAX - 1, OUT_MIN + 1])   # at the saturation boundary
    return terms_for_sum(rng, k * 16384 + rng.randint(-8200, 8200))


def directed():
    sums = []
    sums.append([(3, 0x4000)])                     # line 0: 3 x 1.0 = 3
    sums.append([(IN_MAX, IN_MAX)])
    sums.append([(IN_MIN, IN_MIN)])
    sums.append([(IN_MIN, IN_MAX)])
    sums.append([(IN_MAX, IN_MIN)])
    sums.append([(-1, 1)])
    sums.append([(-1, -1)])
    sums.append([(-1, 0x4000)])
    sums.append([(1, 0x4000)])
    sums.append([(0, 0)])
    sums.append([(IN_MIN, 0x4000)])                # -32768 x 1.0
    sums.append([(IN_MAX, 0x4000)])
    sums.append([(IN_MIN, -0x4000)])               # +32768 x 1.0: saturates high
    sums.append([(0x4000, 0x4000)] * 4)            # 4 x 16384 x 1.0 = 65536: saturates high
    sums.append([(-0x4000, 0x4000)] * 3)
    sums.append([(IN_MIN, IN_MAX)] * 16)           # long sum, big negative value
    sums.append([(IN_MIN, IN_MIN)] * 16)           # long sum, big positive value
    sums.append([(IN_MAX, IN_MIN)] * 8 + [(IN_MIN, IN_MAX)] * 8)
    sums.append([(IN_MAX, IN_MAX), (IN_MIN, IN_MAX), (1, 1)])
    for s in (8192, -8192, 8193, -8193, 8191, -8191, 24576, -24576, 40960, -40960,
              0, 1, -1, 16384, -16384, 16383, -16383,
              OUT_MAX * 16384 + 8191, OUT_MAX * 16384 + 8192, OUT_MAX * 16384 + 8193,
              OUT_MIN * 16384 - 8192, OUT_MIN * 16384 - 8193, OUT_MIN * 16384 + 8191,
              (OUT_MAX + 1) * 16384, OUT_MIN * 16384, 2**29, -2**29):
        sums.append(terms_for_sum(random.Random(s), s))
    return sums


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "sim"
    os.makedirs(out, exist_ok=True)
    rng = random.Random(SEED)
    sums = directed() + [random_sum(rng) for _ in range(N_RANDOM)]
    n_beat = 0
    n_sat = n_tie = 0
    with open(os.path.join(out, "mac_in.hex"), "w") as fi, open(os.path.join(out, "mac_exp.hex"), "w") as fe:
        for si, terms in enumerate(sums):
            for i, (a, b) in enumerate(terms):
                assert IN_MIN <= a <= IN_MAX and IN_MIN <= b <= IN_MAX, (a, b)
                flags = (1 if i == 0 else 0) | (2 if i == len(terms) - 1 else 0)
                gap = 0 if si == 0 else (0 if rng.random() < 0.7 else rng.choice([1, 1, 2, 3, 15]))
                fi.write("%x%x%04x%04x\n" % (flags, gap, a & 0xFFFF, b & 0xFFFF))
                n_beat += 1
            exact = sum(a * b for a, b in terms)
            r = mac_ref(terms)
            fe.write("%04x\n" % (r & 0xFFFF))
            if r in (OUT_MAX, OUT_MIN):
                n_sat += 1
            if exact % 16384 == 8192:
                n_tie += 1
    print("wrote %d sums, %d beats (%d saturate, %d exact ties) to %s" % (len(sums), n_beat, n_sat, n_tie, out))


if __name__ == "__main__":
    main()
