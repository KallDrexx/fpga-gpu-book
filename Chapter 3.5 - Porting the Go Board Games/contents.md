# Chapter 3.5 - Porting the Go Board Games

Phase 0 made a rule: keep everything board-specific in one thin top module, and keep every other module portable. This chapter is where that rule pays off. Pong from Chapter 2.1 and the UART-driven tile engine from Chapter 2.5 move to the Arty Z7 and come out over HDMI, and not one file in `rtl/common/` changes. What changes is two new top modules, about a hundred lines each.

By the end of this chapter you will have:

- `projects/3.5-pong/top_artyz7.v`: Pong on the Arty's buttons, over HDMI,
- `projects/3.5-scroll/top_artyz7.v`: Chapter 2.5's `uart_ppu`, with the UART on Pmod JA through a USB-serial adapter,
- `sim/tb_arty_pong.v` and `sim/tb_arty_scroll.v` passing: they drive the board pins and receive the HDMI video with this chapter's `sim/hdmi_rx_model.v`,
- proof that every Phase 1 and 2 testbench still passes against the same `rtl/common/`, and
- both games on a monitor, with `host/scroll_demo.py` from Chapter 2.5 driving the tile engine unchanged.

```
                 top_goboard.v (Phase 2)                 top_artyz7.v (this chapter)
 clock           25 MHz crystal                          125 MHz -> clk_gen -> clk_pix 25 MHz
 buttons         i_Switch_1..4, active high              btn[0..3], active high
 score / regs    two 7-segment digits                    led[3:0]
 UART            FTDI channel B on the board             USB-serial adapter on Pmod JA
 video           3 bits per channel, VGA connector       3 -> 8 bits, hdmi_tx, HDMI OUT
                 ───────────────────── same files ─────────────────────
 rtl/common:     vga_timing, synchronizer, pong_game, pong_render,
                 uart_rx, uart_tx, uart_ppu and everything below it
```

## 1. What a port touches

Go through the Go Board top and sort every line into one of two piles. Lines that instantiate a core and connect it to another core stay as they are. Lines that touch a pin, a clock or a board fact (how many bits the DAC has, which way a button reads, which pin the UART is on) are rewritten. If a fact about the Go Board turns out to live inside a core, the core was not portable, and the fix is a parameter, not a copy.

Five board facts change on the Arty. Each gets a section below:

| Fact | Go Board | Arty Z7 | Where it is handled |
| --- | --- | --- | --- |
| clock | 25 MHz crystal | 125 MHz, through the MMCM | `clk_gen`, then everything on `clk_pix` |
| buttons | 4 switches, high when pressed | 4 buttons, high when pressed | top: synchronizers, the mapping |
| displays | two 7-segment digits | four LEDs | top |
| UART | on-board FTDI, pins 73/74 | Pmod JA pins 1/2, external adapter | top and `.xdc`; `CLK_HZ` and `BAUD` parameters |
| video | 9 resistor-DAC wires | HDMI, 8 bits per channel | top: bit expansion, then `hdmi_tx` |

Two more facts are not about the board but about the chip: block RAM and `$readmemh` paths. They are Sections 6 and 7.

## 2. One clock domain: `clk_pix`

The Go Board cores were written for a 25 MHz clock, and `clk_gen` makes exactly that. **Run every game core on `clk_pix`**, together with `vga_timing`. Nothing else is needed: the cores do not know the clock came from an MMCM, and the timing generator, the game tick and the UART bit counters all see the same 25 MHz they saw on the Go Board. The serial clock only drives the serializers inside `hdmi_tx`, and `clk_sys` stays unused until Phase 4.

Wherever a core takes a frequency, pass **`CLK_HZ = 25_000_000`**, the clock the core actually runs on. Writing `125_000_000` because "the Arty is a 125 MHz board" is the classic porting bug. The UART then counts 5 times too many clocks per bit, and the board seems not to hear anything.

