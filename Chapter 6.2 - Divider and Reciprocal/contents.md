# Chapter 6.2 - Divider and Reciprocal

Chapter 6.1 gave the GPU a fast multiplier. Two places in Phase 6 need the opposite operation:

- **Chapter 6.3** divides by the triangle's area in every triangle setup, to turn attribute differences into per-pixel steps.
- **Chapter 6.5** divides by z for every vertex: x' = f·x / z, y' = f·y / z. This is what makes distant objects smaller.

This chapter builds one small divider, `seq_divider`, and fixes its rules: rounding, divide by zero, overflow, fraction bits and the `busy`/`done` handshake. Like 6.1 it runs in simulation and synthesis only.

By the end you will have:

- `rtl/common/seq_divider.v`, a signed sequential divider with parameterized widths,
- `sim/py/ref_divider.py`, the Python model (`div_ref`), which writes five vector files,
- `sim/tb_seq_divider.v` passing: about 40,000 divisions in five configurations, two of them exhaustive,
- a yosys report showing a few hundred LUTs and no DSP block.

## 1. Why division is expensive

A multiplier is a grid of independent partial products that are summed in one pass, which is why the FPGA can afford a hard 25x18 multiplier block. Division has no such structure. Each quotient bit depends on the remainder left by the bit before it: you cannot decide bit 5 until you know what bit 6 left over. The work is a chain, not a grid. A combinational 34-bit divider would be 34 subtractors in series, far too slow for one clock.

Division is also rare. You need a few per triangle and one or two per vertex, never one per pixel. So the right design is a small, slow unit that is cheap and easy to get exactly right.

## 2. Long division in binary

School long division brings down a digit, asks how many times the divisor fits, and subtracts. In binary the answer is only 0 or 1, so each step is one compare and one subtract.

**Restoring division** of unsigned numbers keeps a remainder `rem`, starting at 0, and takes the dividend bits most significant first:

1. Shift `rem` left and bring in the next dividend bit: `t = {rem, next_bit}`.
2. If `t >= den`, the quotient bit is 1 and `rem = t - den`. Otherwise the quotient bit is 0 and `rem = t` (the "restore").
3. After the last bit, the quotient bits form the quotient and `rem` is the remainder, 0 <= rem < den.

13 / 3 with the 4-bit dividend `1101`:

| step | bit in | t | t >= 3? | quotient bit | rem |
|---|---|---|---|---|---|
| 1 | 1 | 1 | no | 0 | 1 |
| 2 | 1 | 3 | yes | 1 | 0 |
| 3 | 0 | 0 | no | 0 | 0 |
| 4 | 1 | 1 | no | 0 | 1 |

Quotient `0100` = 4, remainder 1: 13 = 4·3 + 1.

**In hardware**, one step is a subtractor (its borrow out is the compare) and a 2-to-1 mux, and the clocked version does one step per clock. The dividend sits in a shift register that moves left each clock, and each new quotient bit enters at its bottom, so after N clocks the dividend has shifted out and the quotient has shifted in: one register holds both.

**Non-restoring division** skips the mux: when `t - den` went negative it keeps the negative value and adds `den` in the next step instead of subtracting. It needs a correction at the end to get a true remainder. On an FPGA the compare and the subtract already share one carry chain, so it saves little; build whichever you find clearer. Doing **two steps per clock** (two subtractors in series) halves the latency for about a third more LUTs. The testbench accepts all of these.

## 3. The alternative: reciprocal plus Newton-Raphson

Many CPUs and GPUs compute 1/den and multiply:

1. **Normalize.** Shift `den` left until its top bit is set, so that it is d·2^k with d in [1, 2). This needs a leading-zero count and a barrel shifter.
2. **Table.** Look up `r0 ≈ 1/d` in a ROM indexed by the top bits of d. A 256-entry table (one block RAM) gives a relative error of about 2^−9.
3. **Newton step.** `r1 = r0 · (2 − d · r0)`: two multiplies and a subtract. If `r0` has relative error e, `r1` has error e², so one step turns about 9 correct bits into about 17.
4. **Multiply and shift back.** `q = num · r1`, shifted by k.

