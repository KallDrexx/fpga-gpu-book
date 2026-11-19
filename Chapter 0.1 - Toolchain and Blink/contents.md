# Chapter 0.1 - Toolchain and Blink

Every FPGA project starts the same way: make an LED blink. It sounds trivial, but a blinking LED proves that every link in the chain works. Your editor produced valid Verilog, the synthesizer turned it into logic, the place-and-route tool fit that logic onto the chip and connected it to the right pins, the bitstream reached the board, and the FPGA is running your design from its own clock. Once this works, every later chapter is "just" more Verilog.

By the end of this chapter you will have:

- a project layout and set of Makefiles you will keep for the whole book,
- a portable `blinker` module that turns a fast clock into a slow blink,
- a thin board wrapper for the Nandland Go Board that wires the blinker and the four switches to the four LEDs,
- one command, `make clean prog`, that rebuilds everything from nothing and programs the board.

## 1. What an FPGA actually is

A microcontroller runs a program: one instruction after another. An FPGA (field-programmable gate array) runs nothing. It is a large grid of small, identical pieces of hardware whose connections you choose. When you "program" an FPGA you are not loading software; you are wiring up a circuit.

The Go Board's chip is a Lattice iCE40 HX1K in a 100-pin VQ100 package. Its main pieces are:

- **Logic cells (1,280 of them).** Each holds a 4-input lookup table (LUT) and a flip-flop. A 4-input LUT is a tiny 16-entry memory: it can compute *any* Boolean function of four inputs, because you simply store that function's truth table in it. The flip-flop stores one bit and updates it on a clock edge.
- **Routing.** A mesh of programmable wires and switches that connects cells to each other.
- **I/O blocks.** The pads where signals leave and enter the chip, one per package pin.
- **Block RAM (16 blocks of 4 Kbit).** Not needed yet, but it will matter a great deal later.
- **Global buffers.** Special low-skew wires that deliver a clock to every flip-flop at nearly the same instant.

All of the choices (what each LUT holds, which routes are on, which pins are inputs) are stored in configuration memory inside the chip. That memory is SRAM, so it is lost at power-off. The Go Board keeps a copy of your configuration, the **bitstream**, in a small SPI flash chip, and the FPGA reads it from flash every time it powers up. Programming the board means writing that flash.

The Go Board has a 25 MHz crystal oscillator wired to one pin of the FPGA. That clock is the heartbeat of everything you build on this board.

## 2. Installing the tools

This book uses a fully open-source flow. The easiest way to get it is **OSS CAD Suite**, a prebuilt bundle from YosysHQ. Download the archive for your OS, unpack it, and put its `bin/` directory on your `PATH` (the bundle also ships an `environment` script that does this). It contains everything you need for the Go Board:

| Tool | Job |
| --- | --- |
| `yosys` | Synthesis: Verilog in, a netlist of iCE40 cells out |
| `nextpnr-ice40` | Place and route: put the cells on the chip and wire them up |
| `icepack` | Turn the routed design into a binary bitstream |
| `iceprog` | Write the bitstream to the board's flash over USB |
| `iverilog`, `vvp` | Icarus Verilog: compile and run simulations |
| `gtkwave` | View simulation waveforms |
| `verilator` | A second Verilog front end, handy as a strict linter |

Check the install:

```sh
yosys -V
nextpnr-ice40 --version
iverilog -V
```

Also install Python 3 with `numpy`, `Pillow` and `pyserial`. You will not need them in this chapter, but later chapters use them for golden models and host scripts. Keep the list in a `requirements.txt` at the root of your project so `pip install -r requirements.txt` sets up a new machine.

**USB permissions on Linux.** The Go Board's USB interface is an FTDI FT2232H (vendor ID `0403`, product ID `6010`). By default only root can open it, so `iceprog` fails with a "can't find iCE FTDI USB device" or permission error. Add a udev rule granting your user access to that device (a one-line file in `/etc/udev/rules.d/`), reload the rules and replug the board.

## 3. Verilog modules