**What about the time before `LOCKED`?** Chapter 3.2 promised that this chapter would decide how the reset-less cores start. Here is the decision. The cores keep no reset ports, and keep their initial values. The MMCM's outputs may run at the wrong rate before `LOCKED`, so in the top, hold the two inputs that change game state while `rst_pix` is high:

- the Pong tick: `tick = (x == 0) && (y == V_VISIBLE) && !rst_pix`;
- the serial input: `uart_ppu`'s `i_rx` gets `uart_rx | rst_pix`, so the line looks idle.

Everything else recovers by itself: the counters in `vga_timing` wrap within a frame, and a UART receiver resynchronizes on the next start bit. The HDMI serializers are held in reset by the same `rst_pix` (Chapter 3.4), so the monitor sees nothing until the clock is good.

Drop Chapter 3.4's `btn[0]` reset here. Pong needs all four buttons, and the tile engine has no use for one. Tie `clk_gen`'s `i_rst` to `1'b0`; the MMCM locks by itself after configuration.

## 3. Buttons, switches and LEDs

The Arty's buttons read 1 while pressed, like the Go Board's switches. The port is a straight mapping, but it is worth checking the board's schematic every time: on many boards buttons are active low, and an inverted button makes both paddles slide down as soon as the design starts.

| Arty | Pong | Go Board |
| --- | --- | --- |
| `btn[0]` (BTN0) | left paddle up | Switch 1 |
| `btn[1]` (BTN1) | left paddle down | Switch 2 |
| `btn[2]` (BTN2) | right paddle up | Switch 3 |
| `btn[3]` (BTN3) | right paddle down | Switch 4 |

Pass each through a `synchronizer` clocked by `clk_pix`, as in Chapter 2.1. As there, no `debounce` is needed: the game samples the buttons once per frame. **Do not add one**, even though Phase 0 taught it: `tb_arty_pong` shortens the frame to 160 µs, so even a 1 ms debounce delays every press by several frames, and the test, which expects a press to act within a few frames, fails.

There is no 7-segment display, so the top shows a score in binary on `led[3:0]`: the left player's score while `sw[0]` is down, the right player's while it is up. For the tile engine, `led[3:0]` shows register 0 (LEDS), as the Go Board's four LEDs did. Register 1 (SEG) has nowhere to go; it still reads back over the UART.

## 4. Color: 3 bits to 8, in the top

The Phase 2 cores produce 3 bits per channel, because that is what the Go Board's DAC has. `hdmi_tx` takes 8. Expand in the top, with Chapter 3.4's rule (repeat the bits, `{c, c, c[2:1]}`), right after the core's color outputs and before the one register stage that also carries `de`, hsync and vsync:

```verilog
wire [7:0] r8 = {r3, r3, r3[2:1]};   // same for green and blue
always @(posedge clk_pix) begin
    r_de <= de;  r_hs <= hs;  r_vs <= vs;  r_r <= r8;  ...
end
```

Do not widen the cores' outputs instead. That would change `rtl/common/` and every Phase 2 frame test with it, and the Go Board would then need the opposite truncation in its top. The width of the color is a fact about the display, so it belongs in the top that knows the display. From Phase 4 on, the cores produce 8-bit color themselves (a 256-entry RGB888 palette), and this expansion disappears.

For `uart_ppu`, pass its `o_hsync`, `o_vsync` and `o_de` (already delayed to match its pixel pipeline) to the same register as its colors, exactly as the Go Board top did. For Pong, `pong_render` is combinational, so `vga_timing`'s own syncs go into the register.

## 5. The UART on a Pmod pin

The Arty Z7 has a USB-UART chip, but it is wired to the Zynq's processor side (the PS), not to the FPGA fabric. A fabric design needs its own serial pins. Use a **3.3 V USB-serial adapter** (FTDI, CP2102 or CH340 cables all work) on Pmod JA:

