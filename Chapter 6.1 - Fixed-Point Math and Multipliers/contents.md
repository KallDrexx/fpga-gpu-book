# Chapter 6.1 - Fixed-Point Math and Multipliers

Until now every datapath got by with integer adds and compares. 3D needs real arithmetic: a rotation matrix has entries like 0.866, each transformed coordinate is a sum of three products, and lighting multiplies a surface normal by a light direction. The FPGA has no floating-point unit, and you don't want one. It does have 220 hard multiplier blocks, so Phase 6 uses **fixed-point numbers** and one small pipelined **multiply-accumulate unit** (MAC) that the later chapters reuse.

This chapter builds that unit, `mac_unit`, and fixes the number formats for the rest of the phase. It runs in simulation and synthesis only; Chapter 6.5 puts it to work in the vertex transform.

By the end of this chapter you will have:

- `rtl/common/mac_unit.v`, a pipelined signed multiply-accumulate with a rounded, saturated output,
- `sim/py/ref_mac.py`, the Python fixed-point model, which writes `sim/mac_in.hex` and `sim/mac_exp.hex`,
- `sim/tb_mac_unit.v` passing: about 100,000 random sums plus edge cases match the model bit for bit,
- a `make dsp-mac_unit` target whose report shows one `DSP48E1`.

## 1. Q formats

A fixed-point number is an integer with an agreed position for the binary point. **Qm.n** is a signed two's complement number of m + n bits: m integer bits (including the sign bit) and n fraction bits. The stored integer `v` stands for `v / 2^n`.

- **Range:** −2^(m−1) to 2^(m−1) − 2^(−n). The most negative value has no positive twin.
- **Resolution:** 2^(−n), the value of one LSB.
- Two numbers in the same format add as plain integers. The hardware doesn't know where the point is; only your comments and your Python model do.

| Format | Bits | Range | Resolution | Used for |
|---|---|---|---|---|
| Q16.0 | 16 | −32768 to 32767 | 1 | vertex coordinates, translations |
| Q2.14 | 16 | −2 to 1.99994 | 2^−14 ≈ 0.000061 | matrix entries, normals, light direction |
| Q16.16 | 32 | −32768 to 32767.99998 | 2^−16 | values that need sub-pixel precision |

**Why Q2.14 and not Q1.15 for matrix entries?** A rotation entry lies in [−1, 1], and Q1.15 would give it one more bit of precision. But Q1.15 cannot store +1.0 (its largest value is 0.99997), so even the identity matrix would shrink every model slightly. Q2.14 stores 1.0 exactly as `0x4000`, holds every rotation entry, and leaves room for a uniform scale up to just under 2.

| Value | −2.0 | −1.0 | −0.5 | 0.5 | 1.0 | cos 30° | largest |
|---|---|---|---|---|---|---|---|
| Q2.14 hex | `8000` | `C000` | `E000` | `2000` | `4000` | `376D` (14189) | `7FFF` |

cos 30° × 16384 = 14188.96, which rounds to 14189. Converting a real number to fixed point always costs up to half an LSB. That error belongs to the stored data and is separate from the rounding the hardware does later.

## 2. Multiplying and accumulating: how many bits

Multiply a Qa.b number by a Qc.d number as plain integers and you get a **Q(a+c).(b+d)** number: (v₁/2^b)·(v₂/2^d) = v₁v₂/2^(b+d). The fraction bits add. Two N-bit signed numbers always give a product that fits in 2N bits; the only product that needs the full 2N bits is (−2^(N−1))² = 2^(2N−2).

So a Q2.14 matrix entry times a Q16.0 coordinate is a Q18.14 product in 32 bits. Keep all 14 fraction bits while you add the products of a sum, and drop them once, at the end.

**Accumulator growth.** A sum of K products can need up to ⌈log₂K⌉ more bits than one product. These extra bits are **guard bits**: they let a partial sum overshoot without wrapping, so that only the final saturation decides what happens to an out-of-range result. This chapter's accumulator is 48 bits, the width of the DSP block's accumulator. The largest product magnitude is 2^30, so up to 2^47 / 2^30 = 2^17 worst-case products (about 131,000) fit before the sum can wrap. Phase 6 sums have four.

