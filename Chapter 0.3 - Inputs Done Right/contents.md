# Chapter 0.3 - Inputs Done Right

So far every signal in your designs has come from the clock or from your own registers. This chapter brings in the first signal from the outside world: a push button. A button looks like the simplest input there is, but wiring it straight into clocked logic causes three separate problems, and each has a standard fix you will use for the rest of the book. The same fixes protect the UART receiver in Chapter 0.4 and the SPI link in Phase 5.

By the end of this chapter you will have:

- a two-flop `synchronizer` that makes an asynchronous input safe to use,
- a `debounce` module that turns a bouncing contact into one clean change,
- an `edge_detect` module that turns a level into a one-clock pulse,
- a `button_counter` that chains them into the `counter` from Chapter 0.2,
- a Go Board display that goes up by exactly one per press of switch 1.

## 1. Three problems with one button

Press switch 1 and its pin goes high; release it and the pin goes low. Three things are wrong with using that pin directly as, say, the `i_en` of a counter.

1. **It is asynchronous.** Your finger has nothing to do with the 25 MHz clock. The pin can change at any instant, including the instant a flip-flop is sampling it. That can make the flip-flop **metastable** (Section 2).
2. **It bounces.** A mechanical contact does not close cleanly. For a millisecond or more it makes and breaks contact many times before it settles, so one press looks like a burst of presses (Section 4).
3. **It is a level, not an event.** Even a perfect, clean press keeps the pin high for perhaps 200 ms, which is 5 million clocks. A counter enabled by that level would count 5 million times. You want one action per press, which means detecting the moment the level changes (Section 5).

The fixes go in that order, each feeding the next:

```
 i_Switch_1 --> [ synchronizer ] --> [ debounce ] --> [ edge_detect ] --o_rise--> i_en [ counter ]
   async          2 flip-flops       stable for       one-clock pulse            from Chapter 0.2
                                     10 ms            per press
```

## 2. Metastability

A flip-flop is only promised to work if its input is stable for a short window around the clock edge: the **setup time** before the edge and the **hold time** after it. For signals inside your design, nextpnr's timing analysis (the "Max frequency" line in the report) is exactly the check that every path meets setup and hold. For a signal from a button, no analysis is possible. Sooner or later the pin changes inside that window.

When it does, the flip-flop's internal latch is caught between its two stable states, like a ball balanced on the top of a hill. It will roll to 0 or to 1, but how long it takes is unbounded in principle. Most of the time it settles in a fraction of a nanosecond. Rarely it takes longer, and while it is undecided its output may sit at an in-between voltage. Logic that reads it during that time can see a 0 in one gate and a 1 in another. A state machine reading such a signal can jump to a state that should be unreachable, and a counter can take a wrong value.

Three facts about metastability are worth remembering:

- **It cannot be prevented**, only made improbable. Any flip-flop sampling an unsynchronized signal can go metastable.
- **The chance of staying undecided falls off exponentially with time.** Each extra nanosecond of settling time makes a failure many times less likely. That is the whole basis of the fix.
- **Simulation cannot show it.** In Verilog, a flip-flop sampling a changing signal just takes the old or the new value. The bug only exists on the hardware, rarely, and is nearly impossible to reproduce. That is why you fix it by construction, every time, not by testing.

## 3. The two-flop synchronizer

The standard fix is two flip-flops in a row, clocked by your clock, with nothing between them:

```
 i_async --> [D  Q] --r_meta--> [D  Q] --> o_sync
              clk                clk
```

The first flip-flop samples the raw pin and may go metastable. It then has one whole clock period, 40 ns at 25 MHz, to settle before the second flip-flop samples it. A metastable state lasting a whole 40 ns is so unlikely that the second flip-flop's output is, for practical purposes, always a clean 0 or 1. The rest of the design uses only `o_sync`. The price is two clocks of latency, which for a button is nothing.

Rules for using it:

- **Every asynchronous input gets one**, exactly once, as close to the pin as possible. Buttons, the UART receive line in Chapter 0.4, the SPI pins in Phase 5.
- **No logic between the two flip-flops.** Any gate in that path eats into the settling time.
- **Nothing else reads the raw pin.** If two parts of the design each sample the raw signal, one can see the change a clock before the other, and they disagree. Synchronize once, then share `o_sync`.
- **One bit at a time.** Synchronizing the bits of a multi-bit bus separately does not work, because each bit can resolve on a different clock and the bus briefly holds a value that never existed. Crossing a bus between clock domains needs other techniques, which come up in Phase 3.

