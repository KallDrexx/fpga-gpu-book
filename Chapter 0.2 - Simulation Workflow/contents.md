# Chapter 0.2 - Simulation Workflow

In Chapter 0.1 you ran a testbench someone else wrote. This chapter teaches you to write your own, and to read what the simulator tells you when something is wrong. Simulation is where you will spend most of your time in this book. A build-and-program cycle takes a minute and shows you only what the pins do; a simulation takes a second and shows you every signal inside the design, at every clock edge.

By the end of this chapter you will have:

- a parameterized `counter` module that you will reuse in nearly every later chapter,
- a `hex_to_7seg` decoder that turns a 4-bit value into a 7-segment digit,
- a testbench of your own that checks all 16 digits against a table,
- a waveform workflow in GTKWave that you can open with one `make` command,
- the Go Board's two-digit display counting 00 to FF, one count per second.

## 1. Why simulate

A real FPGA gives you almost no visibility. You can see the LEDs, and with a logic analyzer a few pins, but nothing inside the chip. When the board misbehaves you can only guess, change something, rebuild and try again.

A simulator runs your Verilog as a program on your PC. It knows the value of every `reg` and `wire` at every instant, it can print messages, and it can write a complete record of every signal to a file. Two habits follow from this, and the rest of the book depends on both:

1. **Simulate before you build.** A design that has never passed a testbench does not go on the board.
2. **Make testbenches check themselves.** A testbench that only produces a waveform for you to look at gets looked at once. A testbench that compares results against expected values and prints `PASS` or `FAIL` can be rerun forever, which is what `make test` does every time you change anything.

## 2. Anatomy of a testbench

A testbench is a Verilog module with no ports. It is never synthesized, so it can use the parts of Verilog that have no hardware meaning: delays, loops that run over time, file output and printing. A typical one has six parts.

```verilog
`timescale 1ns/1ps
`default_nettype none

module tb_example;
    reg clk = 1'b0;
    always #5 clk = ~clk;                 // 1. a clock: 10 ns period

    reg  [3:0] value;                     // 2. signals to drive and watch
    wire [6:0] seg;

    hex_to_7seg dut (.i_hex(value), .o_seg(seg));   // 3. the design under test

    initial begin                         // 4. stimulus and checks
        value = 4'h1;
        #1;
        if (seg !== 7'b0000110) $display("FAIL tb_example: digit 1 = %b", seg);
        else                    $display("PASS tb_example");
        $finish;                          // 5. end the simulation
    end

    initial begin                         // 6. a watchdog
        #10_000;
        $display("FAIL tb_example: timeout");
        $finish;
    end
endmodule
```

- **`` `timescale 1ns/1ps ``** sets what `#1` means (1 ns) and how finely time is resolved. Only clock counts matter for these designs, so the exact unit is not important, but every file should state it.
- **The clock** is an `always` block that inverts a register every 5 ns. It never stops on its own, which is why the testbench must end with `$finish`.
- **The DUT** (design under test) is instantiated like any module, connected by name.
- **`initial` blocks** run once, starting at time zero. Unlike `always` blocks they can contain delays (`#1`), waits on events (`@(posedge clk)`) and loops that span simulated time. Every `initial` and `always` block in the design runs concurrently.
- **`$finish`** stops the simulator. Without it the clock keeps the simulation alive forever.
- **The watchdog** is a second `initial` block that fails the test if the first one never finishes, for example because it is waiting for an output that never changes. A hung testbench is a failed testbench, and it must say so.

Two comparison operators matter here. `==` treats an unknown (`x`) bit as "can't tell" and produces `x`, which an `if` treats as false. `===` compares bits exactly, including `x` and `z`, and always produces 0 or 1. In checks, use `!==` and `===` so an unknown output counts as wrong instead of slipping through.

Useful system tasks:

| Task | Does |
| --- | --- |
| `$display("fmt", args)` | Print a line. `%b`, `%h`, `%d`, `%0d` (no padding), `%t` (time) |
| `$time` | The current simulation time |
| `$random(seed)` | A pseudo-random 32-bit value, repeatable from the same seed |
| `$test$plusargs("vcd")` | True if the simulator was run with `+vcd` on its command line |
| `$finish` | End the simulation |

### `initial` in a design versus in a testbench

In Chapter 0.1 you used `initial` values in design code (`reg [7:0] r_count = 0;`), and yosys turned them into the iCE40's power-up state. That is the only kind of `initial` that belongs in a design file: setting a starting value. Anything with a delay or a `$display` is testbench-only; synthesis either ignores it or rejects it.

