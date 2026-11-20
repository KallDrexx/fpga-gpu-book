#!/usr/bin/env python3
"""ref_divider.py -- test vectors for tb_seq_divider (Chapter 6.2).

Usage (from the repo root, once; commit the five files it writes):
    .venv/bin/python sim/py/ref_divider.py sim

Writes sim/div_<cfg>.hex for each configuration in CONFIGS.
The first line is a header, {NUM_W, DEN_W, FRAC, Q_W} (8 bits each) and the number of vectors
(32 bits), all hex; the testbench checks it against its parameters. Then one division per line,
one hex number whose bit fields are, from the top:
    flags [7:0]   bits 7:4 = idle clocks before the start (0..15); bit 0 = the testbench may pulse
                  i_start with junk while the divider is busy
    num   [NUM_W bits]  two's complement
    den   [DEN_W bits]  two's complement
    quot  [Q_W bits]    expected o_quot, two's complement
    ovf   [1 bit]       expected o_ovf

The model (div_ref below) is the specification:
    q = floor(num * 2^FRAC / den + 1/2)      (round half up, exactly as Chapter 6.1)
    q is clamped to [-2^(Q_W-1), 2^(Q_W-1) - 1]; ovf = 1 if the clamp changed it
    den == 0 -> q = Q_MIN if num < 0 else Q_MAX (so 0/0 = Q_MAX), ovf = 1
Importable: later Phase 6 models call div_ref(num, den, frac, q_w).
"""
import os
import random
import sys

sys.dont_write_bytecode = True

# name: (NUM_W, DEN_W, FRAC, Q_W, random vectors)
CONFIGS = {
    "wide":  (36, 34, 16, 34, 15000),  # 6.3-sized: a wide numerator over a 34-bit area2
    "recip": (2, 34, 32, 34, 6000),    # a reciprocal 1/den: the smallest numerator, many fraction bits
    "vtx":   (26, 16, 4, 20, 15000),   # 6.5-sized: (f*x)/z with sub-pixel bits
    "tiny":  (6, 5, 2, 7, 0),          # exhaustive: every num, den pair
    "int":   (5, 5, 0, 8, 0),          # exhaustive, FRAC = 0, Q_W wider than the quotient needs
}
SEED = 6262


def div_ref(num, den, frac, q_w):
    """Returns (quotient, ovf) for the signed divide of Chapter 6.2."""
    qmax, qmin = (1 << (q_w - 1)) - 1, -(1 << (q_w - 1))
    if den == 0:
        return (qmin if num < 0 else qmax), 1
    if den < 0:
        num, den = -num, -den
    q = ((num << (frac + 1)) + den) // (2 * den)       # floor(num*2^frac/den + 1/2), den > 0
    if q > qmax:
        return qmax, 1
    if q < qmin:
        return qmin, 1
    return q, 0


def rnd_mag(rng, bits):
    """Signed value with a log-uniform magnitude of up to `bits` bits (always representable)."""
    b = rng.randint(0, bits - 1)
    v = rng.randint(-(1 << b), (1 << b) - 1) if b else rng.randint(-1, 0)
    return max(-(1 << (bits - 1)), min((1 << (bits - 1)) - 1, v))


def clampw(v, bits):
    return max(-(1 << (bits - 1)), min((1 << (bits - 1)) - 1, v))