| Adapter | Pmod JA pin | Port | Ball |
| --- | --- | --- | --- |
| TXD (adapter sends) | 1 | `uart_rx` (FPGA input) | Y18 |
| RXD (adapter receives) | 2 | `uart_tx` (FPGA output) | Y19 |
| GND | 5 | | |

Pmod pins 1-4 are the top row, pin 5 is ground and pin 6 is 3.3 V. Leave the adapter's VCC unconnected; the board and the adapter each have their own supply. The Arty's I/O is 3.3 V only, so an adapter set to 5 V can damage it.

The names follow the FPGA's view, as `i_UART_RX` did on the Go Board. Data from the adapter's TXD arrives on `uart_rx`. Naming the ports from the adapter's side and wiring them to match is the second classic bug: the design then drives its own input. If the board does not answer, swap the two wires before suspecting the Verilog.

This chapter's `arty-z7-20.xdc` is Chapter 3.4's file plus two lines that give JA pins 1 and 2 these names:

```
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports {uart_rx}]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports {uart_tx}]
```

They are the same balls as `ja[0]` and `ja[1]`; a design uses one pair of names or the other. The pull-up keeps `uart_rx` high (idle) when no adapter is plugged in. A floating input would otherwise pick up noise and feed random bytes, and so random register writes, into the parser. Write it as `PULLTYPE PULLUP`: nextpnr-xilinx also accepts Vivado's other spelling, `PULLUP TRUE`, but silently ignores it. You can check with `grep PULLUP build/arty_scroll.fasm`, which should print one `PULLTYPE.PULLUP` feature.

`uart_ppu` already takes `CLK_HZ` and `BAUD`. Give the top a `BAUD` parameter (default `115_200`) and pass it down with `CLK_HZ = 25_000_000`. The board build uses the default, so `scroll_demo.py` works unchanged. The parameter exists for the testbench, which runs the link at 781,250 baud, the same 32 clocks per bit as Chapter 2.5's test, to keep the simulation short.

## 6. Block RAM: from `SB_RAM40_4K` to `RAMB18E1`

Chapters 2.2 to 2.5 described their memories as Verilog arrays with registered reads and let yosys pick the block RAM. That is what makes them portable: `synth_ice40` maps an array to `SB_RAM40_4K`s, and `synth_xilinx` maps the same array to `RAMB18E1`s (18 Kbit each) or `RAMB36E1`s. Nothing in the source names the chip.

The 2.5 reference design shows what changes in practice. The table compares the Go Board build with the Arty build of the same files; your numbers depend on how you wrote 2.5:

| Memory | Go Board (`SB_RAM40_4K`, 4 Kbit) | Arty (from `build/yosys.log`) |
| --- | --- | --- |
| tile map, 2,048 x 6 bits used | 3 blocks | 1 `RAMB18E1` |
| tile graphics, 512 x 16 | 2 blocks | 1 `RAMB18E1` |
| palette, 8 x 9 (with `ram_style = "block"`) | 1 block | 1 `RAMB18E1` |
| sprite shadow table, 32 x 8 | 1 block | 2 `RAM32M` (LUT RAM) |
| sprite ROM, 256 x 32 | 2 blocks | LUTs (`using FF mapping for memory ... r_rom`) |

Yosys reports each decision in `build/yosys.log`; `grep "mapping memory\|FF mapping" build/yosys.log` lists them. Three things are new compared with the iCE40:

- **Bigger blocks.** One `RAMB18E1` holds what took three or four `SB_RAM40_4K`s. The Z7-20 has 140 `RAMB36E1`s (280 halves), so these games use about 1% of it.
- **LUT RAM.** A 7-series LUT can be a 32- or 64-entry RAM, so yosys puts small memories into LUTs instead of wasting an 18 Kbit block on 32 bytes. It also turned the constant sprite ROM into logic, about 140 LUTs. Marking the ROM `(* ram_style = "block" *)` would move it into a fourth `RAMB18E1`. Yosys reads this attribute for every architecture, so it would stay portable, but the Z7-20 has 53,200 LUTs and the change would touch `rtl/common/`, so leave it.
- **Asynchronous reads now exist.** LUT RAM can be read without a clock. Do not start using that in `rtl/common/`: the iCE40 cannot do it, and the cores would stop fitting the Go Board.