That is roughly 8 clocks for any width, and it pipelines to one divide per clock. But the result is an approximation whose last bit or two depend on the table contents and on where each intermediate product is truncated. A Python model must copy all of those details to be bit-exact, and then it describes "this algorithm's answer" rather than "the right answer". Getting the exactly rounded quotient takes another correction step (multiply back, compare, adjust by one).

| | Sequential (restoring) | Table + Newton |
|---|---|---|
| Latency | N + 2 clocks (32 and 54 for the two uses sized in Section 6) | about 8 clocks |
| Throughput | one divide per latency | one per clock, pipelined |
| Resources | a few hundred LUTs, no DSP, no RAM | 1 RAMB18, 2 to 3 DSP blocks, normalizer |
| Result | exactly the rounded quotient | within 1 to 2 LSB, algorithm-dependent |
| Python model | one line | table, widths and truncations replicated |

**Build the sequential divider.** Section 6 shows its latency is affordable: well under 1 ms of a 33 ms frame at 30 frames per second, for both uses. Its result is the mathematically rounded quotient, so the models of 6.3 and 6.5 call one line of Python, and any disagreement with the RTL is a bug, not a table difference. A divide per pixel (perspective-correct texturing, a stretch goal) is where Newton earns its place.

## 4. The rules

These are exact; the Python model and the testbench follow them.

**Signed operands.** Divide the magnitudes, then fix the sign; the result is negative when the signs differ. The magnitude of the most negative W-bit number, 2^(W−1), still fits in W bits read as unsigned, so negate in W bits and treat the result as unsigned. No extra bit is needed.

**Fraction bits.** The divider computes `num · 2^FRAC / den`, so the result has FRAC fraction bits. In hardware that is FRAC zero bits appended below the dividend: the loop runs N = NUM_W + FRAC steps. FRAC = 0 is plain integer division. Example: 1/3 with FRAC = 8 is 85, which is 0.332.

**Rounding: half up, as in Chapter 6.1.**

> q = floor( num · 2^FRAC / den + 1/2 )

The remainder makes exact rounding cheap: one compare and one increment, and it halves the error of truncation. Round half away from zero would fall out of magnitude division more naturally, but Phase 6 uses one rounding rule everywhere, and the price is one detail in the sign fix.

With magnitude quotient `m` and remainder `r` (the exact magnitude is m + r/|den|):

- a **positive** result rounds the magnitude up when `2r >= |den|`;
- a **negative** result is −(m + r/|den|), and half up sends ties toward +∞, that is toward the *smaller* magnitude, so it rounds the magnitude up only when `2r > |den|`.

| num / den (FRAC = 0) | exact | result |
|---|---|---|
| 7 / 2 | 3.5 | 4 |
| −7 / 2 | −3.5 | −3 |
| 7 / −2 | −3.5 | −3 |
| −7 / 3 | −2.33 | −2 |
| −8 / 3 | −2.67 | −3 |