def vectors(name, rng):
    nw, dw, frac, qw, nrand = CONFIGS[name]
    nmax, nmin = (1 << (nw - 1)) - 1, -(1 << (nw - 1))
    dmax, dmin = (1 << (dw - 1)) - 1, -(1 << (dw - 1))
    qmax = (1 << (qw - 1)) - 1
    out = []
    if nrand == 0:                                          # exhaustive
        for n in range(nmin, nmax + 1):
            for d in range(dmin, dmax + 1):
                out.append((n, d))
        rng.shuffle(out)
        return out
    ns = sorted({0, 1, -1, 2, -2, 3, -3, nmax, nmin, nmax - 1, nmin + 1, 5, -5}, key=abs)
    ns = [clampw(n, nw) for n in ns]
    ds = [clampw(d, dw) for d in (1, -1, 2, -2, 3, -3, 4, 7, dmax, dmin, dmax - 1, dmin + 1, 0, 1 << (dw - 2), -(1 << (dw - 2)))]
    for n in ns:                                            # directed cross product
        for d in ds:
            out.append((n, d))
    for _ in range(nrand):
        k = rng.randrange(8)
        if k <= 2:                                          # varied magnitudes
            n, d = rnd_mag(rng, nw), rnd_mag(rng, dw)
        elif k == 3:                                        # full range
            n, d = rng.randint(nmin, nmax), rng.randint(dmin, dmax)
        elif k == 4:                                        # exact multiples and neighbours: remainder 0, +-1
            d = rnd_mag(rng, dw) or 1
            m = rng.randint(-8, 8) if frac == 0 else rng.randint(-(1 << 6), 1 << 6)
            n = clampw((m * d) >> frac if frac else m * d, nw) + rng.choice([0, 0, 1, -1])
            n = clampw(n, nw)
        elif k == 5:                                        # exact rounding ties: 2*num*2^frac = (2m+1)*den
            d = rng.choice([2, 4, 6, 8, 2 << frac, 3 << frac, 1 << (frac + 1)]) * rng.choice([1, -1])
            d = clampw(d, dw) or 2
            n = clampw(rng.randint(-40, 40), nw)
        elif k == 6:                                        # quotient right at the clamp limits
            d = rnd_mag(rng, dw) or 1
            tgt = rng.choice([qmax, -qmax - 1, qmax + 1, -qmax - 2, qmax - 1, qmax * 2])
            n = clampw(((tgt * d) >> frac) + rng.choice([-1, 0, 0, 1]), nw)
        else:                                               # small divisors
            d = rng.choice([1, -1, 2, -2, 3, -3, rng.randint(-20, 20)])
            n = rnd_mag(rng, nw)
        out.append((n, clampw(d, dw)))
    # add exact ties built directly: num*2^frac*2 == (2m+1)*den  ->  den = 2^(frac+1)*num/(2m+1) when divisible
    for _ in range(nrand // 20):
        odd = rng.choice([1, 3, 5, 7, 9, 15, 33])
        n = rnd_mag(rng, nw)
        d = (n << (frac + 1)) // odd if n else 0
        if d and (n << (frac + 1)) % odd == 0:
            out.append((n, clampw(d, dw)))
            out.append((-n if n != nmin else n, clampw(d, dw)))
    return out


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "sim"
    os.makedirs(outdir, exist_ok=True)
    rng = random.Random(SEED)
    for name, (nw, dw, frac, qw, _) in CONFIGS.items():
        vs = vectors(name, rng)
        word = 8 + nw + dw + qw + 1
        digits = (word + 3) // 4
        n_ovf = n_tie = n_dz = 0
        with open(os.path.join(outdir, "div_%s.hex" % name), "w") as f:
            # header line: the widths and the vector count, which the testbench checks
            f.write("%02x%02x%02x%02x%08x\n" % (nw, dw, frac, qw, len(vs)))
            for i, (n, d) in enumerate(vs):
                assert -(1 << (nw - 1)) <= n < (1 << (nw - 1)) and -(1 << (dw - 1)) <= d < (1 << (dw - 1)), (name, n, d)
                q, ovf = div_ref(n, d, frac, qw)
                gap = 0 if i == 0 else (0 if rng.random() < 0.6 else rng.choice([1, 1, 2, 3, 15]))
                junk = 0 if i == 0 else (1 if rng.random() < 0.7 else 0)
                w = (((gap << 4) | junk) << (nw + dw + qw + 1))
                w |= (n & ((1 << nw) - 1)) << (dw + qw + 1)
                w |= (d & ((1 << dw) - 1)) << (qw + 1)
                w |= (q & ((1 << qw) - 1)) << 1
                w |= ovf
                f.write("%0*x\n" % (digits, w))
                n_ovf += ovf
                n_dz += d == 0
                if d and ((n << (frac + 1)) % (2 * abs(d)) == abs(d)):
                    n_tie += 1
        print("%-5s NUM_W=%d DEN_W=%d FRAC=%d Q_W=%d: %d vectors (%d ovf, %d div by zero, %d exact ties) -> div_%s.hex"
              % (name, nw, dw, frac, qw, len(vs), n_ovf, n_dz, n_tie, name))


if __name__ == "__main__":
    main()