What does not port is a **primitive instantiated by name**. If you wrote `SB_RAM40_4K` (or `SB_PLL40_CORE`, `SB_IO`) directly in a Phase 2 module, yosys for Xilinx stops with an unknown-module error. Rewrite it as an inferred array (Chapter 2.3 shows the pattern) and rerun the Phase 2 tests. Xilinx primitives such as `MMCME2_BASE` and `OSERDESE2` live in `rtl/xc7/` for the same reason.

## 7. `$readmemh` paths

A relative path in `$readmemh` is relative to the directory the tool runs in, not to the source file. Three tools read these files, from three places:

| Tool | Runs in | `rtl/common/tiles.hex` is |
| --- | --- | --- |
| yosys, from a project Makefile | `projects/3.5-scroll/` | `../../rtl/common/tiles.hex` |
| `make test` (vvp) | `build/sim/` | `../../rtl/common/tiles.hex` |
| `make frame` (vvp) | the repo root | `rtl/common/tiles.hex` |

So a board top passes `TILE_FILE("../../rtl/common/tiles.hex")` and `SPRITE_FILE("../../rtl/common/sprites.hex")`, as the Go Board top did. Projects and `build/sim/` are both two levels below the root, so the same string works for the build and for `make test`. The Phase 1 and 2 frame tests pass root-relative paths themselves (and write `build/frame_*.ppm`), which is right for `make frame`, the way Chapter 1.2 runs them, from the root.

A wrong path is not always an error. Icarus prints a warning and leaves the memory at X. Yosys, if it cannot find the file in its working directory, also tries the directory of the source file, so a default like `"tiles.hex"` can work in one tool and fail in another. If the HDMI picture is X in simulation while the UART works, check the paths first.

## 8. The two tops

### `projects/3.5-pong/top_artyz7.v`

```verilog
module top_artyz7 #(
    parameter integer V_VISIBLE = 480,
    parameter integer V_FRONT   = 10,
    parameter integer V_SYNC    = 2,
    parameter integer V_BACK    = 33
) (
    input  wire       clk,
    input  wire [3:0] btn,
    input  wire [1:0] sw,
    output wire [3:0] led,
    output wire       hdmi_tx_clk_p,
    output wire       hdmi_tx_clk_n,
    output wire [2:0] hdmi_tx_d_p,
    output wire [2:0] hdmi_tx_d_n
);
```

Inside: `clk_gen` (`i_rst` tied to 0), `vga_timing` on `clk_pix` with the four `V_` parameters passed through, five synchronizers (`btn[3:0]` and `sw[0]`), the gated tick from Section 2, `pong_game` and `pong_render` with their default parameters (`i_load` tied to 0, as on the Go Board), the bit expansion, one register stage and `hdmi_tx`. `led` is `sw[0] ? score_r : score_l`.

Note the tick: `y == V_VISIBLE`, the parameter, not the number 480. On the board they are the same. The testbench shortens the frame, and a tick that waits for line 480 never comes.

### `projects/3.5-scroll/top_artyz7.v`

```verilog
module top_artyz7 #(
    parameter integer V_VISIBLE = 480,
    parameter integer V_FRONT   = 10,
    parameter integer V_SYNC    = 2,
    parameter integer V_BACK    = 33,
    parameter integer BAUD      = 115_200
) (
    input  wire       clk,
    output wire [3:0] led,
    input  wire       uart_rx,
    output wire       uart_tx,
    output wire       hdmi_tx_clk_p,
    output wire       hdmi_tx_clk_n,
    output wire [2:0] hdmi_tx_d_p,
    output wire [2:0] hdmi_tx_d_n
);
```

