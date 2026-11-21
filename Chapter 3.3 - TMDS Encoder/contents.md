# Chapter 3.3 - TMDS Encoder

On the Go Board, a pixel left the FPGA as nine slow wires of color plus two sync wires. HDMI has no color wires. It has four differential pairs, and three of them carry a serial bit stream at ten times the pixel clock: 250 Mbit/s each at 640x480. A bit stream that fast needs some care. The receiver must find word boundaries in it, recover the bits from edges, and pass it through AC-coupled inputs that block any DC level. The coding that gives it those properties is **TMDS** (transition-minimized differential signaling), defined in the DVI 1.0 specification. HDMI uses the same coding for its video, and a monitor's HDMI input accepts a plain DVI signal.

In this chapter you write the TMDS encoder, the part that turns one 8-bit color value (or two control bits) into a 10-bit symbol every pixel clock. You also write a decoder, which reverses it, so a testbench can check that nothing is lost. Everything here runs in simulation. Chapter 3.4 adds the serializers and puts the result on the HDMI connector.

By the end of this chapter you will have:

- `rtl/common/tmds_encoder.v`, a DVI 1.0 TMDS encoder for one channel,
- `rtl/common/tmds_decoder.v`, its decoder, used by the testbench,
- `sim/tb_tmds.v` from this chapter passing. It compares your encoder bit for bit with a model of the DVI algorithm, round-trips a long stream through your encoder and decoder, and checks the running disparity bound.

Neither module uses a Xilinx primitive, so both go in `rtl/common/`.

## 1. What travels on the cable