## 3. How the simulator thinks: blocking versus nonblocking

To use `=` and `<=` correctly you need a rough picture of how a simulator works. It keeps a queue of events (a signal changed, a delay ran out, a clock edge happened). At each instant of simulated time it wakes every block waiting on those events and runs them, in an order you do not control. Assignments come in two kinds.

- **Blocking, `a = b;`** updates `a` immediately. The next statement in the same block already sees the new value. It behaves like an assignment in C.
- **Nonblocking, `a <= b;`** reads `b` now but schedules the update of `a` for the end of the current time step, after every block that woke up this instant has finished reading. All the registers then change together.

Nonblocking is exactly how flip-flops behave: on a clock edge, every flip-flop samples its input from just before the edge, and only then does anything change. Consider a three-stage shift register:

```verilog
always @(posedge clk) begin
    r_a <= i_in;
    r_b <= r_a;
    r_c <= r_b;
end
```

With `<=`, `r_b` gets the *old* `r_a` and `r_c` the *old* `r_b`, so a value takes three clocks to travel through: three flip-flops. Change all three to `=` and `r_a` is updated first, then `r_b` copies the *new* `r_a`, then `r_c` copies the *new* `r_b`. The input reaches `r_c` on the first clock, and synthesis builds one flip-flop's worth of behavior instead of three. Worse, if those three assignments were in three separate `always @(posedge clk)` blocks, the result with `=` would depend on the order the simulator happened to run them, which no one promises. That is a **race**, and races are the bugs that pass in one simulator and fail in another, or pass in simulation and fail on the board.

**Exercise.** Write that shift register both ways in a scratch file with a small testbench that drives a single 1 into `i_in` for one clock. Look at both versions in GTKWave (Section 4). Seeing the pulse jump straight to `r_c` is worth more than any rule.

The rules that keep you out of trouble:

1. **Clocked logic (`always @(posedge clk)`) uses `<=`.** Always.
2. **Combinational logic (`always @(*)`) uses `=`.** There are no registers to keep in step, and `=` lets one line use the result of the previous one.
3. **Never mix the two kinds in one block, and never assign the same variable from two blocks.**
4. **In testbenches, do not change a DUT input at the same instant the DUT samples it.** If the testbench writes `i_en = 1` at the rising edge with a blocking assignment, whether the DUT sees the old or the new value is a race. Either change inputs on the falling edge (`@(negedge clk)`), or after a small delay past the rising edge (`@(posedge clk); #1;`). The testbenches in this book do this throughout.

Rule 2 has a trap of its own. An `always @(*)` block must assign every output on every path. If some input value leaves an output unassigned, the output has to remember its old value, and synthesis builds a **latch**, a level-sensitive memory that is almost never what you meant. Give every `case` a `default`, or assign a value at the top of the block before the `case`.

## 4. Waveforms: `$dumpvars` and GTKWave

When a check fails, the message tells you *what* went wrong. The waveform tells you *why*. Verilog can record every signal change to a **VCD** (value change dump) file:

```verilog
initial begin
    if ($test$plusargs("vcd")) begin
        $dumpfile("tb_counter.vcd");
        $dumpvars(0, tb_counter);   // 0 = this module and everything below it
    end
end
```

Dumping is guarded by `+vcd` because VCD files get large quickly (the full-frame video tests later in the book would write hundreds of megabytes), and `make test` does not need them. When you want to look, run the testbench with `+vcd`:

```sh
vvp -n tb_counter.vvp +vcd
gtkwave tb_counter.vcd
```

In GTKWave:

1. The **SST** pane on the upper left shows the design hierarchy. Click an instance (`tb_counter`, then `u_value`) to list its signals below.
2. Select signals and click **Append** (or drag them) to put them in the wave pane.
3. Use **Zoom Fit** to see the whole run, then zoom into a region of interest. Click in the wave pane to place the cursor; the signal values at the cursor appear beside the names.
4. Right-click a bus and choose **Data Format → Hex** (or Decimal) so an 8-bit counter reads as `3F` instead of `00111111`.
5. **File → Write Save File** stores your signal list and formats in a `.gtkw` file. Next time, `gtkwave tb_counter.vcd tb_counter.gtkw` opens straight to the same view.

Chapter 0.1 suggested two make targets; if you did not add them then, add them now, because this chapter is where they pay off:

- `make sim-<tb>` compiles and runs one testbench with `+vcd` and shows its full output.
- `make wave-<tb>` does the same and opens GTKWave on the result, loading `sim/<tb>.gtkw` if it exists. Keep the `.gtkw` files in `sim/` next to their testbenches and commit them.

Write the VCD into `build/` like every other generated file. Running `vvp` from inside `build/sim/` takes care of that, since the testbench writes to the current directory.

## 5. A parameterized counter

Counters are everywhere in digital design: dividing clocks, timing pulses, stepping through addresses, generating video coordinates. Rather than writing a new one each time, write one good one.

```verilog
module counter #(
    parameter integer WIDTH = 8,
    parameter integer MAX   = (1 << WIDTH) - 1
) (
    input  wire             i_clk,
    input  wire             i_en,
    output reg  [WIDTH-1:0] o_count,
    output wire             o_wrap
);
```

Its behavior:

- `o_count` starts at 0.
- On a rising edge of `i_clk` with `i_en` high, `o_count` goes up by one, except that from `MAX` it goes back to 0. With `i_en` low, it holds.
- `o_wrap` is high exactly when `i_en` is high and `o_count` equals `MAX`: it says "the next edge wraps". It is **combinational** (an `assign`), not a register, so it is high during the same clock cycle that the counter sits at `MAX`, not one cycle later.

`MAX` defaults to the largest value `WIDTH` bits can hold, so `counter #(.WIDTH(8))` is a plain 0-to-255 counter. Setting `MAX` makes it count 0 to `MAX` and wrap, for any range: `MAX = 9` gives a decimal digit, `MAX = 799` gives a VGA line in Chapter 1.1. Two details:

- **Compare at the right width.** `MAX` is a 32-bit integer and `o_count` is `WIDTH` bits. Verilog extends the narrower one silently, which works here, but `verilator --lint-only -Wall` warns about it. Declaring a `localparam [WIDTH-1:0]` copy of `MAX` keeps the lint clean.
- **The off-by-one again.** A counter from 0 to `MAX` has `MAX + 1` states, so it wraps every `MAX + 1` enabled clocks. To divide 25 MHz down to one pulse per second, `MAX` is 24,999,999, not 25,000,000.

### Clock enables, not new clocks

The board design needs something to happen once per second. The tempting way is to build a 1 Hz signal and use it as the clock of the display counter: `always @(posedge slow_clk)`. Do not. A signal made by logic is not a real clock. It arrives at flip-flops late and at different times, it does not travel on the low-skew global buffers from Chapter 0.1, and the timing analysis in nextpnr cannot check paths that cross between it and the real clock.

Instead, keep one clock for the whole design and use **clock enables**. The divider counter runs every cycle, and its `o_wrap` is a **tick**: a pulse exactly one clock wide, once every `MAX + 1` clocks. Feed that tick into the `i_en` of the next counter. Everything still runs on the 25 MHz clock, but the second counter only steps once per tick. This is why `counter` has both an `i_en` and an `o_wrap`: chaining them is the whole point.

```
             i_en=1                       i_en
 i_Clk --> [ counter  MAX=CLK_HZ-1 ] --o_wrap(tick)--> [ counter WIDTH=8 ] --> o_count[7:0]
```

Writing `counter` is your next task. It is a handful of lines.

## 6. The 7-segment display and its decoder

A 7-segment digit is seven LED bars, named A to G clockwise from the top, with G in the middle:

```
   --A--
  |     |
  F     B
  |     |
   --G--
  |     |
  E     C
  |     |
   --D--
```

Lighting the right subset draws a digit. Hexadecimal needs 16 shapes; `b` and `d` are drawn in lower case so they do not look like `8` and `0`. The patterns this book uses (and the testbench checks) are:

| Digit | Segments lit | | Digit | Segments lit |
| --- | --- | --- | --- | --- |
| 0 | A B C D E F | | 8 | A B C D E F G |
| 1 | B C | | 9 | A B C D F G |
| 2 | A B D E G | | A | A B C E F G |
| 3 | A B C D G | | b | C D E F G |
| 4 | B C F G | | C | A D E F |
| 5 | A C D F G | | d | B C D E G |
| 6 | A C D E F G | | E | A D E F G |
| 7 | A B C | | F | A E F G |

Some displays draw 6 without the top bar, 7 with F, or 9 without the bottom bar. All are fine on a real display, but the testbench needs one answer, and this table is it.