Inside: `clk_gen`, `vga_timing` as above, `uart_ppu` with `CLK_HZ(25_000_000)`, `BAUD(BAUD)` and the two file paths from Section 7, its `i_rx` held idle during reset, its `o_tx` on `uart_tx`, `o_reg0[3:0]` on `led`, then the expansion, the register stage and `hdmi_tx`.

### Makefiles and hooks

Each project's Makefile is Chapter 3.4's with its own `PROJ` (`arty_pong`, `arty_scroll`) and the files the design uses: the HDMI path (`clk_gen`, `hdmi_tx` and `tmds_serializer` from `rtl/xc7/`, `reset_sync` and `tmds_encoder` from `rtl/common/`), `vga_timing`, and the game's own modules. List every module yosys needs, including the small ones `iverilog -y` finds by itself: the reference scroll build first failed with ``Module `\synchronizer' referenced in module `\uart_rx' ... is not part of the design``, because `uart_rx` uses one internally. Set `TESTS := tb_arty_pong` and `TESTS := tb_arty_scroll`, and in `mk/sim.mk` add:

```make
tb_arty_pong_SRCS   := $(ROOT)/projects/3.5-pong/top_artyz7.v
tb_arty_scroll_SRCS := $(ROOT)/projects/3.5-scroll/top_artyz7.v
```

Copy this chapter's `arty-z7-20.xdc` over `boards/arty-z7-20/arty-z7-20.xdc`.

## 9. Prove "unchanged"

"The cores are portable" is a claim you can check mechanically. At the root of the repo:

```sh
git diff --stat <your Step 2.5 commit> -- rtl/common    # must print nothing
make test                                              # the testbenches, Phases 0-3
make frame                                             # the frame tests, Phases 1-2
```

`make test` with no `TESTS` runs every `sim/tb_*.v` from `build/sim/`, and that would include the `tb_frame_*` testbenches. Run from there, their root-relative paths find neither the `.hex` files nor `build/`, and they fail; they belong to `make frame`. Chapter 1.2 asked you to leave them out; if your `mk/sim.mk` does not yet, make it: `ALL_TESTS := $(filter-out tb_frame_%,$(sort $(basename $(notdir $(wildcard $(ROOT)/sim/tb_*.v)))))`.

The first line proves that porting did not edit a core. The other two prove that the cores still do what they did: the Phase 1 and 2 tests (`tb_pong_game`, the UART tests, and every frame test from Chapters 1.2 to 2.5) read exactly the same `rtl/common/` files that the Arty builds read. If a test fails here, the port changed something it should not have. Revert it, and solve the problem in the top or with a parameter instead.

Then build both projects for the Arty. That is the other half of portability: the same files synthesize for a second chip. A Go Board build of an old project (`make build P=2.5-scroll`) should still work too; `make build-all` does all of them.

## 10. Simulate

Copy `hdmi_rx_model.v`, `tb_arty_pong.v` and `tb_arty_scroll.v` from this chapter to `sim/`, next to the Chapter 3.2 and 3.4 models.

**`hdmi_rx_model.v`** is Chapter 3.4's testbench receiver turned into a module. Connect it to the four TMDS pairs and it gives back what a monitor would see: a recovered pixel clock `o_clk` and, per pixel clock, `o_de`, `o_hsync`, `o_vsync` and 8-bit `o_red`, `o_grn`, `o_blu`, plus `o_ok` for words that decoded cleanly. Internally it does what `tb_hdmi` did: it locks onto the clock pair, finds the word boundaries from the control symbols and checks every data symbol against the DVI 1.0 encoder. Its outputs are stable at each rising edge of `o_clk`, so Chapter 1.2's `frame_dump` with `BITS = 8` can save what arrives over HDMI as a PPM. (Phase 4's frame tests take a shorter road and dump the 8-bit colors in front of `hdmi_tx`; the model is there whenever you want to check a picture end to end.)

Both testbenches touch only the board pins. Like `tb_hdmi`, they keep the real 800-clock lines and shorten the frame through the `V_` parameters.

**`tb_arty_pong`** uses a 5-line frame, so the visible picture is logical row 0, the top edge of the court, and 40 game ticks take 6.4 ms. It holds `btn[0]` and `btn[2]` from power-up; after 40 frames both paddles must have reached the top (the centred paddles need 35 ticks), white, with the net's grey top dash between them (`3'b011` must arrive as `8'h6D`). Then it presses `btn[1]` (only the right paddle stays at the top) and `btn[3]` (neither does). Each check compares a whole frame, clock for clock. At the end, `led` must read `0000` with `sw[0]` at either position. It takes about two minutes.

**`tb_arty_scroll`** uses a 17-line frame, which shows logical rows 0 to 3 of the map. Over `uart_rx` at 781,250 baud it sends `W 00 53` and checks `led = 0011`, then `R 00` and checks that `uart_tx` answers `0x53` at the right bit rate. It writes palette entries 0-3 (chosen so that every 3-bit level appears and no two channels are equal), map row 0 (tiles 1 to 31, read from `rtl/common/tiles.hex`) and the four sprites' ATTR bytes (off). Then two received frames must match its own drawing pixel for pixel. A short frame never reaches line 480, so the scroll and sprite registers are never committed; the test does not depend on them. It takes about a minute.

Chapter 3.4's tip applies here even more: setting `CLKOUT3_DIVIDE` to `CLKOUT6_DIVIDE` to 128 in `clk_gen` nearly halves the run times (the reference `tb_arty_scroll` goes from 57 to 30 seconds).

The messages point at the usual porting bugs: `CLK_HZ` set to 125 MHz (no answer to the first packet), `uart_rx` and `uart_tx` swapped or with the wrong direction, `led` reversed, colors shifted instead of bit-repeated (`80 a0 c0` instead of `92 b6 db`), red and blue swapped, color one clock off from `de`, a button inverted or two buttons swapped, a tick that waits for line 480, and an X picture from a `$readmemh` path.

## 11. Build and read the report

Run `make` in each project directory. The reference builds took 11 and 16 seconds:

```
== arty_pong: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:     919/ 106400     0%
  SLICE_FFX:     164/ 106400     0%
  CARRY4:      92/  13300     0%
  ...
  OSERDESE2:       8/    200     4%
  MMCME2_ADV:       1/      4    25%
  Max frequency for clock 'clk_pix': 149.45 MHz (PASS at 25.00 MHz)