Write `synchronizer` with ports `i_clk`, `i_async` and `o_sync`. It is two registers and two nonblocking assignments; give both registers an initial value of 0. The testbench checks that `o_sync` is exactly `i_async` delayed by two clock edges, so one flip-flop or three both fail.

## 4. Bounce and debouncing

Inside a push button, a springy metal contact strikes another. It bounces off, touches again, and chatters for a while before it settles. The time this takes depends on the switch and how you press it. Under a millisecond is typical, several milliseconds is common, and worn switches can be worse. Release bounces too. Some boards put an RC filter on each button to smooth this out; the Go Board's buttons go straight to the FPGA, so you see every bounce.

To a 25 MHz clock, a 2 ms bounce is 50,000 cycles long and holds dozens of separate "presses". There are several ways to clean it up:

- **Sample slowly.** Look at the button only every 10 ms or so. Simple, but the delay varies with where in the sampling period the press lands.
- **Shift register.** Shift the input into an N-bit register each sample, and change the output only when all N bits agree.
- **Counter ("stable for N clocks").** Change the output only after the input has *disagreed* with the output for N consecutive clocks. Any bounce resets the count.

This chapter uses the counter method, because its behavior is exact and easy to test. With `N = CLK_HZ / 1000 * DEBOUNCE_MS` clocks:

- `o_out` starts at 0.
- While `i_in` equals `o_out`, the count is held at 0.
- While `i_in` differs from `o_out`, the count goes up by one each clock. When the input has differed for N clocks in a row, `o_out` takes the input's value and the count starts again from 0.

So during a bounce, every return to the old level resets the count, and the output does not move. Only when the contact has truly settled does the count run all the way to N. Then the output changes once, N clocks after the last bounce.

Choose `DEBOUNCE_MS` longer than the worst bounce you expect: 10 ms is a common choice. A 10 ms delay between a press and a response is far below what a person can notice. At 25 MHz that is N = 250,000 clocks, so the count needs 18 bits. As in Chapter 0.1, derive both N and the width from the parameters with `$clog2`, and think about the off-by-one: the testbench expects the output to change N to N+2 clocks after a clean change of the input, and requires a run of N-1 clocks to be ignored.

Make `debounce` with parameters `CLK_HZ` and `DEBOUNCE_MS`, an input `i_in` and an output `o_out`. Note that it does not synchronize its input. Its own logic is clocked and compares `i_in` with a register, so `i_in` must already be synchronous. Keeping the two jobs in separate modules means each one is simple and can be tested on its own, and you reuse the synchronizer alone for the UART.

## 5. Edge detection

After debouncing you have a clean level: high while the button is held, low otherwise. To act once per press you want the **event** of it going high. Keep a copy of the level as it was one clock earlier, in a register `r_prev`. Then compare:

| `i_level` | `r_prev` | Meaning |
| --- | --- | --- |
| 0 | 0 | low, unchanged |
| 0 | 1 | just fell: `o_fall` |
| 1 | 0 | just rose: `o_rise` |
| 1 | 1 | high, unchanged |

`o_rise` is `i_level & ~r_prev`, and `o_fall` is `~i_level & r_prev`. Make them combinational `assign`s, like the counter's `o_wrap`. Then `o_rise` is high during the very first clock cycle in which the level is 1, and only that cycle, because by the next cycle `r_prev` has caught up:

```
 clk       _|‾|_|‾|_|‾|_|‾|_|‾|_|‾|_
 i_level   ______|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
 r_prev    __________|‾‾‾‾‾‾‾‾‾‾‾‾‾
 o_rise    ______|‾‾‾|_____________
```

A pulse exactly one clock wide is a **tick**, the same kind of signal as `o_wrap` in Chapter 0.2, and it is made to drive a clock enable. Connect `o_rise` to the `counter`'s `i_en` and the counter steps once per press.

Give `r_prev` an initial value of 0. The edge detector must never see the raw button, only the debounced level. A bouncing input would produce a burst of rising edges, and an asynchronous one could produce a pulse that some flip-flops see and others miss.