## 3. Rounding

To go from Q18.14 back to Q16.0 you drop 14 fraction bits. There are three common ways:

- **Truncate:** arithmetic shift right. It costs nothing, but it is the floor of the true value: the error is between −1 and 0 LSB, about −0.5 LSB on average, for every value of either sign. That systematic offset accumulates wherever results feed further arithmetic.
- **Round half up:** add half an LSB (2^(n−1)), then shift right. The error is in [−0.5, +0.5). Exact ties go toward +∞.
- **Round half to even (convergent):** like round half up, but an exact tie goes to the even neighbor, so ties have zero average bias. It needs extra logic to detect a tie.

An example: 0.5 (`0x2000`) times 101 is 827392, which is 50.5 in Q18.14.

| | +50.5 | −50.5 |
|---|---|---|
| Truncate | 50 | −51 |
| Round half up | 51 | −50 |
| Round half away from zero | 51 | −51 |
| Round half to even | 50 | −50 |

Round half up is not symmetric (negating the input does not negate the output on a tie), but its error is never more than half an LSB, and the only bias is on exact ties.

**This book rounds half up:** `result = (sum + 2^(FRAC−1)) >>> FRAC`. The arithmetic shift `>>>` rounds toward −∞, which is exactly what makes "add half, then shift" correct for negative values too, with no special case. It costs one constant add and it is a one-liner in Python, whose `>>` also floors. Every Phase 6 model uses it. Implementing convergent rounding is a good exercise once you pass, but the testbench will (correctly) report the differences on ties, so keep round half up in the shipped version.

## 4. Saturation

The rounded result is still wider than the output, and an out-of-range value has to go somewhere. **Wrapping** (dropping the high bits) sends a vertex that is slightly too far to the right to the far left. **Saturating** clamps it to the largest or smallest output value, so the error stays small. Saturate after rounding: a sum of 32767.6 rounds to 32768 and then clamps to 32767.

In Verilog, signedness is where saturation and accumulation go wrong. An expression is signed only if **every** operand is signed. One unsigned operand makes the whole expression unsigned: signed operands are then zero-extended, comparisons are unsigned, and `>>>` becomes a logical shift. Sized literals such as `48'd0` or `14'h2000`, part selects such as `x[15:0]`, concatenations, and anything declared without `signed` are unsigned. Unsized decimal literals such as `0` or `8192` are signed. The two traps this chapter's unit is most likely to fall into:

- **The clear mux:** `r_p <= (clear ? 48'd0 : r_p) + r_m;`. The `48'd0` makes the right-hand side unsigned, so the 32-bit product `r_m` is zero-extended, and every negative product is added as a large positive one.
- **The rounding constant:** with `localparam [47:0] HALF = 1 << 13;` (no `signed`), `(r_p + HALF) >>> 14` is unsigned and the shift is logical. A negative sum turns into a huge positive one and saturates high. The same goes for a comparison against an unsigned `OUT_MAX`: −1 is "greater" than 32767.

Declare constants and intermediate values `signed` at the full width, and wrap part selects in `$signed(...)`.

## 5. The DSP48E1

The Zynq XC7Z020 on the Arty has 220 DSP48E1 blocks. Each is a complete multiply-accumulate datapath:

```
 A (30-bit port, 25 bits into the multiplier) ─┐
                                               ├─ 25 x 18 signed multiplier ─ M ─┐
 B (18-bit) ───────────────────────────────────┘                                 ├─ 48-bit ALU ─ P
 0 / P (feedback) / C (48-bit) ──────────────────────────────────────────────────┘
```

- The multiplier is **signed 25 × 18**. A 16 × 16 product uses one block; a 32 × 32 product takes four.
- The **48-bit ALU** after it adds, subtracts or does logic, and one of its inputs can be the block's own output P. That is an accumulator with no fabric logic. The OPMODE input picks that ALU input per clock: P to continue a sum, or 0 (or the C port) to start a new one, so clearing a sum costs nothing.
- Optional **pipeline registers**: A and B at the inputs (up to two stages each), M after the multiplier, P at the output, each with its own clock enable.