== arty_scroll: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:     997/ 106400     0%
  SLICE_FFX:     555/ 106400     0%
  CARRY4:      47/  13300     0%
  ...
  RAMB18E1:       3/    280     1%
  SELMUX2_1:      80/  40700     0%
  Max frequency for clock 'clk_pix': 153.82 MHz (PASS at 25.00 MHz)
```

How to read it:

- **`SLICE_LUTX` counts LUT halves** (the chip has 53,200 LUT6s, each with two outputs). Yosys's own count, at the end of `build/yosys.log`, is 336 LUTs for Pong and 667 for the tile engine, plus 2 `RAM32M` and 3 `SRL16E` (shift registers in LUTs, which yosys builds from chains of plain flip-flops such as delay lines). On the Go Board the same tile engine used 944 of 1,280 logic cells and 9 of 16 RAM blocks. Here it uses about 1% of everything except the MMCM.
- **`RAMB18E1` 3**: the map, the tile graphics and the palette (Section 6). Pong has no memory at all.
- **`SELMUX2_1` 80**: the `MUXF7`/`MUXF8` multiplexers that combine LUTs into wider functions (yosys counts 55 and 25). All of them belong to the sprite ROM in logic; a test build with the ROM in block RAM had none.
- **No DSP slices.** The bit expansion is wiring, and nothing in the Phase 2 cores multiplies.
- **`clk_pix` could run at about 150 MHz**, six times faster than it needs to. The critical-path report in `build/nextpnr.log` names the source lines: for the reference Pong it ends in `pong_game`'s ball-edge compare, for the tile engine in a TMDS encoder. Phase 4's 50 MHz drawing engine has plenty of room.
- **`IOB33_INBUF_EN`**: 7 for Pong (clock, four buttons, two switches), 2 for the tile engine (clock and `uart_rx`).

## 12. On the board

**Pong.** Connect the monitor to HDMI OUT and run `make prog` in `projects/3.5-pong/`. Then repeat Chapter 2.1's checks with the Arty's buttons:

1. The ball waits in the centre for about one second, then moves right and down.
2. BTN0 and BTN1 move the left paddle up and down, BTN2 and BTN3 the right one. Both stop flush at the top and bottom.
3. The ball bounces off a paddle's very top and very bottom edge, and passes a paddle one ball-height away.
4. With `sw[0]` down the LEDs count the left player's points in binary, with it up the right player's. The next serve goes toward the player who missed.
5. At 9 points (LEDs `1001`) the game pauses for about three seconds, then both scores return to 0.
6. The net has a dash at the very top, and nothing tears or flickers. The colors are pure white, yellow and grey.

**The tile engine.** Wire the adapter to Pmod JA (Section 5) and plug it into the PC. Find its port: run `ls /dev/ttyUSB*` before and after plugging it in. The Arty's own USB connection also adds serial ports, so the adapter may be `/dev/ttyUSB2` rather than `ttyUSB0`. Run `make prog` in `projects/3.5-scroll/`, then:

1. `.venv/bin/python host/scroll_demo.py /dev/ttyUSBn` prints `map drawn: 6306 bytes in 0.55 s`, and LD0 and LD2 light up (LEDS = `0x5`, read back by the script's link check).
2. Go through the rest of Chapter 2.5's list: the starfield and the ship, smooth diagonal scrolling, the seamless wrap at the map's edge, `--dx 1 --dy 0` crossing X = 256 without a flash, and about 60 frames per second when you press Ctrl-C. The frame rate is 25 MHz / 420,000 = 59.52 Hz, the same as the Go Board, because `clk_pix` is the same 25 MHz.
3. Unplug the adapter while the board runs. The picture holds, and nothing changes on screen: the pull-up keeps the line idle. Plug it back in and run the script again.

**If the script reports no answer**, swap the TXD and RXD wires first, then check GND, that the adapter is set to 3.3 V, and that `tb_arty_scroll` passes. **If the picture is right but the colors are off**, compare it with the same scene on the Go Board: with 8-bit output the colors should look the same or cleaner, never darker. A dim white means the expansion shifts instead of repeating the bits.

**Done when:** `make test` and `make frame` pass with `rtl/common/` unchanged since Chapter 2.5, `tb_arty_pong` and `tb_arty_scroll` print `PASS`, and both games run on the monitor, the tile engine driven by `scroll_demo.py` through the adapter.

## Testbench requirements

**Modules you provide:**

- `projects/3.5-pong/top_artyz7.v`: `top_artyz7 #(V_VISIBLE=480, V_FRONT=10, V_SYNC=2, V_BACK=33)(clk, btn[3:0], sw[1:0], led[3:0], hdmi_tx_clk_p, hdmi_tx_clk_n, hdmi_tx_d_p[2:0], hdmi_tx_d_n[2:0])`.
  - From a 125 MHz `clk`: Chapter 3.4's DVI output (exact TMDS, channel 0 blue with `{vsync, hsync}`, channel 1 green, channel 2 red, clock pair `1111100000`), 640-pixel lines of 800 clocks and the `V_` parameters' line counts.
  - The picture is `pong_render`'s with default parameters, each channel expanded to 8 bits by repeating its bits. `de`, the syncs and the color share one latency.
  - `pong_game` with default parameters, ticked once per frame at the start of vertical blanking (`y == V_VISIBLE`, from the parameter), not before `clk_pix` is good. `btn[0]`/`btn[1]` move the left paddle up/down, `btn[2]`/`btn[3]` the right one, active high, through synchronizers only (no `debounce`): a button change acts on the next tick or the one after.
  - `led[3:0]` is the left score while `sw[0]` is 0, the right score while it is 1 (only `0000` is checked).