A DVI link (and HDMI's video part) has four TMDS pairs:

| Pair | Carries during active video | Carries during blanking |
| --- | --- | --- |
| Channel 0 | blue, 8 bits | C0 = HSYNC, C1 = VSYNC |
| Channel 1 | green, 8 bits | C0 = CTL0, C1 = CTL1 |
| Channel 2 | red, 8 bits | C0 = CTL2, C1 = CTL3 |
| Clock | the pixel clock | the pixel clock |

Each data channel sends one **10-bit symbol per pixel clock**, least significant bit first. The clock pair runs at the pixel clock itself, 25 MHz here, and the receiver multiplies it by ten to sample the data. Chapter 3.4 sends it as the 10-bit pattern `1111100000` through a serializer like the others.

Every pixel clock, each encoder gets three inputs from the video timing:

- `de` (data enable), high during the visible 640x480 area,
- 8 bits of color, used when `de` is high,
- 2 control bits, C1 and C0, used when `de` is low.

Only channel 0's control bits mean anything to a DVI monitor: they carry the sync signals, at their real levels. For 640x480 both syncs are active low, so `vga_timing`'s outputs go straight in. The CTL bits on channels 1 and 2 must be **0**. HDMI uses nonzero CTL values to announce its data islands, and a monitor that sees them may expect packets you are not sending.

A data symbol and a control symbol look different on purpose, and the receiver tells them apart by value alone. There is no separate `de` wire.

## 2. Stage 1: transition minimizing

The first stage turns the 8-bit value D into a 9-bit word `q_m`. It chains the bits: each output bit is the previous output bit XORed (or XNORed) with the next data bit.

```
q_m[0] = D[0]
q_m[i] = q_m[i-1] XOR  D[i]      (i = 1..7)  and q_m[8] = 1,   or
q_m[i] = q_m[i-1] XNOR D[i]      (i = 1..7)  and q_m[8] = 0
```

With XOR, every 1 in `D[7:1]` causes a transition between neighbouring bits of `q_m`. With XNOR, every 0 does. So the encoder picks the operation by counting ones. The exact rule from DVI 1.0:

> **Use XNOR if N1(D) > 4, or if N1(D) = 4 and D[0] = 0. Otherwise use XOR.**

N1(D) is the number of ones in D. The tie-break on D[0] is not arbitrary. With four ones and D[0] = 0, all four ones sit in `D[7:1]`, so XOR would give four transitions, and XNOR gives three. With D[0] = 1, XOR gives three. Either way, `q_m[7:0]` never has more than **3 transitions**. Get the tie-break backwards and the code still decodes, but it is not DVI.

`q_m[8]` records the choice: **1 means XOR, 0 means XNOR**. The decoder needs it to undo the chain.

## 3. Stage 2: DC balancing

A long run of the same color could send many more ones than zeros. Through AC coupling that shifts the signal's average level until the receiver starts misreading bits. Stage 2 prevents this. It may invert `q_m[7:0]`, flags that in bit 9, and keeps a **running disparity counter** `cnt`: the number of ones minus the number of zeros sent so far.

Let N1 and N0 be the number of ones and zeros in `q_m[7:0]` (N1 + N0 = 8), and `cnt` the counter before this symbol. The DVI 1.0 rules, in order:

```
if de = 0:                                   (blanking)
    q_out = control symbol for {C1, C0}      (Section 4)
    cnt   = 0

else if cnt = 0 or N1 = N0:                  (no bias either way)
    q_out[9]   = NOT q_m[8]
    q_out[8]   = q_m[8]
    q_out[7:0] = q_m[8] ? q_m[7:0] : NOT q_m[7:0]
    cnt        = cnt + (q_m[8] ? N1 - N0 : N0 - N1)

else if (cnt > 0 and N1 > N0) or (cnt < 0 and N0 > N1):
    q_out[9]   = 1                           (invert: it would make things worse)
    q_out[8]   = q_m[8]
    q_out[7:0] = NOT q_m[7:0]
    cnt        = cnt + 2*q_m[8] + (N0 - N1)

else:
    q_out[9]   = 0                           (send as is: it helps)
    q_out[8]   = q_m[8]
    q_out[7:0] = q_m[7:0]
    cnt        = cnt - 2*(NOT q_m[8]) + (N1 - N0)
```

Two details are easy to miss:

- **Bit 9 means "inverted".** When it is 1, the decoder flips `q_out[7:0]` back first.
- **The odd-looking `2*q_m[8]` terms count bits 9 and 8.** Work them out for each branch and `cnt` changes by exactly the ones minus zeros of the whole 10-bit symbol. The counter is the true disparity of the stream since the last blanking period, and the testbench checks it that way: it counts the ones in what your encoder sends.

`cnt` is reset to 0 in every blanking period. That is in the spec's flowchart, not an option: an encoder that keeps its count through blanking produces different symbols, and the testbench rejects it. It also makes the encoder self-starting. Whatever `cnt` holds at power-up, the first blanking period clears it.

## 4. The four control symbols

During blanking each channel sends one of four fixed symbols, written here as `q_out[9:0]`:

| C1 C0 | Symbol | Hex |
| --- | --- | --- |
| 00 | `1101010100` | `0x354` |
| 01 | `0010101011` | `0x0AB` |
| 10 | `0101010100` | `0x154` |
| 11 | `1010101011` | `0x2AB` |

They are not all balanced: `0x354` and `0x0AB` have five ones, `0x154` has four and `0x2AB` six. That does not matter to the counter, which is held at 0 through blanking and counts only data symbols. Each has **at least 7 transitions** (7 for `0x354` and `0x0AB`, 8 for the other two), while a data symbol has at most 5 (3 inside `q_m[7:0]`, plus at most 2 around bits 8 and 9). That gap is how a receiver finds the symbol boundaries in a serial stream: it slides its 10-bit window until it sees control symbols during blanking.

On channel 0, `{C1, C0} = {vsync, hsync}`. With 640x480's active-low syncs, ordinary blanking (both syncs high) sends `0x2AB`, a horizontal sync pulse (`hsync` low) sends `0x154`, and vertical sync sends `0x0AB`, or `0x354` while both pulses overlap. Channels 1 and 2 send `0x354` throughout blanking.

## 5. Why the disparity stays within +/-8

Write `d = N1 - N0` for the 8 bits of `q_m`. It is even and between -8 and +8. Suppose `cnt > 0`:

- If `d > 0`, the encoder inverts. `cnt` changes by `2*q_m[8] - d`, at most `2 - 2 = 0`.
- If `d = 0`, `cnt` does not change.
- If `d < 0`, it sends the word as is. `cnt` changes by `d - 2*(NOT q_m[8])`, between -10 and -2.

So while `cnt` is positive it never grows, and one symbol lowers it by at most 10. Starting from `cnt` between 2 and 8, the next value is between -8 and 8. The case `cnt < 0` is the mirror image, and from `cnt = 0` one symbol moves it by `|d| <= 8`. Since `cnt` starts at 0 after blanking, by induction:

> **The running disparity is always even and within -8..+8 at every symbol boundary.**

All nine values are reachable. One byte takes `cnt` from 0 to any of them (0x00 gives -8, 0x01 gives +8), which is how the testbench reaches every state.

## 6. Building the encoder

```verilog
module tmds_encoder (
    input  wire       i_clk,    // pixel clock
    input  wire       i_de,     // 1 = active video
    input  wire [7:0] i_data,   // color, used when i_de = 1
    input  wire [1:0] i_ctrl,   // {C1, C0}, used when i_de = 0
    output reg  [9:0] o_tmds    // q_out; bit 0 is sent first
);
```

Chapter 3.4 instantiates this three times: blue with `i_ctrl = {vsync, hsync}`, green and red with `i_ctrl = 2'b00`.

**Stage 1** is plain combinational logic: count the ones in `i_data` (a `for` loop adding the bits works), then build the chain with a `for` loop. Assign `q_m[0]` before the loop, since each bit uses the one before.

**Stage 2** needs a counter for N1 of `q_m[7:0]`, the three-way decision and the counter update. Practical points:

- **Make `cnt` signed and wide enough.** It holds -8..+8, so it needs 5 bits signed. A 4-bit counter wraps at +8 and the output goes wrong after a few bright pixels.
- **Watch signedness in expressions.** In Verilog one unsigned operand makes the whole expression unsigned, so `cnt > 0` or `N1 - N0` can silently compare as huge positive numbers. Either declare every operand `signed` (and use `$signed()` on bit counts), or compare N1 with N0 directly (`n1 > n0`) instead of testing the sign of a difference.
- Give `cnt` and `o_tmds` initial values (no reset port, as in all earlier modules).

**Pipelining.** At 25 MHz a single stage meets timing easily, but where you place the registers matters for later:

- Stage 1 depends only on the input, so you can register `q_m` and pipeline it as deep as you like.
- Stage 2 cannot be split. `cnt` for the next symbol depends on this one, so the N1 count, the decision and the update form a loop that must close in one clock.
- **Register the output.** It feeds the serializer in Chapter 3.4, and a registered output keeps the encoder's logic delay away from that interface.

A common choice is two stages: register `q_m`, `de` and `ctrl` after stage 1, then register `o_tmds` and `cnt` after stage 2. That gives a latency of 2 clocks. One stage (latency 1) is fine too. The testbench measures the latency and accepts any fixed value from 1 to 8 clocks.

**Latency must match everywhere.** `i_de` and `i_ctrl` have to pass through the same number of registers as `i_data`. If `de` arrives a clock early, the first pixel of each line is encoded as a control symbol and the last blanking clock as data. Across channels the rule is the same: all three encoders must have the same latency, or the monitor sees red, green and blue (and the syncs on blue) out of step. Three identical instances of one module guarantee that. Do not give one channel a special version.

For scale: the reference two-stage encoder synthesizes with yosys `synth_xilinx` to 56 LUTs, 7 `CARRY4` and 27 flip-flops (9 for `q_m`, 3 for `de` and `ctrl`, 5 for `cnt`, 10 for the output). The Z7-20 has 53,200 LUTs, so three encoders cost nothing.

## 7. Building the decoder

The decoder exists to check the encoder. It is combinational:

```verilog
module tmds_decoder (
    input  wire [9:0] i_tmds,
    output reg        o_de,     // 0 for the four control symbols, 1 otherwise
    output reg  [7:0] o_data,   // valid when o_de = 1
    output reg  [1:0] o_ctrl    // {C1, C0}, valid when o_de = 0
);
```

(`output wire` with `assign`s is fine as well.)

1. If `i_tmds` is one of the four control symbols, set `o_de = 0` and `o_ctrl` from the table. Anything else is data.
2. Undo the inversion: `q = i_tmds[9] ? ~i_tmds[7:0] : i_tmds[7:0]`.
3. Undo the chain: `o_data[0] = q[0]`, and for i = 1..7, `o_data[i] = q[i] XOR q[i-1]` if `i_tmds[8]` is 1, `XNOR` if it is 0.

Disparity does not matter for decoding: each symbol decodes on its own, which is why a receiver can join a stream at any time.

The book's plan allows the decoder in Python. This chapter uses Verilog, so the whole check is one Icarus testbench. The testbench's own reference model of the encoder is written separately inside it, not shared with your code.

## 8. Simulate

Copy `tb_tmds.v` to `sim/` and run `make test` (or `make sim-tb_tmds`). Both modules are in `rtl/common/`, where `-y rtl/common` finds them, so `mk/sim.mk` needs no `tb_tmds_SRCS` line. The testbench takes about 5 seconds.

**Part 1** tests the decoder alone. For each of the nine disparity states -8..+8 it encodes all 256 bytes with its DVI model, feeds each symbol to your decoder, and expects the byte back. That covers every valid data symbol (460 distinct ones). Then it checks the four control symbols.

**Part 2** runs about 100,000 pixel clocks through your encoder, with your decoder on its output:

- an exhaustive section: for every disparity state and every byte, two control symbols, one byte that sets the state, the byte under test, and one more,
- a random section shaped like video: data periods of 1 to 700 pixels (noise, flat colors, stripes, gradients, and the bytes that push the disparity hardest) between control periods of 1 to 160 clocks with random or sync-like control bits.

It measures your encoder's latency, prints it, and then checks every symbol against the model, the control symbols, the running disparity of what your encoder actually sent, and the round trip. Messages name the byte and the disparity state: `encoder: byte 0x65 at running disparity -2: sent 1001110110, DVI 1.0 gives 1111011100` points at the ones-count rule (a backwards tie-break), and a mismatch whose state is always 0 right after blanking points at the counter reset. If the encoder and decoder agree with each other but not with DVI, the round trip passes and the model comparison fails. That is intended.

**Done when:** `tb_tmds` prints `PASS`: all 256 values from every disparity state and the long random stream round-trip through encode and decode, match DVI 1.0 bit for bit, and the running disparity stays within +/-8.

## Testbench requirements

**Modules you provide:**

- `rtl/common/tmds_encoder.v`: `tmds_encoder(i_clk, i_de, i_data[7:0], i_ctrl[1:0], o_tmds[9:0])`.
  - `o_tmds` is the DVI 1.0 symbol (`q_out[9:0]`, bit 0 sent first) for the inputs presented a fixed number of clocks earlier: any latency from 1 to 8 clocks, the same for `i_de`, `i_data` and `i_ctrl`. No `x` or `z` once the pipeline has filled.
  - Stage 1 uses XNOR when N1(D) > 4 or (N1(D) = 4 and D[0] = 0), and `q_m[8]` = 1 for XOR. Stage 2 follows Section 3 exactly, and the counter is reset to 0 whenever `i_de` is 0.
  - `i_de = 0` sends `0x354`, `0x0AB`, `0x154`, `0x2AB` for `{C1, C0}` = 00, 01, 10, 11.
- `rtl/common/tmds_decoder.v`: `tmds_decoder(i_tmds[9:0], o_de, o_data[7:0], o_ctrl[1:0])`, **combinational**.
  - The four control symbols give `o_de = 0` and their `{C1, C0}`. Every valid data symbol gives `o_de = 1` and its byte. `o_data` during control symbols and the outputs for invalid symbols are not checked.

**Testbench:** copy `tb_tmds.v` to `sim/tb_tmds.v` (top module `tb_tmds`). No `_SRCS` hook is needed. It runs in about 5 seconds and prints one `PASS` or `FAIL` line, after a line giving the measured latency. `+vcd` writes `tb_tmds.vcd`. Without the Makefiles:

```sh
mkdir -p build/sim
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_tmds.vvp sim/tb_tmds.v
cd build/sim && vvp -n tb_tmds.vvp
```