The multiplier is slow; a path through it and the adder takes several nanoseconds. With the A/B, M and P registers in use, each stage is short and the block can run at a few hundred MHz. yosys builds these registers from your own flip-flops: it finds the multiply and absorbs the registers directly around it as AREG/BREG, MREG and PREG. A register stays in the fabric if there is logic between it and the multiplier, or if it has a nonzero initial value (the DSP's registers power up at 0). Worse, with the openXC7 yosys (v0.69), a nonzero-initialized register one step further out (the caller's register in front of your input register, which yosys tries to absorb as a second input stage) made synthesis stop with an assertion in `xilinx_dsp.cc`. Initialize registers that feed the multiplier to 0.

## 6. The `mac_unit` module

The unit is **serial**: one product enters per clock, and a sum is a run of products whose first and last the caller marks.

```verilog
module mac_unit #(
    parameter A_W = 16, B_W = 16, ACC_W = 48, FRAC = 14, OUT_W = 16
) (
    input  wire                    i_clk,
    input  wire                    i_valid,   // this clock carries a product
    input  wire                    i_clear,   // this product starts a new sum
    input  wire                    i_last,    // this product ends the sum
    input  wire signed [A_W-1:0]   i_a,
    input  wire signed [B_W-1:0]   i_b,
    output reg                     o_valid,   // one clock per finished sum
    output reg  signed [OUT_W-1:0] o_data     // saturate(round_half_up(sum / 2^FRAC))
);
```

**Behavior.**

- On a clock with `i_valid` high (a *beat*), `i_a * i_b` is added to the running sum. With `i_clear` also high, the old sum is discarded first. `i_clear` and `i_last` may be on the same beat (a one-product sum).
- With `i_valid` low, nothing happens, whatever `i_clear`, `i_last`, `i_a` and `i_b` hold. Idle clocks may come in the middle of a sum.
- After the beat with `i_last`, the rounded, saturated result appears with `o_valid` high for exactly one clock, after a fixed latency that is the same for every sum.
- A new beat is accepted every clock, including the first beat of the next sum right after the last beat of the previous one.
- No reset. Registers get initial values (0) at declaration, and `o_valid` stays low until the first sum is done.

**Pipeline.** The reference design has four register stages, with the flags (`valid`, `clear`, `last`) travelling alongside the data:

```
edge:      1            2         3           4
inputs -> r_a, r_b -> r_m ----> r_p ------> o_data, o_valid
          (AREG/BREG)  (MREG)   (PREG)      (round + saturate in the fabric)
```

1. Register `i_a`, `i_b` and the flags.
2. Register the product `r_a * r_b`, `A_W + B_W` bits wide.
3. On a beat, update the accumulator: `r_p <= (clear ? 0 : r_p) + product`, with the product sign-extended to `ACC_W` bits. The clear mux is the DSP's OPMODE choice, so it costs no logic.
4. If this was the last product, round, saturate, and register `o_data` and `o_valid`.

That gives a latency of 4: a beat driven before edge 1 shows up on `o_data` after edge 4. Other arrangements are fine as long as the latency is fixed; the testbench accepts any latency from 1 to 40. Modules that use `mac_unit` must take results by `o_valid`, never by counting clocks.

**Use in Chapter 6.5.** A transformed coordinate `x' = m00·x + m01·y + m02·z + tx` is four beats:

| beat | i_a (Q2.14) | i_b (Q16.0) |
|---|---|---|
| 1 (clear) | m00 | x |
| 2 | m01 | y |
| 3 | m02 | z |
| 4 (last) | `0x4000` (1.0) | tx |

The translation is a product too: tx × 1.0. With `FRAC = 14` the result is in the units of the input coordinates. Three rows are 12 clocks per vertex: 1,000 vertices take 0.24 ms at 50 MHz, against 33 ms per frame at 30 frames per second, so one serial unit is plenty (the perspective divide of Chapter 6.2 will cost more). Chapter 6.5 chooses the output format: a smaller `FRAC` keeps fraction bits and a larger `OUT_W` keeps range, which may matter for the z that goes into the divide. It must also keep models and translations small enough that transformed coordinates don't saturate in normal use; saturation is a safety net, not a clipper.