- `projects/3.5-scroll/top_artyz7.v`: `top_artyz7 #(V_VISIBLE=480, V_FRONT=10, V_SYNC=2, V_BACK=33, BAUD=115_200)(clk, led[3:0], uart_rx, uart_tx, hdmi_tx_clk_p, hdmi_tx_clk_n, hdmi_tx_d_p[2:0], hdmi_tx_d_n[2:0])`. Any other input you add is left unconnected by the test.
  - The same DVI output, line timing and color expansion; the picture is `uart_ppu`'s, with its delayed syncs.
  - `uart_ppu` on `clk_pix` with `CLK_HZ = 25_000_000` and the top's `BAUD`. `uart_rx` is an input and idles high; `uart_tx` is driven by `uart_ppu` and idles high. Chapter 2.5's packets and register and VRAM maps; at 32 clocks per bit, `led` changes within 4 bit times of a packet's last byte and a read reply starts within 200 bit times.
  - `led[3:0]` is register 0x00 bits 3:0, `led[0]` = bit 0.
  - Tile graphics loaded from `rtl/common/tiles.hex`, found from `build/sim/` (`TILE_FILE("../../rtl/common/tiles.hex")`); the map row and palette entries the test writes must show at once (VRAM writes are immediate in Chapter 2.5).