In Python, with `den > 0` (negate both operands when it isn't): `q = ((num << (FRAC + 1)) + den) // (2 * den)`. Python's `//` floors, which is exactly what the formula needs.

**Saturation.** The result is clamped to `Q_W` bits, `Q_MIN = −2^(Q_W−1)` to `Q_MAX = 2^(Q_W−1) − 1`, and `o_ovf` is 1 when the clamp changed the value. Exactly `Q_MIN` is representable and does not set `o_ovf`. In the finish step that means: a positive magnitude overflows at `>= 2^(Q_W−1)`, a negative one only at `> 2^(Q_W−1)`. With `Q_W = NUM_W` and `FRAC = 0`, the most negative numerator divided by −1 overflows and gives `Q_MAX`.

**Divide by zero** is defined as the limit: `Q_MAX` if `num >= 0` (so 0/0 is `Q_MAX`), `Q_MIN` if `num < 0`, with `o_ovf = 1`. The loop runs anyway and its garbage is replaced at the end, so a zero divisor never hangs and never needs a special case in the caller.

## 5. The `seq_divider` module

```verilog
module seq_divider #(
    parameter NUM_W = 16, DEN_W = 16, FRAC = 0, Q_W = 16
) (
    input  wire                    i_clk,
    input  wire                    i_start,   // accepted only when o_busy is low
    input  wire signed [NUM_W-1:0] i_num,
    input  wire signed [DEN_W-1:0] i_den,
    output wire                    o_busy,    // high from the clock after a start until the clock before o_done
    output wire                    o_done,    // one clock per division
    output wire signed [Q_W-1:0]   o_quot,    // clamp(floor(num * 2^FRAC / den + 1/2))
    output wire                    o_ovf      // the clamp changed the result, or den == 0
);
```

**Handshake.**

- A clock with `i_start` high and `o_busy` low **starts** a division. `i_num` and `i_den` are captured on that edge and may change afterwards.
- `i_start` while `o_busy` is high is **ignored**: no restart, no queued request.
- `o_done` is high for exactly one clock, with `o_busy` low. A start on the done clock is accepted, so divisions can run back to back.
- `o_quot` and `o_ovf` are valid on the done clock and hold until the next `o_done`.
- Callers wait for `o_done`, never count clocks. The reference takes N + 2 clocks every time, but a different latency, even one that varies (say, a divide by zero that finishes at once), is legal.
- No reset: registers have initial values, and `o_busy` and `o_done` start at 0.

**Inside.** The reference keeps the dividend/quotient shift register (N bits), the denominator magnitude and the remainder (DEN_W bits each: the remainder is always below the denominator, and only the shifted value `t` needs DEN_W + 1 bits), a step counter, and three flags: result sign, numerator sign and divide by zero. One division is:

1. **Capture** (the start clock): magnitudes, flags, counter = N, remainder = 0, busy.
2. **Iterate** N clocks, as in Section 2.
3. **Finish** (one clock): add the rounding bit to the magnitude, compare it with the limit, negate if needed, and load `o_quot` and `o_ovf`, or the divide-by-zero result. Raise done, drop busy. The magnitude plus one needs `max(N, Q_W) + 1` bits.

Watch signedness (Chapter 6.1, Section 4): the magnitudes, the remainder and the limit compare are unsigned; only the inputs and the final result are signed.

## 6. Precision is the caller's choice

The divider rounds each quotient to `FRAC` bits, but whether that is precise enough depends on what is done with it next. The case that matters is a quotient that gets multiplied or added up many times.

**Attribute steps (Chapter 6.3).** A gradient such as dz/dx is a numerator built from attribute and coordinate differences, divided by `area2`, the rasterizer's doubled area. The inner loop adds the step once per pixel, so a step error e grows to S·e after S steps; across a 320x240 bounding box S is at most 560. Two ways to get the steps:

- **Divide each gradient directly.** The step is exactly rounded to FRAC bits, so its error is at most 2^−(FRAC+1) whatever the triangle's size. FRAC = 16 keeps the drift after 560 steps below 0.005 of a depth or intensity unit. Four divides per triangle (dI/dx, dI/dy, dz/dx, dz/dy).
- **One reciprocal 1/area2, then multiply.** One divide instead of four, but a fixed-point reciprocal has fixed *absolute* precision, so its *relative* precision falls as the area grows. `area2` reaches 2^32 for a triangle with corners off the screen (Chapter 4.6), where a Q2.32 reciprocal is 0 or 1 and carries no information. Even an on-screen triangle (`area2` up to 76,800 ≈ 2^16) with a Q2.32 reciprocal drifts by about one depth unit across 560 steps. Keeping the drift small for every legal triangle takes over 40 fraction bits and a much wider multiply, or normalizing the area first (a floating-point reciprocal).

Dividing each gradient directly is the simpler path to exact, area-independent steps; Chapter 6.3 makes the final choice and sets the widths. For scale: a depth-gradient numerator needs about 35 signed bits and `area2` 34, so `#(NUM_W = 36, DEN_W = 34, FRAC = 16, Q_W = 34)` has N = 52 and takes 54 clocks. Four in a row are 216 clocks per triangle, 0.86 ms for 200 triangles at 50 MHz. That is small against a 33 ms frame, but large against the roughly 500 clocks it takes to draw an average triangle, so 6.3 may want two dividers or setup that overlaps the previous triangle's drawing. `area2 = 0` (degenerate) gives a saturated, flagged result; the rasterizer skips those triangles anyway.

**Perspective divide (Chapter 6.5).** x' = f·x / z gives a screen coordinate, used once, so FRAC is just the number of sub-pixel bits to keep. The widths depend on formats 6.5 has not fixed yet: the transformed coordinates may keep fraction bits from the MAC, x after a rotation and translation can need a few more bits than 16, and f (the focal length in pixels, about 277 for a 60° field of view on a 320-pixel-wide screen) adds about 9 bits. As an example with 16-bit integer x and z: `#(NUM_W = 26, DEN_W = 16, FRAC = 4, Q_W = 20)`, N = 30, 32 clocks. Two dividers, for x and y, started together, give one vertex per 32 clocks (the MAC's 12 clocks per vertex overlap): 1,024 vertices in 0.66 ms, a 200-triangle model's hundred or so vertices in under 0.1 ms. A vertex at or behind the near plane gives a meaningless or saturated result; Chapter 6.6 rejects those triangles by testing z, not `o_ovf`.

