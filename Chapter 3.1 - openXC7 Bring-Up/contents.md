# Chapter 3.1 - openXC7 Bring-Up

The Go Board taught you to fit a GPU into 1,280 logic cells and 8 KB of RAM. From here on you have room. The Digilent Arty Z7-20 carries a Xilinx Zynq XC7Z020, with about forty times the logic, seventy-five times the block RAM and 220 hardware multipliers. Phase 4 needs that room for framebuffers and Phase 6 for a depth buffer.

A new chip means a new toolchain, a new constraint format and a new way to load the design, so this chapter does what Chapter 0.1 did: it blinks an LED. When the LED blinks, you know that synthesis, place and route, bitstream assembly and JTAG programming all work for the new part, and that your old portable code still runs on it.

By the end of this chapter you will have:

- a board file, `boards/arty-z7-20/arty-z7-20.xdc`, that every Arty Z7 step reuses,
- `mk/artyz7.mk`, the open-source Xilinx flow written once, next to `mk/goboard.mk`,
- `projects/3.1-arty-blink/`, with a thin `top_artyz7.v` that reuses Phase 0's `blinker` **unchanged**,
- one command, `make prog`, that rebuilds the design from a clean checkout and loads it into the board.

## 1. From iCE40 to Xilinx 7-series

The XC7Z020's programmable fabric is the same fabric as an Artix-7 FPGA. The ideas are the ones you know from the iCE40; the building blocks are bigger.

| | iCE40 HX1K (Go Board) | Zynq XC7Z020 fabric (Arty Z7-20) |
| --- | --- | --- |
| Lookup tables | 1,280 LUT4 | 53,200 LUT6 |
| Flip-flops | 1,280 (one per LUT) | 106,400 (two per LUT) |
| Grouping | 8 logic cells per tile | 4 LUTs + 8 FFs + a carry chain per slice, 2 slices per CLB |
| Block RAM | 16 x 4 Kbit | 140 x 36 Kbit (4.9 Mbit) |
| Multipliers | none | 220 DSP48E1 slices |
| Clock generators | none we rely on | 4 clock management tiles, each with an MMCM and a PLL |
| Global clock buffers | 8 | 32 BUFG |

**CLBs, slices and LUT6.** A 7-series logic block (CLB) holds two **slices**. Each slice has four 6-input LUTs, eight flip-flops and a 4-bit carry chain called `CARRY4`. A LUT6 is a 64-entry truth table, so it computes any function of six inputs, against four on the iCE40. It can also be split into two LUT5s that share their inputs, which is why a slice has two flip-flops per LUT. About a third of the slices are **SLICEM**s, whose LUTs can also act as small RAMs with an *asynchronous* read, or as shift registers. The iCE40 had nothing like that. The rest are **SLICEL**s, logic only.

One lesson from Chapter 2.5 carries over. The eight flip-flops in a slice share one clock, one clock enable and one set/reset. Registers that each have their own enable cannot share a slice, so a design can run out of slices before it runs out of LUTs. You have far more slices now, but the rule still holds.

**BRAM36.** Each block RAM holds 36 Kbit and can be split into two independent 18 Kbit RAMs. It is true dual-port, and each port may use its own clock. Phase 4 relies on that: the drawing engine writes the framebuffer at 50 MHz while scanout reads it at 25 MHz. Like the iCE40's RAM, the read is synchronous: data appears after the clock edge.

**DSP48E1.** A hard 25x18-bit multiplier with a 48-bit adder and accumulator behind it. On the Go Board a multiply cost a long chain of shifts and adds; here it is one block, and yosys infers it from `a * b`. Phase 6's matrix math lives in these.

**MMCM, a preview.** A clock management tile multiplies and divides an input clock to make new clocks, with phase control. On the Go Board you used the 25 MHz crystal as the pixel clock. The Arty's clock is 125 MHz, so Step 3.2 uses an `MMCME2_BASE` to make 25 MHz (pixel), 125 MHz (serial, phase-aligned to the pixel clock) and 50 MHz (system). This chapter divides the clock with a counter only, as Chapter 0.1 did.