Verilog describes hardware as **modules**. A module is a box with named ports; inside, you describe how the outputs depend on the inputs and on stored state. A module is not a function that "runs": once synthesized, every part of it exists at once and works all the time.

```verilog
`default_nettype none

module blinker #(
    parameter integer CLK_HZ   = 25_000_000,
    parameter integer BLINK_HZ = 1
) (
    input  wire i_clk,
    output reg  o_led
);
    // ... your logic ...
endmodule

`default_nettype wire
```

A few things to notice:

- **Parameters** (`#( ... )`) are constants fixed when the module is instantiated. Making the clock frequency a parameter instead of hard-coding 25,000,000 has two payoffs. The same module works on the Arty Z7's 125 MHz clock in Phase 3, and a testbench can pretend the clock is 1 kHz so a simulated "second" takes 1,000 cycles instead of 25 million.
- **`wire` versus `reg`.** A `wire` is a connection; something else drives it continuously (a port, or an `assign`). A `reg` is a variable assigned inside an `always` block. Despite the name, `reg` does not always mean a register; it becomes a flip-flop only when it is assigned on a clock edge, as below.
- **`` `default_nettype none ``.** Verilog's worst default is that a misspelled name silently becomes a new 1-bit wire. With this directive, a typo is a compile error. Put it at the top of every file and restore the default at the bottom so you do not break other people's code.
- **Naming.** This book prefixes inputs with `i_`, outputs with `o_` and registers with `r_`. It is only a convention, but it makes waveforms and port lists much easier to read.

To use a module, you **instantiate** it inside another one, giving the instance a name and connecting its ports by name:

```verilog
blinker #(.CLK_HZ(25_000_000), .BLINK_HZ(1)) u_blinker (
    .i_clk (i_Clk),
    .o_led (w_blink)
);
```

Always connect by name (`.port(signal)`), never by position; it survives port reordering and is self-documenting.

## 4. Clocked logic: `always @(posedge clk)`

There are two kinds of digital logic. **Combinational** logic has no memory: its output is a function of its current inputs, like `assign o_LED_2 = i_Switch_2;`. **Sequential** logic has memory: flip-flops that capture a new value only at a clock edge and hold it until the next one.

In Verilog, you describe sequential logic with a block that is triggered by the rising edge of the clock:

```verilog
always @(posedge i_clk) begin
    r_count <= r_count + 1'b1;