Write `edge_detect` with ports `i_clk`, `i_level`, `o_rise` and `o_fall`. The board design only needs `o_rise`, but `o_fall` costs one gate, and you will use both edges later (the UART receiver looks for the falling edge of a start bit).

## 6. Putting it together: `button_counter`

`button_counter` is a portable module that chains the four parts:

- a `synchronizer` on `i_button`,
- a `debounce` passing down `CLK_HZ` and `DEBOUNCE_MS`,
- an `edge_detect` on the debounced level,
- an 8-bit `counter` from Chapter 0.2, with `i_en` driven by `o_rise` and `o_count` driving the module's `o_count`.

It contains no logic of its own, only instances and wires. Leave unused outputs (`o_fall`, `o_wrap`) connected to named wires so lint does not complain about missing connections.

Count the latency from the moment the button stops bouncing: two clocks in the synchronizer, N in the debouncer, and one more for the counter register. The testbench accepts N to N+8.

## 7. Testing a bouncy press

Testing input logic means writing a model of a misbehaving outside world. Four testbenches come with this chapter. Run them all, and read `tb_button_counter.v` in particular, because it shows the techniques:

- **`tb_synchronizer`** changes `i_async` at random moments between clock edges, sometimes several times in one period, and checks that `o_sync` equals the value the input had two edges earlier.
- **`tb_edge_detect`** drives a random synchronous level and checks `o_rise` and `o_fall` both right after each change (are they combinational?) and in the middle of each cycle.
- **`tb_debounce`** runs two instances, with N = 1000 and N = 200. It sends pulses of 1, 2, ..., N-1 clocks that must be ignored, clean changes that must come through in N to N+2 clocks, and bursts of random bounces about 2N clocks long. A bounce burst longer than N tests that it is the length of each *stable* stretch that matters, not the total time.
- **`tb_button_counter`** runs the whole chain with `CLK_HZ = 100_000`, so one clock stands for 10 µs and 1 ms is 100 clocks, and keeps `DEBOUNCE_MS = 10`. It simulates 20 presses. Each one bounces at random for 5 ms on the way down and again on the way up, and some have short drop-outs while held. It also adds noise spikes while idle. The count must go up exactly once per press, never on a release or a spike, and must read `8'h14` at the end.

Two techniques are worth copying:

- **Scale time with parameters.** Real time at 25 MHz is far too slow to simulate. Because every duration in your design is computed from `CLK_HZ`, a testbench can pretend the clock is slower and keep every millisecond meaning the same thing. This only works if you never hard-code a clock count.
- **Make "asynchronous" really asynchronous.** The testbench delays are random and not whole numbers of nanoseconds, such as `#(137.37)`, so the button never changes exactly on a clock edge. A stimulus that only changes in step with the clock would never exercise the synchronizer.

**Exercise.** Before running the supplied testbench, write a small one of your own: a task that toggles an input a few dozen times at random intervals, then settles. Feed it into `button_counter` and watch the synchronized button, the debounced level, `o_rise` and `o_count` in GTKWave. Seeing the debouncer's count reset on each bounce, then run up to N, makes the design obvious.

Then break things on purpose. Connect the edge detector to the synchronizer output instead of the debouncer, and `tb_button_counter` should report a press that counted a dozen times. Count on `o_rise | o_fall`, and it should report counts on release.

## 8. The board design

Make `projects/0.3-button-counter/` with a `Makefile` and `top_goboard.v`, as before. Put the new modules in `rtl/common/`, list them in `SRCS` together with `counter.v` and `hex_to_7seg.v`, and set:

```make
TESTS := tb_synchronizer tb_edge_detect tb_debounce tb_button_counter
```

`top_goboard` takes a `CLK_HZ` parameter (default 25,000,000) and is again only wiring:

- `i_Switch_1` (pin 53, high when pressed) into a `button_counter`, passing `CLK_HZ` down,
- the upper nibble of the count to digit 1 and the lower nibble to digit 2, through two `hex_to_7seg` decoders,
- every segment inverted, because the segments are active low. The pins are those from Chapter 0.2: `o_Segment1_A` to `o_Segment1_G` on 3, 4, 93, 91, 90, 1, 2 and `o_Segment2_A` to `o_Segment2_G` on 100, 99, 97, 95, 94, 8, 96.

The board has no reset button in this design, so the count starts from `00` at power-up. To start over, unplug and replug the board, or run `make prog` again.