**Power-up values still work.** At the end of configuration, a 7-series chip loads every flip-flop with its `INIT` value, and yosys sets `INIT` from initial values in your Verilog (`reg [7:0] r_count = 0;`). Your reset-free Phase 0 to 2 modules keep working as written. Step 3.2 adds resets, because a clock from an MMCM is not stable until the MMCM says so.

## 2. The Zynq: PS and PL

A Zynq is two chips in one package:

- the **Processing System (PS)**: two ARM Cortex-A9 cores with caches, a DDR3 memory controller and fixed peripherals (USB, Ethernet, SD, UART), wired to their own pins, the MIO pins;
- the **Programmable Logic (PL)**: the 7-series fabric described above, with its own I/O pins.

The two halves talk over AXI buses, and the PS can configure the PL while it runs. A typical Zynq design boots Linux on the ARM cores and uses the fabric as an accelerator.

**This book uses only the PL.** The PS never runs a line of code. That keeps the open-source flow simple, since no PS configuration or boot image is needed, and it keeps the GPU a plain piece of logic that an outside microcontroller drives, as Phase 5 plans. It has three consequences you have already seen in the Summary:

- The **DDR3 is out of reach**. It hangs off the PS's memory controller. Everything stays in block RAM.
- The board's **USB-UART is out of reach**. It is wired to the PS. Step 3.5 puts the fabric's UART on a Pmod pin through your USB-serial adapter.
- The **bitstream lives only in configuration SRAM**. You load it over JTAG, and it is gone at power-off. Booting the PL from flash on a Zynq needs a PS boot image (a first-stage boot loader), which is out of scope. Every session starts with `make prog`.

The PL has its own clock. A 125 MHz clock from the board's Ethernet PHY chip reaches PL pin H16, a clock-capable pin, so no PS help is needed.

## 3. The openXC7 flow

openXC7 is the open-source flow for Xilinx 7-series parts. It has the same shape as the iCE40 flow, with one extra stage:

```
 .v --yosys--> .json --nextpnr-xilinx--> .fasm --fpga-as--> .bit --openFPGALoader--> board
      synth_xilinx      place & route             assemble              JTAG
                        (+ chipdb, .xdc)          (+ Project X-Ray database)
```

Install openXC7 (its toolchain installer builds or downloads everything below) and `source` its `export.sh` in each shell before building. The script puts the tools on `PATH` and sets two variables the Makefile needs:

- `PRJXRAY_DB_DIR`, the Project X-Ray database: the reverse-engineered meaning of every configuration bit, one directory per family (`zynq7` for this chip);
- `NEXTPNR_XILINX_DIR`, the openXC7 install root, where the chip database lives.

openXC7 ships its own yosys. If your shell also has the OSS CAD Suite on `PATH`, check that `which yosys` points into the openXC7 install after sourcing `export.sh`. Both versions know `synth_xilinx`, but staying with the one the rest of the flow was tested against avoids surprises.

**Synthesis.** Yosys maps your Verilog onto 7-series primitives: `LUT1` to `LUT6`, `FDRE` flip-flops, `CARRY4` chains, `IBUF`/`OBUF` pads and a `BUFG` for the clock.

```sh
yosys -p 'read_verilog top_artyz7.v ../../rtl/common/blinker.v;
          synth_xilinx -flatten -abc9 -arch xc7 -top top_artyz7;
          write_json build/arty_blink.json'
```

`-flatten` merges the hierarchy into one module, which nextpnr-xilinx expects. `-abc9` uses yosys's timing-aware LUT mapper.

**The chip database.** nextpnr needs a description of every tile, site and wire on the die, the *chipdb*. openXC7 generates it from the Project X-Ray database, once per die. It takes about a minute and makes a 40 MB file:

```sh
python3 $NEXTPNR_XILINX_DIR/share/nextpnr/himbaechel/uarch/xilinx/gen/xilinx_gen.py \
        --xray $PRJXRAY_DB_DIR/zynq7 --device xc7z020 --bba build/chipdb.bba
bbasm -l build/chipdb.bba $NEXTPNR_XILINX_DIR/chipdb/chipdb-xc7z020clg400.bin
```

The Makefile below does this automatically if the file is missing. If your installer already made one, it reuses it.

**Place and route.** nextpnr-xilinx places the cells, routes them, applies the `.xdc` and writes **FASM**, a text list of the fabric features to turn on (`CLBLM_R_X71Y116.SLICEM_X0.ALUT.INIT[63:0] = 64'b...`):

```sh
nextpnr-xilinx --chipdb $NEXTPNR_XILINX_DIR/chipdb/chipdb-xc7z020clg400.bin \
               --xdc ../../boards/arty-z7-20/arty-z7-20.xdc \
               --json build/arty_blink.json --fasm build/arty_blink.fasm
```

The device comes from the chipdb, so there is no `--hx1k`-style flag, and the clock period comes from the `.xdc`, so there is no `--freq`. In current openXC7 installs `nextpnr-xilinx` is a small wrapper around `nextpnr-himbaechel`, the newer nextpnr engine. The command line is the same either way.

**Assembly.** `fpga-as` looks up each FASM feature in the Project X-Ray database and writes a `.bit` file:

```sh
fpga-as --prjxray_db_path=$PRJXRAY_DB_DIR/zynq7 --part xc7z020clg400-1 \
        build/arty_blink.fasm > build/arty_blink.bit
```

Older guides do this in two steps, `fasm2frames` and then `xc7frames2bit`. `fpga-as` does both in one faster process. The two-step pair still works if you need it.

**Programming.** `openFPGALoader -b arty_z7_20 build/arty_blink.bit` sends the bitstream over the board's USB-JTAG link straight into configuration SRAM.

The whole build of this chapter's design takes about 7 seconds, most of it in nextpnr. The first build also generates the chipdb, about 50 seconds more.

## 4. Constraints: the `.xdc` file

The iCE40 `.pcf` had one job: bind a port to a pin. A Xilinx `.xdc` file uses Tcl syntax, from Vivado, and carries more. You need three things in it:

```tcl
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports {clk}]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
create_clock -period 8.000 [get_ports {clk}]
```

- **`PACKAGE_PIN`** binds a port, or one bit of a vector port, to a ball of the 400-ball package. Ball names are a letter row and a number column (`H16`).
- **`IOSTANDARD`** sets the voltage and signaling of the pin. Every Arty Z7 pin we use in this phase is 3.3 V `LVCMOS33`, except the HDMI pairs in Step 3.4 (`TMDS_33`). The iCE40 had one fixed standard per bank, so the `.pcf` never said it.
- **`create_clock`** tells the timing analyzer the clock's period: 8 ns for 125 MHz. It replaces nextpnr-ice40's `--freq`. Without it nextpnr checks timing against its default target of 12 MHz, and a design too slow for 125 MHz would still "pass".

`set_property -dict {...}` sets several properties in one line. The longer Vivado form, one `set_property` per property, also works.

### The brace quirk

Digilent publishes a master `.xdc` for each board, and its lines look like this:

```tcl
set_property -dict { PACKAGE_PIN H16 IOSTANDARD LVCMOS33 } [get_ports { sysclk }];
```

Vivado accepts it. nextpnr-xilinx's XDC parser does not. It handles the spaces inside the `-dict` braces, but spaces inside the **`get_ports` braces** make it crash while reading the file:

```
Info: Parsing XDC file...
terminate called after throwing an instance of 'nextpnr_himbaechel::assertion_failure'
  what():  Assertion failure: str.back() == '}' (.../uarch/xilinx/xdc.cc:55)
```