Chapter 6.7 uses the unit for lighting: a normal (Q2.14) dotted with a light direction (Q2.14) with `FRAC = 14` is again Q2.14, and for unit vectors it lies in [−1, 1].

## 7. The Python model and the test vectors

`sim/py/ref_mac.py` is the specification. The model is three lines:

```python
acc = sum(a * b for a, b in terms)                 # exact; cannot overflow 48 bits here
r = (acc + (1 << (FRAC - 1))) >> FRAC              # round half up (Python's >> floors)
result = max(OUT_MIN, min(OUT_MAX, r))             # saturate to OUT_W bits
```

Run `.venv/bin/python sim/py/ref_mac.py sim` once. It writes `sim/mac_in.hex` (one beat per line: flags, idle clocks to insert before the beat, `i_a`, `i_b`) and `sim/mac_exp.hex` (one result per sum). The sums are a mix of:

- full-range random operands, which saturate often,
- random operands of varied magnitude, which mostly don't,
- transform-shaped sums: three Q2.14 entries times coordinates, plus a translation times `0x4000`,
- **exact rounding ties** of both signs (sum = k + 0.5 output LSB, and its neighbors),
- sums right at both saturation limits (32767.5, −32768.5 and their neighbors),
- directed cases: −32768 × −32768, −32768 × 32767, −1 × 1, 0, products with `0x4000`, and 16-product sums of extreme values.

Keep the file importable: later Phase 6 models `import ref_mac` and call `mac_ref(terms, frac, out_w)`.

## 8. Simulate

```sh
.venv/bin/python sim/py/ref_mac.py sim
make test TESTS=tb_mac_unit
```

The testbench applies the first sum alone to measure the latency, then streams the rest, with junk on every input except `i_valid` during idle clocks. It prints the first few mismatches with the sum number, clock, and expected and actual values. Typical failures:

- **Wrong on large or negative sums:** an unsigned multiply or mux, a missing sign extension (Section 4), or a narrow accumulator.
- **Wrong on negative values only:** a logical shift instead of an arithmetic one, or rounding that tries to be symmetric.
- **Wrong at 32767 or −32768:** saturation missing, done before rounding, or comparing unsigned.
- **A sum that includes the previous one:** `i_clear` ignored.
- **A sum that includes junk:** idle clocks not leaving the accumulator alone.
- **"result at the wrong time" or "o_valid not low":** latency that varies, or `o_valid` raised by an idle clock's `i_last`.

## 9. Check that yosys used the DSP block

A passing simulation says nothing about the hardware: a multiplier built from a hundred LUTs and a carry chain simulates just as correctly. To see what you got, synthesize the module alone and read the cell counts. Add to the top Makefile:

```make
dsp-%:
	@mkdir -p build
	. ~/opt/openxc7/export.sh && yosys -q -p "read_verilog -sv rtl/common/$*.v; \
	  synth_xilinx -flatten -abc9 -arch xc7 -top $*; tee -o build/$*.stat stat" \
	  && grep -E "DSP48E1|CARRY4|LUT[0-9]" build/$*.stat
```

`make dsp-mac_unit` must list **1 DSP48E1**. The reference design gives

```
       13   CARRY4
        1   DSP48E1
        4   LUT2
        2   LUT4
       18   LUT5
       12   LUT6
```

and 24 flip-flops. Your counts will differ a little. What matters is one `DSP48E1` and no pile of LUTs and CARRY4s big enough to be a multiplier; the CARRY4s here are the 48-bit rounding add and the saturation check. If the DSP is missing or there are more, check for `A_W` above 25 or `B_W` above 18, logic between your input registers and the multiply, or a multiply that lost its signedness.

To see which registers went into the block, add `write_verilog -noattr build/mac_net.v` after `synth_xilinx` and look at the `DSP48E1` instance. The reference has `AREG`, `BREG`, `MREG` and `PREG` all 1 (stages 1 to 3 entirely inside the block), the delayed `valid` flag on `CEP`, and the delayed `clear` flag in `OPMODE`.

**Optional improvement.** Start each sum from 2^(FRAC−1) instead of 0: `r_p <= (clear ? HALF : r_p) + product`. The accumulator then already holds the rounding constant, and the output stage only shifts and saturates. yosys puts `HALF` on the C port and the CARRY4 count drops from 13 to 4. The result is bit-identical, so the testbench still passes.