end
```

Read it as: "at every rising edge of `i_clk`, the register `r_count` takes the value `r_count + 1`." Synthesis turns it into a row of flip-flops plus an adder in LUTs. The `<=` is a **nonblocking assignment**: every register in the design samples its new value at the same instant, using the values from just before the edge. Always use `<=` inside clocked blocks. Chapter 0.2 explains exactly why, and what goes wrong with `=`.

**Initial values.** On the iCE40, every flip-flop powers up as 0, and yosys honors initial values written in the declaration (`reg [7:0] r_count = 0;`) or in an `initial` block. The simulator, on the other hand, starts every `reg` as `x` (unknown) unless you give it a value. Give every register in this chapter an explicit initial value so simulation matches the hardware. Later chapters add proper resets.

## 5. Counters: dividing 25 MHz down to 1 Hz

An LED that blinks at 1 Hz is on for half a second and off for half a second. At 25 MHz, half a second is

  HALF_PERIOD = CLK_HZ / (2 × BLINK_HZ) = 25,000,000 / 2 = 12,500,000 clocks.

The design is a counter plus a toggling register:

1. Count clocks from 0 upward.
2. When the counter reaches the last value of the half period, reset it to 0 and invert the LED.
3. Otherwise, add one.

Two details deserve care.

**The width.** The counter must hold values up to 12,499,999. Since 2²⁴ = 16,777,216, it needs 24 bits. Let the tools work this out from the parameter with `$clog2(HALF_PERIOD)` instead of typing 24, or the module will break the moment someone changes `CLK_HZ`.

**The off-by-one.** A counter that goes 0, 1, ..., N−1 and then wraps visits N states, so it wraps every N clocks. If you compare against `N` instead of `N − 1`, every half period is one clock too long. On the board you would never see that (one part in 12.5 million), which is exactly why you want a testbench that can see it. Counting off-by-ones are the most common bug in this whole book; Step 1.2 has you hunting one in video timing.

That is all the logic you need. Writing `blinker` is your first task: a counter register, a comparison, and an `o_led` register that flips.

## 6. The board wrapper and the pin file

Your `blinker` knows nothing about the Go Board, and it should stay that way. The board-specific part goes in a separate, deliberately thin top-level module, `top_goboard`. Its ports are the board's physical signals; its body only instantiates portable modules and wires them to pins. When you move to the Arty Z7 in Phase 3, you write a new `top_artyz7` and reuse every portable module untouched.

For this chapter, `top_goboard` needs:

| Port | Direction | Board signal |
| --- | --- | --- |
| `i_Clk` | input | 25 MHz oscillator |
| `i_Switch_1` .. `i_Switch_4` | input | push buttons, high while pressed |
| `o_LED_1` .. `o_LED_4` | output | LEDs, active high |

LED 1 shows the blink. Switches 2 to 4 drive LEDs 2 to 4 directly with `assign`. So that switch 1 also "lights its LED", OR it into LED 1: `o_LED_1` is the blink signal or switch 1.

### The `.pcf` file

How does the place-and-route tool know that `o_LED_1` is the LED and not some other pin? You tell it in a **physical constraints file** (`.pcf`). Each line binds a top-level port name to a package pin number:

```
set_io -nowarn i_Clk        15
set_io -nowarn i_Switch_1   53
set_io -nowarn o_LED_1      56
```

The Go Board's pins for this chapter are:

| Signal | Pin | | Signal | Pin |
| --- | --- | --- | --- | --- |
| `i_Clk` | 15 | | | |
| `i_Switch_1` | 53 | | `o_LED_1` | 56 |
| `i_Switch_2` | 51 | | `o_LED_2` | 57 |
| `i_Switch_3` | 54 | | `o_LED_3` | 59 |
| `i_Switch_4` | 52 | | `o_LED_4` | 60 |

Write one `.pcf` for the whole board and list every pin on it (7-segment display, UART, VGA and Pmod too; the Go Board schematic and Nandland's examples give the numbers). Use `-nowarn` on each line so a design that uses only a few ports does not drown you in "unused constraint" warnings. Then every later chapter uses the same file and only has to pick port names from it. The port names in your `top_goboard` must match the names in the `.pcf` exactly, including case.

## 7. The build flow

Going from Verilog to a configured FPGA takes four tools in a row:

```
  .v sources --yosys--> .json --nextpnr-ice40--> .asc --icepack--> .bin --iceprog--> board
               synthesis        place & route          packing         programming
```

**Synthesis (yosys).** Yosys parses your Verilog, works out what hardware it describes, optimizes it, and maps it onto the iCE40's primitives: `SB_LUT4` lookup tables, `SB_DFF` flip-flops, `SB_CARRY` cells for fast adders, and I/O cells. The output is a netlist in JSON.

```sh
yosys -p 'read_verilog top_goboard.v blinker.v; synth_ice40 -top top_goboard -json build/blink.json'
```

**Place and route (nextpnr-ice40).** nextpnr chooses a physical location for every cell, routes the wires between them, and applies your `.pcf`. The device and package flags must match the chip exactly: `--hx1k --package vq100`. Give it the target clock with `--freq 25` and it checks that your design is fast enough, reporting the maximum frequency it achieved.

```sh
nextpnr-ice40 --hx1k --package vq100 --pcf goboard.pcf --freq 25 \
              --json build/blink.json --asc build/blink.asc