If you see that message, look at your `get_ports` braces. Write `[get_ports {clk}]` and `[get_ports {led[0]}]`, with no spaces inside. Drop Digilent's trailing semicolons too; the parser stops on them with `ERROR: failed to parse target (on line N)`. Keep the braces themselves: in Tcl, a bare `led[0]` would run `0` as a command.

### The board file

This chapter supplies `arty-z7-20.xdc`. Copy it to `boards/arty-z7-20/arty-z7-20.xdc`. Its pins come from Digilent's Arty-Z7-20 master XDC, rewritten in the brace-safe form, and it keeps Digilent's port names so their examples drop in. The one exception is the clock: Digilent calls it `sysclk`, this file calls it `clk`. (The `arty-z7-20.pcf` already in that directory is no use to nextpnr-xilinx; you can delete it.) The pins for this chapter:

| Port | Pin | | Port | Pin | | Port | Pin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `clk` (125 MHz) | H16 | | `btn[0]` | D19 | | `led[0]` (LD0) | R14 |
| `sw[0]` | M20 | | `btn[1]` | D20 | | `led[1]` (LD1) | P14 |
| `sw[1]` | M19 | | `btn[2]` | L20 | | `led[2]` (LD2) | N16 |
| | | | `btn[3]` | L19 | | `led[3]` (LD3) | M14 |
| `led4_r` | N15 | | `led4_g` | G17 | | `led4_b` | L15 |
| `led5_r` | M15 | | `led5_g` | L14 | | `led5_b` | G14 |

All are active high: a button reads 1 while pressed, a switch reads 1 when up, and an LED lights on 1. LD4 and LD5 are the two RGB LEDs.