If a later chapter picks widths far outside the testbench's configurations (Section 7), it should add a configuration to `ref_divider.py` and the testbench.

## 7. The Python model and test vectors

`sim/py/ref_divider.py` is the specification. `div_ref(num, den, frac, q_w)` returns `(quotient, ovf)` and is importable: Chapters 6.3 and 6.5 build their models on it. Running the script writes `sim/div_<cfg>.hex` for five configurations:

| config | NUM_W, DEN_W, FRAC, Q_W | content |
|---|---|---|
| `wide` | 36, 34, 16, 34 | sized like a 6.3 gradient divide, about 15,000 vectors |
| `recip` | 2, 34, 32, 34 | a reciprocal 1/den (the smallest numerator), about 6,000 |
| `vtx` | 26, 16, 4, 20 | sized like a 6.5 perspective divide, about 15,000 |
| `tiny` | 6, 5, 2, 7 | every numerator and denominator, 2,048 |
| `int` | 5, 5, 0, 8 | every pair, plain integers, quotient wider than needed |

The exhaustive configurations cover every sign combination, tie, overflow and divide by zero at their widths. The large ones check the real widths with log-uniform random magnitudes (so small divisors and quotients are common), exact multiples of the divisor and their neighbors, exact rounding ties of both signs, quotients at the clamp limits (`Q_MAX`, `Q_MAX + 1`, `Q_MIN`, `Q_MIN − 1`), and a cross product of numerators {0, ±1, ±2, ±3, ±5, most positive, most negative, and neighbors} with denominators {±1, ±2, ±3, 4, 7, ±2^(DEN_W−2), most positive, most negative, neighbors, 0}.

## 8. Simulate

```sh
.venv/bin/python sim/py/ref_divider.py sim
make test TESTS=tb_seq_divider
```

Typical failures:

- **Wrong for negative results only:** the sign taken from one operand, or `>=` in the tie test where a negative result needs `>`.
- **Wrong exactly at the extremes:** the most negative operand negated into a width that loses it, or a signed compare on a magnitude.
- **Off by one everywhere:** one step too few or too many, or no rounding bit.
- **`o_ovf` wrong but `o_quot` right:** `Q_MIN` flagged as overflow, or divide by zero not flagged.
- **"o_busy not high while dividing" or "no o_done in time":** `o_busy` dropped early, or a state machine that never finishes.
- **Wrong results after a busy period with junk starts:** `i_start` not ignored while busy.

## 9. Check the resources