- Below them, your `clk_gen`, `reset_sync`, `tmds_encoder`, `tmds_serializer` and `hdmi_tx` (Chapters 3.2-3.4), and the unchanged Phase 0-2 modules in `rtl/common/`.

**Files from this chapter:** `hdmi_rx_model.v`, `tb_arty_pong.v` and `tb_arty_scroll.v` to `sim/`; `arty-z7-20.xdc` to `boards/arty-z7-20/arty-z7-20.xdc`. In `mk/sim.mk`, `tb_arty_pong_SRCS := $(ROOT)/projects/3.5-pong/top_artyz7.v` and `tb_arty_scroll_SRCS := $(ROOT)/projects/3.5-scroll/top_artyz7.v`. They also need Chapter 3.2's and 3.4's models (`MMCME2_BASE.v`, `BUFG.v`, `OSERDESE2.v`, `OBUFDS.v`) in `sim/`.

**Testbenches:** `sim/tb_arty_pong.v` (top module `tb_arty_pong`, vertical timing overridden to `V_VISIBLE=2, V_FRONT=1, V_SYNC=1, V_BACK=1`, about two minutes) and `sim/tb_arty_scroll.v` (top module `tb_arty_scroll`, `V_VISIBLE=8, V_FRONT=3, V_SYNC=2, V_BACK=4`, `BAUD=781_250`, about one minute; `+tiles=<path>` if it is not run from `build/sim/`). Each prints one `PASS` or `FAIL` line; `+vcd` writes a large `.vcd`. Without the Makefiles:

```sh
mkdir -p build/sim
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y rtl/xc7 -y sim -o build/sim/tb_arty_scroll.vvp \
         sim/tb_arty_scroll.v projects/3.5-scroll/top_artyz7.v
cd build/sim && vvp -n tb_arty_scroll.vvp
```

(and the same for `tb_arty_pong` with `projects/3.5-pong/top_artyz7.v`).