**Read the report.** The synchronizer adds 2 flip-flops, the debouncer 18 count bits plus its output, the edge detector 1, and the counter 8. The total should still be under a hundred logic cells. Expect 16 `SB_IO`: the clock, one switch and 14 segments.

## 9. Bringing it up on the board

1. From the repository root, run `make test`. All four new testbenches must print `PASS`, along with every testbench from Chapters 0.1 and 0.2.
2. In `projects/0.3-button-counter/`, run `make clean prog`. The display shows `00`.
3. **Twenty presses.** Press switch 1 twenty times, at any speed and pressure you like: quick taps, slow firm presses, and a few long holds. The display must read `14` (hex for 20).
4. **Hold and release.** Hold the button down for several seconds. The count goes up once when you press and does not change while you hold or when you let go.
5. **Fast tapping.** Tap as fast as you can. Every tap still counts, because even fast tapping leaves the button released for well over 10 ms between presses. Try to make a tap so short that it is missed. A press shorter than 10 ms is very hard to do.
6. **See the bounce (optional).** Temporarily connect the edge detector to the synchronizer output, bypassing the debouncer, and rebuild. Now some presses add 2, 3 or more, and the count sometimes rises on release too. How bad it gets depends on your switches; some presses come through clean even like this, which is exactly why "it worked when I tried it" is not a test. Restore the debouncer afterwards.

If presses never count, check that `top_goboard` really instantiates `button_counter` with `i_Switch_1`, and that `DEBOUNCE_MS` is not so large that the count runs for seconds. If the display counts by 2, count only rising edges.

**Done when:** `make test` prints PASS for every testbench, including `tb_button_counter` feeding bouncy presses and seeing exactly one increment for each, and 20 real presses of switch 1 show `14` on the display.

## Testbench requirements

**Modules you provide**, each in a file named after the module under `rtl/` (for example `rtl/common/`), with outputs either `reg` or `wire`:

```verilog
module synchronizer (
    input  wire i_clk,
    input  wire i_async,
    output reg  o_sync
);

module debounce #(
    parameter integer CLK_HZ      = 25_000_000,
    parameter integer DEBOUNCE_MS = 10
) (
    input  wire i_clk,
    input  wire i_in,
    output reg  o_out
);

module edge_detect (
    input  wire i_clk,
    input  wire i_level,
    output wire o_rise,
    output wire o_fall
);

module button_counter #(
    parameter integer CLK_HZ      = 25_000_000,
    parameter integer DEBOUNCE_MS = 10
) (
    input  wire       i_clk,
    input  wire       i_button,
    output wire [7:0] o_count
);
```

- `synchronizer`: `o_sync` starts at 0 and equals `i_async` delayed by exactly two rising edges.
- `debounce`: with `N = CLK_HZ / 1000 * DEBOUNCE_MS`, `o_out` starts at 0. It changes to `i_in` only after `i_in` has differed from it for N consecutive clocks, which takes N to N+2 clocks after a clean change. Runs shorter than N clocks are ignored. The test uses (`CLK_HZ`, `DEBOUNCE_MS`) = (100,000, 10) and (50,000, 4).
- `edge_detect`: a registered copy of `i_level` starting at 0. `o_rise = i_level & ~prev` and `o_fall = ~i_level & prev`, both combinational.
- `button_counter`: synchronizer, then debounce, then edge_detect, then `counter` from Chapter 0.2 (`counter.v` must also be in `rtl/`). `o_count` starts at 0 and rises by one per press, N to N+8 clocks after the bouncing stops. Tested with `CLK_HZ = 100_000` and `DEBOUNCE_MS = 10`.
- No `x` on any output at any time.

**Testbenches:** copy `tb_synchronizer.v`, `tb_edge_detect.v`, `tb_debounce.v` and `tb_button_counter.v` from this chapter to `sim/` (top modules named after their files). No other support files are needed.

**Run them:** from the repository root, `make test`. Without the Makefiles:

```sh
mkdir -p build/sim
for tb in tb_synchronizer tb_edge_detect tb_debounce tb_button_counter; do
    iverilog -g2012 -Wall -Wno-timescale -o build/sim/$tb.vvp -y rtl/common sim/$tb.v
    (cd build/sim && vvp -n $tb.vvp)              # add +vcd to write <tb>.vcd
done
```

Each prints one line starting with `PASS` or `FAIL`.