The `make dsp-%` target from Chapter 6.1 synthesizes the default parameters; to see a real size, set them with `chparam` (in a shell where openXC7's `export.sh` is sourced):

```sh
yosys -q -p "read_verilog -sv rtl/common/seq_divider.v; \
  chparam -set NUM_W 36 -set DEN_W 34 -set FRAC 16 -set Q_W 34 seq_divider; \
  synth_xilinx -flatten -abc9 -arch xc7 -top seq_divider; tee -o build/div.stat stat"
```

A reference design gives:

| config | LUTs | flip-flops | CARRY4 |
|---|---|---|---|
| `wide` | about 280 | 168 | 63 |
| `recip` | about 240 | 150 | 49 |
| `vtx` | about 160 | 96 | 37 |

No DSP and no RAM. The CARRY4 blocks are the loop's subtractor, the rounding compare and increment, the limit compare and the negations. The two-steps-per-clock version is about a third larger. Your counts will differ; what matters is no DSP, and no pile of LUTs big enough to be a multiplier. Timing is no concern: the loop is one 35-bit subtract and a mux, and the finish step a few carry chains in a row; the reference `wide` configuration, placed and routed alone, reaches about 100 MHz.

**Done when:** `tb_seq_divider` passes (all five configurations bit-exact against `div_ref`, including tiny divisors, negatives, the largest values, ties, overflow and divide by zero), and the synthesis report shows a few hundred LUTs and no DSP block.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **The divider:** `seq_divider #(NUM_W, DEN_W, FRAC, Q_W)(i_clk, i_start, i_num, i_den, o_busy, o_done, o_quot, o_ovf)`, sequential, no DSP; the reference takes `NUM_W + FRAC + 2` clocks. Callers wait for `o_done`, never count clocks.
- **Handshake:** a start is accepted only while `o_busy` is low (otherwise ignored); `o_done` is one clock with `o_busy` low, so back-to-back starts are legal; `o_quot`/`o_ovf` hold until the next `o_done`.
- **Quotient:** `floor(num · 2^FRAC / den + 1/2)` (round half up, as in 6.1), saturated to `Q_W` bits with `o_ovf = 1`; exactly `Q_MIN` is not an overflow. Divide by zero: `Q_MAX` for `num >= 0`, `Q_MIN` for `num < 0`, `o_ovf = 1`.
- **Precision:** a reciprocal in fixed point loses relative precision as the divisor grows (`area2` reaches 2^32). Chapter 6.3 chooses between one divide per gradient (suggested; for example `#(36, 34, 16, 34)`, 54 clocks each) and a reciprocal with enough bits. Chapter 6.5 sizes its perspective divide from its transform format (for example two `#(26, 16, 4, 20)` instances, 32 clocks per vertex).
- **Models:** `sim/py/ref_divider.py` exports `div_ref(num, den, frac, q_w)` returning `(quotient, ovf)`.

## Testbench requirements

**Module:** `seq_divider #(NUM_W, DEN_W, FRAC, Q_W)` in `rtl/common/seq_divider.v`, ports as in Section 5: `i_clk`, `i_start`, signed `i_num[NUM_W-1:0]`, signed `i_den[DEN_W-1:0]`, `o_busy`, `o_done`, signed `o_quot[Q_W-1:0]`, `o_ovf`. No reset. The testbench instantiates the five configurations of Section 7.

- **Function:** `o_quot = clamp(floor(num · 2^FRAC / den + 1/2), −2^(Q_W−1), 2^(Q_W−1) − 1)`; `o_ovf` = 1 when the clamp changed the value. If `den = 0`: `Q_MIN` for `num < 0`, otherwise `Q_MAX`, with `o_ovf = 1`.
- **Vectors:** random and directed cases as in Section 7, including tiny divisors, all sign combinations, zero numerator, the most negative numerator and denominator, divide by zero, ties and results that overflow `Q_W`.
- **Latency:** any latency from 1 to 200 clocks, measured per division from the start clock to `o_done`; it may differ between divisions.
- **Handshake:** `o_busy` high on every clock between the start and the done clock, low on the done clock and when idle; `o_done` high for exactly one clock per division. Each start comes on the done clock of the previous division (back to back) or after 1 to 15 idle clocks with junk on `i_num` and `i_den`.
- **Start while busy:** while busy, `i_start` is pulsed at random with junk operands; the running division must be unaffected.
- **Outputs:** `o_quot` and `o_ovf` equal the model on the done clock and stay unchanged until the next `o_done`.

**Files:** `tb_seq_divider.v` goes to `sim/`, `ref_divider.py` to `sim/py/`. Run `.venv/bin/python sim/py/ref_divider.py sim` once and commit the five `sim/div_*.hex` files (about 0.9 MB). Each file starts with a header line holding its widths and vector count; the testbench FAILs if the widths don't match its parameters or the file is short, so changing the generator needs no edits to the testbench.

**Running:** about 15 seconds. The testbench uses a 50 MHz clock, drives and samples on the falling edge, reads `../../sim/div_<cfg>.hex` (`+dir=PATH/` for another folder), prints at most five error lines per configuration and then one `PASS` or `FAIL` line, with the latencies it saw; the watchdog is 800 ms of simulated time. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y rtl/xc7 -y sim -o build/sim/tb_seq_divider.vvp sim/tb_seq_divider.v
cd build/sim && vvp -n tb_seq_divider.vvp
```