```

**Packing (icepack).** The `.asc` file is a human-readable text description of every configuration bit. `icepack build/blink.asc build/blink.bin` packs it into the binary bitstream.

**Programming (iceprog).** `iceprog build/blink.bin` writes the bitstream to the board's SPI flash and resets the FPGA, which then loads it. Unplug and replug the board and your design comes back, because it is in flash.

### Reading the report

nextpnr's log ends with a utilization summary. Look for these lines:

- `ICESTORM_LC`: logic cells used, out of 1,280. A 24-bit counter and a few gates should use a few dozen.
- `SB_IO`: I/O pins used. You should see 9 (one clock, four switches, four LEDs).
- `SB_GB`: global buffers. Your clock should be on one.
- `Max frequency for clock`: should be far above 25 MHz for a design this small.

Get into the habit of reading these numbers after every build. On a 1,280-cell chip you will run out by Phase 2, and knowing your budget is part of the job.

## 8. Project layout and Makefiles

You could type those four commands every time, but you will build dozens of designs in this book. Set up a structure now that scales.

```
Makefile              root: run all testbenches, shortcuts into projects/
requirements.txt      Python packages for later chapters
mk/
  config.mk           tool names and repo paths, shared by every Makefile
  goboard.mk          the iCE40 build flow (yosys -> nextpnr -> icepack -> iceprog)
  sim.mk              simulation rules (make test)
boards/
  goboard/goboard.pcf every Go Board pin
rtl/                  portable modules, one per file, file named after the module
  common/blinker.v
sim/                  testbenches, sim/tb_<name>.v
projects/
  0.1-blink/          one directory per step
    Makefile          a few lines: name, sources, tests, then include mk/goboard.mk
    top_goboard.v     the thin board wrapper