**About Fmax.** If you place and route the unit (wrapped in a small top whose inputs come from an LFSR), nextpnr-xilinx reports a very high maximum frequency (about 530 MHz for the reference). Don't trust it for DSP paths: an unpipelined version with the multiply, accumulate and rounding all in one clock reported about 450 MHz, which real silicon cannot do. The design runs at 50 MHz, so this is not a problem, but keep the pipeline registers anyway; they cost nothing inside the block.

## Phase 6 conventions set here

Later chapters use these without restating them.

- **Formats:** model-space vertex coordinates and translations are signed 16-bit integers (Q16.0). Matrix entries, normals and light directions are Q2.14 (1.0 = `0x4000`, range [−2, 2)), so a matrix is a rotation times a uniform scale below 2; the translation column stays Q16.0. Q2.14 × Q16.0 products are Q18.14; accumulators are 48 bits.
- **Rounding:** drop fraction bits by round half up (add 2^(FRAC−1), arithmetic shift right), then saturate to the output width. This is the only rounding in Phase 6 hardware and Python models.
- **The MAC:** `mac_unit` as in Section 6: serial, one product per clock, `i_clear`/`i_last` flags, fixed latency (4 in the reference). Users take results by `o_valid`, never by counting clocks. `FRAC` and `OUT_W` are chosen per use (Chapter 6.5 picks the transform's output format); one DSP48E1 holds `A_W` ≤ 25 and `B_W` ≤ 18.
- **Synthesis:** registers that feed a multiplier start at 0.
- **Models:** `sim/py/ref_mac.py` exports `mac_ref(terms, frac=14, out_w=16)`.

## Testbench requirements

**Module:** `mac_unit #(A_W = 16, B_W = 16, ACC_W = 48, FRAC = 14, OUT_W = 16)` in `rtl/common/mac_unit.v`, ports as in Section 6: `i_clk`, `i_valid`, `i_clear`, `i_last`, signed `i_a[A_W-1:0]`, signed `i_b[B_W-1:0]`, `o_valid`, signed `o_data[OUT_W-1:0]`. No reset. The testbench uses the default parameters.

- **Function:** per sum, `o_data = clamp(floor((Σ a·b + 2^(FRAC−1)) / 2^FRAC), −2^(OUT_W−1), 2^(OUT_W−1) − 1)`, with sums of up to 16 products computed exactly.
- **Beats:** only clocks with `i_valid = 1` count. A sum's first beat has `i_clear`, its last has `i_last` (both for a one-product sum). On idle clocks `i_clear`, `i_last`, `i_a` and `i_b` are random. Between any two beats, also inside a sum, there are 0 to 15 idle clocks; most beats are back to back.
- **Latency:** any fixed latency L from 1 to 40, measured on the first sum, which is applied alone. Every result must appear exactly L clocks after its `i_last` beat. `o_valid` is high for one clock per sum and at no other time, including the eight idle clocks before the first beat. `o_data` is checked only while `o_valid` is high.
- **Throughput:** one beat per clock, no gap needed between sums.

**Files:** `tb_mac_unit.v` goes to `sim/`, `ref_mac.py` to `sim/py/`. Run `.venv/bin/python sim/py/ref_mac.py sim` once and commit `sim/mac_in.hex` and `sim/mac_exp.hex` (about 4 MB together). The testbench has their line counts (322,270 beats, 100,046 sums) as `N_BEAT` and `N_SUM`; if you change the generator, update them to the counts it prints.

**Running:** `make test` runs it from `build/sim/` (about 5 seconds). It uses a 50 MHz clock, drives and samples on the falling edge, reads `../../sim/mac_in.hex` and `mac_exp.hex` (`+dir=PATH/` for another folder), prints at most ten error lines and then one `PASS` or `FAIL` line; the watchdog is 400 ms of simulated time. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y rtl/xc7 -y sim -o build/sim/tb_mac_unit.vvp sim/tb_mac_unit.v
cd build/sim && vvp -n tb_mac_unit.vvp
```

**DSP check:** `make dsp-mac_unit` (Section 9) lists one `DSP48E1`.