The file also lists Pmod headers JA and JB (Step 3.5's UART goes on one), the ChipKit header and audio. The HDMI output pins are there but commented out; Step 3.4 turns them on when it adds the differential output buffers.

As with the `.pcf`, one board file serves every project. A line whose port is not in the design is skipped without a message (`nextpnr-xilinx --verbose` lists them). A port that is in the design but has no `PACKAGE_PIN` is an error:

```
ERROR: FIXME: unconstrained IO not supported (pad led[0])
```

That message almost always means a name mismatch between your top module and the `.xdc`. The names must match exactly, including case and brackets.

## 5. The design

### Reusing `blinker`

Chapter 0.1 asked you to make the clock frequency a parameter and derive the counter width from it. This is the payoff. At 125 MHz and 1 Hz:

  HALF_PERIOD = 125,000,000 / 2 = 62,500,000 clocks

That needs 26 bits (2^26 = 67,108,864). `$clog2` works it out, so the same `rtl/common/blinker.v` that ran at 25 MHz runs at 125 MHz with only a new parameter value. **Do not edit it, and do not copy it into the project.** If the Phase 0 file needs a change to work here, the change is a bug fix, and `tb_blinker` should still pass afterwards.

### `top_artyz7.v`

Like `top_goboard.v`, the Arty top only maps board pins onto portable modules. Its ports carry the names from the `.xdc`:

| Port | Direction | Use |
| --- | --- | --- |
| `clk` | in | 125 MHz clock |
| `btn[3:0]` | in | push buttons BTN0-BTN3 |
| `sw[1:0]` | in | slide switches SW0, SW1 |
| `led[3:0]` | out | green LEDs LD0-LD3 |
| `led4_r`, `led4_g`, `led4_b` | out | RGB LED LD4 |
| `led5_r`, `led5_g`, `led5_b` | out | RGB LED LD5 |

Wire them like this:

- `led[0]` shows the 1 Hz blink, OR'd with `btn[0]` so the button lights it too (same as LED 1 on the Go Board);
- `led[1]`, `led[2]`, `led[3]` follow `btn[1]`, `btn[2]`, `btn[3]`;
- `sw[0]` lights LD4 green and `sw[1]` lights LD5 green;
- the red and blue channels of LD4 and LD5 are driven to 0. Every output port needs a driver, and an RGB LED left floating may glow.

Give the top one parameter, `CLK_HZ`, defaulting to 125,000,000, and pass it to the blinker. The board build uses the default. The testbench overrides it with a small value so a simulated second takes 1,250 clocks:

```verilog
module top_artyz7 #(
    parameter integer CLK_HZ = 125_000_000
) ( ... );
    ...
    blinker #(.CLK_HZ(CLK_HZ), .BLINK_HZ(1)) u_blinker (
        .i_clk (clk),
        .o_led (w_blink)
    );
```

The rest is `assign` statements. The buttons drive LEDs directly, with no clock involved, so they need no synchronizer. (If you add one anyway, as Chapter 0.3 taught, the testbench allows a few clocks of delay.)

The RGB LEDs are very bright at full drive. Digilent recommends dimming them with PWM for long use. For a bring-up test, flipping a switch on and off is fine.

## 6. The Makefile

This chapter supplies **`artyz7.mk`**: copy it to `mk/artyz7.mk`. It is the Xilinx twin of `mk/goboard.mk`, and a project uses it the same way. Read it through; it is short. It provides:

| Target | What it does |
| --- | --- |
| `make` | yosys, then nextpnr-xilinx, then `fpga-as`, giving `build/<proj>.bit`, and prints the resource report |
| `make prog` | build if needed, then `openFPGALoader -b arty_z7_20` |
| `make report` | the utilization and max-frequency lines from the nextpnr log |
| `make chipdb` | generate the chip database if it is missing (any build does this too) |
| `make lint`, `make test`, `make clean` | as for the Go Board |

Some details worth knowing:

- It stops with a clear message if `PRJXRAY_DB_DIR` or `NEXTPNR_XILINX_DIR` is unset, which usually means you forgot `source export.sh`.
- The chipdb lives at `$NEXTPNR_XILINX_DIR/chipdb/chipdb-xc7z020clg400.bin`, outside the repo. Set `CHIPDB_DIR` to keep it elsewhere. Either way it is built once and is not part of `make clean`.
- New tool names (`NEXTPNR_XILINX`, `FPGA_AS`, `BBASM`, `OPENFPGALOADER`) are `?=` variables at the top. Move them into `mk/config.mk` if you prefer all tools in one place.
- It uses the tool variables from your `mk/config.mk` (`YOSYS`, `VERILATOR`) and gets `make test` by including `mk/sim.mk` on its last line. If your names differ, or your `goboard.mk` defines the project's `test` target itself, adjust those lines to match.
- `.DELETE_ON_ERROR` matters here. `fpga-as` writes to standard output, and without it a failed run would leave a half-written `.bit` that make thinks is up to date.

Create `projects/3.1-arty-blink/Makefile` the same way as a Go Board project:

```make
PROJ  := arty_blink
TOP   := top_artyz7
include ../../mk/config.mk

SRCS  := top_artyz7.v \
         $(ROOT)/rtl/common/blinker.v

TESTS := tb_blinker tb_arty_blink

include $(ROOT)/mk/artyz7.mk
```

(If you gave yourself a `make new` template in Phase 0, it makes a Go Board project. For an Arty project, copy this directory instead, or add a second template.)

Finally, the testbench needs to find `top_artyz7.v`, which is not under `rtl/`. Until now `iverilog -y` found every module on its own, so `mk/sim.mk` has had no way to add a file for one testbench. Give it one: in the rule that compiles `sim/tb_X.v`, append `$(tb_X_SRCS)` to the iverilog command line, where `tb_X` is the testbench name (`$($*_SRCS)` in a `%` pattern rule). To make editing the top recompile, also list it as a prerequisite; in a pattern rule that takes `.SECONDEXPANSION:` and `$$($$*_SRCS)`. Then set it for this testbench in `mk/sim.mk`:

```make
tb_arty_blink_SRCS := $(ROOT)/projects/3.1-arty-blink/top_artyz7.v
```

With it in `mk/sim.mk`, both `make test` at the root and `make test` in the project find the file.

## 7. Simulate first

Copy `tb_arty_blink.v` to `sim/`. It runs your top level twice:

- with `CLK_HZ = 1250`, the board's 125 MHz scaled down 100,000 times. It checks that `led[0]` starts low and toggles exactly every 625 clocks for 60 blinks; that each button and switch reaches its LED (with random patterns, while the blink runs); that a held `btn[0]` keeps `led[0]` lit through a falling blink edge (OR, not XOR); and that the red and blue RGB channels stay low and nothing is ever `x` or `z`;
- with the default parameters, checking that `CLK_HZ` defaults to 125,000,000 and that this copy does not blink in the short run.

Run `make test` at the root. Both `tb_blinker` and `tb_arty_blink` should pass. `tb_blinker` is the Phase 0 test, unchanged, and it proves the blinker you are reusing still behaves.

What the testbench cannot check: that 125,000,000 clocks really fit in the counter (that would take 125 million simulated clocks per second of blink), and anything about pins. The resource report and the board check those.

## 8. Build and read the report

Run `make` in `projects/3.1-arty-blink/`. The report from the reference design:

```
== arty_blink: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:      68/ 106400     0%
  SLICE_FFX:      27/ 106400     0%
  CARRY4:       8/  13300     0%
  PAD:      17/    392     4%
  IOB33_OUTBUF:      10/    200     5%
  IOB33_INBUF_EN:       7/    200     3%
  BUFGCTRL:       1/     32     3%
  Max frequency for clock 'u_blinker.i_clk': 284.90 MHz (PASS at 125.00 MHz)
```

(It also lists two `PSEUDO_GND`/`PSEUDO_VCC` cells, nextpnr's constant drivers.) How to read it:

- **`SLICE_FFX` 27**: the 26-bit counter plus the LED register. If you see 25 or 24, the width did not follow `CLK_HZ`.
- **`CARRY4` 8**: the counter's +1 runs on the carry chain, 4 bits per `CARRY4`.
- **`SLICE_LUTX` 68 of 106,400**: nextpnr counts LUT *halves*, two per LUT6 (that is why the total is twice 53,200). Yosys's log (`build/yosys.log`) shows only 10 LUTs, 27 `FDRE` and 7 `CARRY4`. The other 58 are `LUT1` pass-throughs that nextpnr adds while packing, because a `CARRY4` takes its inputs from the LUTs in its own slice (the log says `Created 68 SLICE_LUTX cells from: 58x LUT1 ...`). Watch this number grow with real logic, not with a counter.
- **`PAD` 17**: 7 inputs (clock, 4 buttons, 2 switches) and 10 outputs (4 LEDs, 6 RGB channels).
- **`BUFGCTRL` 1**: the clock is on a global buffer.
- **Max frequency**: about 285 MHz against a 125 MHz target. The `PASS at 125.00 MHz` shows that the `create_clock` line was applied. If it says `PASS at 12.00 MHz`, nextpnr used its default because your `create_clock` is missing or did not match the clock port.

The `.bit` is about 4 MB. A 7-series bitstream always configures the whole device, however little of it you use.

## 9. On the board

**Set up once.**

1. Set the board's programming-mode jumper (JP4) to **JTAG**. In JTAG mode the PS waits at power-up and nothing else configures the PL. In QSPI or SD mode it may boot whatever image is stored there first.
2. Connect the micro-USB port labeled **PROG UART** to your PC. With the power-source jumper set to USB, that cable also powers the board.
3. The Arty's USB-JTAG chip is an FTDI FT2232H, vendor `0403`, product `6010`, the same IDs as the Go Board. The udev rule from Chapter 0.1 already covers it. `openFPGALoader --detect -b arty_z7_20` should find the `xc7z020` on the JTAG chain.

**Load and check.**

1. In `projects/3.1-arty-blink/`, run `make prog`. The board's DONE LED lights when the PL is configured.
2. **Stopwatch check.** Count LD0 turning on for 60 seconds. You should count 60. If you count 300, `CLK_HZ` is still 25,000,000 somewhere; if 30 or 120, recheck the factor of 2.
3. **Button and switch check.** Each of BTN0-BTN3 lights LD0-LD3 while held; BTN0 holds LD0 on over the blink. SW0 up lights LD4 green, SW1 up lights LD5 green.
4. **Power check.** Unplug and replug the board. The design is gone and LD0 stays dark: the bitstream was only in SRAM. Run `make prog` again.

**Clean-checkout check.** This is the real "Done when". Commit your work, clone the repo into a scratch directory and build there, from a shell where you have sourced `export.sh`:

```sh
git clone . /tmp/fresh            # run from your repo root
cd /tmp/fresh/projects/3.1-arty-blink
make prog
```

It must run yosys, nextpnr, `fpga-as` and openFPGALoader in order with no other command typed. It usually fails because a file was never committed (the `.xdc`, `mk/artyz7.mk`), or because a path in a Makefile only worked from your old directory. The chipdb is outside the repo, so it is not rebuilt.

**If nothing happens.** Check that `make prog` reported a successful load, that JP4 is on JTAG, and that the `create_clock` and `clk` pin lines are in the `.xdc`. A design whose clock pin is wrong still builds, and its LED simply never blinks, while the buttons still work. If the buttons work and the blink does not, look at the clock.

**Done when:** LD0 blinks 60 times a minute, the buttons and switches light their LEDs, and one `make prog` in a fresh clone rebuilds everything and loads the board.

## Testbench requirements

**Modules you provide:**

- `rtl/common/blinker.v` from Chapter 0.1, unchanged: `blinker #(CLK_HZ, BLINK_HZ)(i_clk, o_led)`.
- `projects/3.1-arty-blink/top_artyz7.v`:

  ```verilog
  module top_artyz7 #(
      parameter integer CLK_HZ = 125_000_000
  ) (
      input  wire       clk,
      input  wire [3:0] btn,
      input  wire [1:0] sw,
      output wire [3:0] led,
      output wire       led4_r, led4_g, led4_b,
      output wire       led5_r, led5_g, led5_b
  );
  ```

  - `led[0]` = (1 Hz blink at `CLK_HZ`) OR `btn[0]`. The blink starts low and toggles every `CLK_HZ/2` clocks; the first toggle may come up to 4 clocks late (a registered output is fine).
  - `led[3:1]` = `btn[3:1]`, `led4_g` = `sw[0]`, `led5_g` = `sw[1]`, each within 4 clocks of an input change (wires or synchronizers both pass).
  - `led4_r`, `led4_b`, `led5_r`, `led5_b` = 0 at all times. No output may be `x` or `z`.
  - The parameter must be named `CLK_HZ` (the test reads it hierarchically) and must set the blink rate. The test uses `CLK_HZ = 1250` and the default.

**Testbench:** copy `tb_arty_blink.v` to `sim/tb_arty_blink.v` (top module `tb_arty_blink`). Add the per-testbench source hook to `mk/sim.mk` (Section 6) and set `tb_arty_blink_SRCS := $(ROOT)/projects/3.1-arty-blink/top_artyz7.v` there. It runs in under a second and prints one `PASS` or `FAIL` line; `+vcd` writes `tb_arty_blink.vcd`. Without the Makefiles:

```sh
mkdir -p build/sim
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_arty_blink.vvp \
         sim/tb_arty_blink.v projects/3.1-arty-blink/top_artyz7.v
cd build/sim && vvp -n tb_arty_blink.vvp
```

**Other files from this chapter:** `arty-z7-20.xdc` to `boards/arty-z7-20/arty-z7-20.xdc`, and `artyz7.mk` to `mk/artyz7.mk`.