Write `hex_to_7seg` with a 4-bit input `i_hex` and a 7-bit output `o_seg`, where `o_seg[0]` is segment A through `o_seg[6]` for segment G, and **1 means lit**. It is pure combinational logic: an `always @(*)` block with a `case` statement over the 16 values (using `=`, and a `default` so no latch can appear), or an `assign` from a lookup. Translating the table above into bit patterns is the exercise; write each one out carefully, as `7'b` literals with the bits in G-to-A order. The commonest bug is reading the bits in the wrong order.

**Active-low outputs.** On the Go Board, a segment lights when its pin is driven **low** (the display's LEDs share a common supply, and the FPGA sinks the current). Keep the decoder active-high anyway and invert in the board wrapper. Active-high is the natural "1 means on" logic; the inversion is a fact about one board's wiring, so it belongs in `top_goboard`, not in a portable module. A display on another board might be active-high, and the decoder should not have to change.

## 7. Writing the decoder testbench

The step asks you to write a testbench that checks all 16 digits against a table. Write your own before looking at the one supplied with this chapter; you learn far more from writing one than reading one. It needs:

1. **The table.** A memory of 16 expected patterns, `reg [6:0] expected [0:15];`, filled in an `initial` block. Write it directly from the segment table in Section 6, independently of your decoder code. A testbench whose expected values were copied from the design proves nothing.
2. **A loop.** For each value 0 to 15: drive `i_hex`, wait `#1` so the combinational logic settles, compare `o_seg` against the table with `!==`, and on a mismatch print the digit, the value received and the value expected. Count errors instead of stopping at the first one, so one run shows you every wrong digit.
3. **One verdict line.** After the loop, print a single `PASS` line if the error count is zero, otherwise a single `FAIL` line, then `$finish`. Print detail lines for individual errors before it, indented, and never starting with `PASS` or `FAIL`.
4. **A watchdog**, and VCD dumping behind `+vcd`, as in Section 2.

One subtlety catches many first testbenches. An `always @(*)` block only runs when one of its inputs *changes*. If the testbench declares `reg [3:0] hex = 4'h0;` and its first check also applies 0, nothing changes, the block never runs, and `o_seg` is still `x`. Leave the input register uninitialized (`x`) so the first value applied is a change. (SystemVerilog's `always_comb` runs once at time zero and avoids this, but this book sticks to plain Verilog.)

Once your testbench passes, mutate your decoder on purpose: swap two bits, or remove segment A from digit 6, and confirm that your testbench fails and that its message points at the problem. A testbench that has never been seen to fail has not been tested. Then run the supplied `tb_hex_to_7seg.v` too. It also applies the digits in a scrambled order and names the wrong segments by letter.

The supplied `tb_counter.v` checks your `counter`. It runs three instances with different `WIDTH` and `MAX` values and random enables against a simple model of the expected count, then chains a divider (`MAX = 9`, so a "second" is 10 clocks) into an 8-bit counter, exactly as on the board, and checks that the display counter steps 00, 01, ..., FF, 00, one step per tick. Read through it: the model-based check (compute what the output should be, every cycle, and compare) is the pattern you will use most often from here on.

## 8. The board design

Make a project directory, `projects/0.2-hex-counter/`, with a `Makefile` and a `top_goboard.v`, the same way as in Chapter 0.1. Put `counter.v` and `hex_to_7seg.v` in `rtl/common/` and list them in `SRCS`, and set `TESTS := tb_counter tb_hex_to_7seg`.

`top_goboard` is again only wiring:

- a `counter` dividing the 25 MHz clock into a one-per-second tick (`i_en` tied high, `MAX` from a `CLK_HZ` parameter),
- an 8-bit `counter` enabled by that tick,
- two `hex_to_7seg` decoders: the upper nibble `[7:4]` to digit 1 (the left one), the lower nibble `[3:0]` to digit 2 (the right one),
- every segment output inverted, because the board's segments are active low.

You may keep the LED and switch logic from Chapter 0.1 too, or leave those pins unused. Give `top_goboard` a `CLK_HZ` parameter defaulting to 25,000,000 and pass it down, rather than writing the number inside the divider instantiation.

The segment pins, from `boards/goboard/goboard.pcf`:

| Port | Pin | | Port | Pin |
| --- | --- | --- | --- | --- |
| `o_Segment1_A` | 3 | | `o_Segment2_A` | 100 |
| `o_Segment1_B` | 4 | | `o_Segment2_B` | 99 |
| `o_Segment1_C` | 93 | | `o_Segment2_C` | 97 |
| `o_Segment1_D` | 91 | | `o_Segment2_D` | 95 |
| `o_Segment1_E` | 90 | | `o_Segment2_E` | 94 |
| `o_Segment1_F` | 1 | | `o_Segment2_F` | 8 |
| `o_Segment1_G` | 2 | | `o_Segment2_G` | 96 |

The pins are separate 1-bit ports, so you will split each decoder's bus into its bits: `assign o_Segment1_A = ~w_seg_hi[0];` and so on. An `assign {o_Segment1_G, o_Segment1_F, ..., o_Segment1_A} = ~w_seg_hi;` does all seven in one line, but get the order right: the leftmost name receives the most significant bit.

**Read the report.** You should see 15 `SB_IO` (the clock plus 14 segments, more if you kept the LEDs and switches). The 25-bit divider and the 8-bit counter are around 35 flip-flops; with the two decoders the total should still be well under a hundred logic cells.

## 9. Bringing it up on the board

1. From the repository root, run `make test`. Both `tb_counter` and `tb_hex_to_7seg` must print `PASS`, along with `tb_blinker` from Chapter 0.1.
2. In `projects/0.2-hex-counter/`, run `make clean prog`.
3. **Counting check.** The display starts at `00` and counts `01`, `02`, ... `09`, `0A`, ... `0F`, `10`. A full pass from `00` to `FF` and back to `00` takes 256 seconds, 4 minutes 16 seconds, on a stopwatch.
4. **Shape check.** Watch the right digit through one full cycle of 16 counts and compare every shape with the table in Section 6: `b` and `d` lower case, 6 with its top bar, 9 with its bottom bar.
5. **Order check.** The left digit must change exactly when the right digit rolls over from `F` to `0`. If the left digit races and the right digit crawls, the nibbles are swapped.

If a digit looks mirrored or scrambled, but the testbench passes, the decoder is right and the wrapper's bit-to-pin mapping is wrong. If every segment is lit except the ones that should be, you forgot the inversion. If the display counts much too fast or not at all, check `MAX` in the divider and that it is `CLK_HZ - 1`.

**Done when:** `make test` prints PASS for every testbench, and the board's 7-segment display counts 00 to FF at one count per second.

## Testbench requirements

**Modules you provide**, each in a file named after the module under `rtl/` (for example `rtl/common/`):

`counter` in `counter.v`:

```verilog
module counter #(
    parameter integer WIDTH = 8,
    parameter integer MAX   = (1 << WIDTH) - 1
) (
    input  wire             i_clk,
    input  wire             i_en,
    output reg  [WIDTH-1:0] o_count,   // a wire output driven by a register is fine too
    output wire             o_wrap
);
```

- `o_count` is 0 at time zero (initial value) and never `x`.
- On each rising edge with `i_en = 1`: `o_count` becomes `o_count + 1`, or 0 if it was `MAX`. With `i_en = 0` it holds.
- `o_wrap` is combinational: `1` exactly when `i_en = 1` and `o_count == MAX`.
- The test uses (`WIDTH`, `MAX`) = (4, default 15), (8, 199), (3, 4), (4, 9) and (8, default 255).

`hex_to_7seg` in `hex_to_7seg.v`:

```verilog
module hex_to_7seg (
    input  wire [3:0] i_hex,
    output reg  [6:0] o_seg    // a wire output is fine too
);
```

- Purely combinational. `o_seg[0]` = segment A ... `o_seg[6]` = segment G, active high, with the patterns in Section 6.

**Testbenches:** copy `tb_counter.v` and `tb_hex_to_7seg.v` from this chapter to `sim/` (top modules `tb_counter` and `tb_hex_to_7seg`). No other support files are needed.

**Run them:** from the repository root, `make test` (or `make test TESTS="tb_counter tb_hex_to_7seg"`). Without the Makefiles:

```sh
mkdir -p build/sim
for tb in tb_counter tb_hex_to_7seg; do
    iverilog -g2012 -Wall -Wno-timescale -o build/sim/$tb.vvp -y rtl/common sim/$tb.v
    (cd build/sim && vvp -n $tb.vvp)              # add +vcd to write <tb>.vcd
done
```

Each prints one line starting with `PASS` or `FAIL`.