build/                all generated output (ignored by git)
```

The ideas behind it:

- **Portable code lives in `rtl/`**, one module per file with the file named after the module (`blinker` is in `blinker.v`). That naming rule lets the simulator find modules on its own, so testbenches never need source lists.
- **Each step gets a project directory** with only a `Makefile` and a `top_goboard.v`. Old steps keep building, so you can always go back.
- **The flow is written once** in `mk/goboard.mk`. A project Makefile sets a few variables and includes it:

  ```make
  PROJ  := blink
  TOP   := top_goboard
  SRCS  := top_goboard.v $(ROOT)/rtl/common/blinker.v
  TESTS := tb_blinker
  include ../../mk/goboard.mk
  ```

- **Tool names are variables** in `mk/config.mk`, set with `?=` so they can be overridden from the command line or environment (`make ICEPROG="openFPGALoader -b ice40_generic"`). It is also a good place to compute `ROOT`, the absolute path of the repository, from the location of `config.mk` itself (GNU make's `$(lastword $(MAKEFILE_LIST))` gives the path of the file being read).
- **Generated files go in `build/`.** Never mix outputs with sources; `make clean` should be a single `rm -rf build`. Add `build/` to `.gitignore`, along with stray `*.vcd`, `*.vvp`, `*.asc` and `*.bin` files.

### Targets to provide

In `mk/goboard.mk`, write one rule per stage so make rebuilds only what changed. The JSON depends on the sources (and on the Makefiles, so changing a flag rebuilds), the ASC on the JSON and the `.pcf`, the BIN on the ASC. Then add these targets:

| Target | What it does |
| --- | --- |
| `make` (default `all`) | Build `build/<proj>.bin` and print the resource report |
| `make prog` | Build if needed, then run `iceprog` |
| `make clean` | Delete the project's `build/` |
| `make report` | Print the utilization and max-frequency lines from the nextpnr log |
| `make lint` | Run `verilator --lint-only -Wall` on the design (optional, but it catches width mistakes that yosys accepts) |
| `make test` | Run this project's testbenches (`TESTS`) |

Declare targets that are not files (`all`, `prog`, `clean`, `report`, `lint`, `test`) as `.PHONY`. Pass `-q` and a `-l`/`--log` file to yosys and nextpnr so the terminal stays readable and the full logs are in `build/` when you need them.

In `mk/sim.mk` and the root `Makefile`, provide `make test`: compile each `sim/tb_*.v` with Icarus, letting it search `rtl/` and its subdirectories for modules (`iverilog -y <dir>` for each directory), run it, and check its output. Every testbench in this book prints exactly one line starting with `PASS` or `FAIL`. Count it as passing only if a `PASS` line appears and no `FAIL` line does; anything else (a crash, a compile error, no output) is a failure. Print a one-line summary and exit non-zero if anything failed, so later you can hook it into git or CI. A `make sim-<tb>` target that runs one testbench with `+vcd` and shows its output, and a `make wave-<tb>` that also opens GTKWave, will save you time in Chapter 0.2.

The goal of all this: `make clean prog` from inside `projects/0.1-blink/` must rebuild from nothing and program the board, and `make test` from the root must run every testbench you have ever written.

## 9. Testing before you program

A testbench is Verilog that is never synthesized. It instantiates your module, drives its inputs (here, just a clock), watches its outputs and decides whether they are right. Chapter 0.2 teaches you to write your own; for this chapter, use the one provided, `tb_blinker.v`.

It cannot simulate 60 real seconds at 25 MHz in reasonable time, so it uses the `CLK_HZ` parameter to shrink time. It runs two blinkers side by side, one with `CLK_HZ = 1000, BLINK_HZ = 1` and one with `CLK_HZ = 600, BLINK_HZ = 3`, and checks that each:

- starts with the LED off and is never `x` or `z`,
- toggles exactly every `CLK_HZ / (2 × BLINK_HZ)` clocks (so the duty cycle is exactly 50%),
- completes 60 blinks in exactly 60 simulated "seconds".

Using two parameter sets catches a module that hard-codes a number instead of computing it. If the test fails, read the message: "toggle came 501 clocks after the previous one, expected 500" is the off-by-one from Section 5.

## 10. Bringing it up on the board

Once the testbench passes:

1. Plug in the Go Board and run `make clean prog` in `projects/0.1-blink/`. Watch the output: synthesis, place and route, the report, packing and programming should all run in order, starting from an empty `build/`.
2. Check the report: 9 `SB_IO`, a few dozen logic cells, and a max frequency well above 25 MHz.
3. **Stopwatch check.** Start a stopwatch and count LED 1 turning on. You should count 60 blinks in 60 seconds. If you count 30 or 120, recheck the factor of 2 in the half-period formula.
4. **Switch check.** Hold each switch in turn; its LED lights while held. Switch 1 holds LED 1 on over the blink.
5. **Persistence check.** Unplug the board and plug it back in. The design starts again on its own, loaded from flash.

If nothing happens: confirm `iceprog` found the device (permissions, cable), confirm the port names in `top_goboard` match the `.pcf` exactly (a mismatched name leaves a port unconstrained, and nextpnr will warn or error), and confirm the clock port really is pin 15.

**Done when:** LED 1 blinks 60 times in 60 seconds on a stopwatch, each switch lights its LED, and `make clean prog` rebuilds from nothing and programs the board.

## Testbench requirements

**Module you provide:** `blinker`, in a file named `blinker.v` under `rtl/` (for example `rtl/common/blinker.v`).

```verilog
module blinker #(
    parameter integer CLK_HZ   = 25_000_000,
    parameter integer BLINK_HZ = 1
) (
    input  wire i_clk,
    output reg  o_led      // a wire output driven by a register is fine too
);
```

- `o_led` must be `0` at time zero (give it an initial value) and toggle every `CLK_HZ / (2 * BLINK_HZ)` rising edges of `i_clk`, the first toggle coming after that many edges.
- Every width must be derived from the parameters; the test uses `CLK_HZ` = 1000 and 600.

**Testbench:** copy `tb_blinker.v` from this chapter to `sim/tb_blinker.v` (top module `tb_blinker`). No other support files are needed.

**Run it:** from the repository root, `make test` (or `make test TESTS=tb_blinker`). Without your Makefiles yet:

```sh
mkdir -p build/sim
iverilog -g2012 -Wall -Wno-timescale -o build/sim/tb_blinker.vvp -y rtl/common sim/tb_blinker.v
cd build/sim && vvp -n tb_blinker.vvp          # add +vcd to write tb_blinker.vcd
```

It prints one line starting with `PASS` or `FAIL`.
