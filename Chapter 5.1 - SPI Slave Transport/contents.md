# Chapter 5.1 - SPI Slave Transport

Phase 4 ended with a GPU that obeys packets from a UART. That suits a PC with a USB-serial adapter, but not a microcontroller. At 115,200 baud the UART carries about 11,500 bytes per second, and a 60 Hz scene that sends 1 KB per frame needs 60 KB per second. Faster baud rates help, but many MCUs cannot produce 2.5 Mbaud exactly, and a UART has no framing: one lost byte puts the parser out of step.

**SPI** (Serial Peripheral Interface) is what MCUs are good at. It carries its own clock, so there is no baud rate to agree on. It is fast: 1 MHz is 125 KB per second, and this chapter's design accepts up to about 6 MHz. And it has a chip-select line that frames the data. This chapter puts the GPU on an SPI bus as a **slave**.

By the end of this chapter you will have:

- `rtl/common/spi_slave.v`: an SPI mode 0 slave that runs entirely in the 50 MHz `clk_sys` domain,
- `rtl/common/gpu_link.v`: `uart_gpu` with the SPI slave next to the UART, both feeding the same command FIFO, plus a `READY` output, a status byte and replies over SPI,
- `sim/tb_spi_slave.v`, `sim/tb_link_arb.v` and `sim/tb_gpu_spi.v` passing. The last replays Chapter 4.8's whole test stream over SPI and gets the same framebuffers and `FB_CRC` replies as the UART path,
- `projects/5.1-spi/` built for the Arty with the SPI pins on Pmod JB, and `host/spi_test.py` checking CRCs through an FT232H USB-SPI bridge.

## 1. SPI in one page

SPI has four wires. The master drives `SCK` (clock), `MOSI` (master out, slave in) and `CS` (chip select, also called `SS`). The slave drives `MISO` (master in, slave out). Data moves in both directions at once: every `SCK` cycle the master sends one bit and receives one bit.

SPI comes in four **modes**, set by two bits: `CPOL` is the idle level of `SCK`, and `CPHA` says which edge samples. We use **mode 0** (`CPOL` = 0, `CPHA` = 0), the most common:

```
CS    ‾‾‾\_______________________________________/‾‾‾
SCK   ____/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\___________
MOSI  ---< b7 >< b6 >< b5 >< b4 >< b3 >< b2 >< b1 >< b0 >---
MISO  ---< b7 >< b6 >< b5 >< b4 >< b3 >< b2 >< b1 >< b0 >---
          ^     ^                                    ^
       sample  sample                          last sample
```

- `SCK` idles **low**. `CS` is **active low**: the slave takes part only while `CS` is low.
- Both sides **sample on the rising edge** of `SCK` and **change** their output after the **falling** edge, so the data has half a clock period to settle. The first bit, b7, must already be on the wire when `CS` falls, since no falling edge comes before it.
- **Most significant bit first**, eight bits per byte.
- One `CS` low period is a **frame**. It holds any number of bytes. Raising `CS` ends the frame and drops any incomplete byte.

Two consequences shape the design. The slave can only answer while the master clocks; it never sends on its own. And to receive a byte the master must send one, so reading a reply means sending bytes.

## 2. Oversampling a slow clock

The slave must react to `SCK`, an input it does not control. There are two ways to do that:

1. **Use `SCK` as a clock**: shift registers clocked by `SCK` itself. Then every byte has to cross into the 50 MHz domain, which needs a handshake or an asynchronous FIFO, and timing constraints for a clock the tools know nothing about.
2. **Oversample it**: treat `SCK`, `MOSI` and `CS` as ordinary slow inputs and look at them with the 50 MHz clock. Everything stays in one clock domain.

We take the second. It works while `SCK` is much slower than the clock.

**Synchronize.** The three pins change at arbitrary moments relative to `clk_sys`, so each goes through two flops, as with the buttons in Chapter 0.3 (the `synchronizer` module). A third flop on `SCK` holds its previous value for edge detection:

```verilog
reg [2:0] r_sck;
always @(posedge i_clk) r_sck <= {r_sck[1:0], i_sck};
wire sck_rise = r_sck[1] & ~r_sck[2];     // one clock wide
wire sck_fall = ~r_sck[1] & r_sck[2];
```

`CS` and `MOSI` need only the two flops. All three pins pass through the same two-flop delay, so their order survives: in the clock where `sck_rise` is high, `r_mosi[1]` holds `MOSI` as it was within 20 ns after the rising edge. Since the master changes `MOSI` only after the falling edge, that is the bit we want.

**Why only up to about 6 MHz?** `MISO` is the tight direction. Count how long the slave takes to change it after `SCK` falls (`clk_sys` has a 20 ns period):

- The first flop catches the edge 0 to 20 ns after it happens, and the second flop one clock later. `sck_fall` is then high for one clock, and the register that drives `MISO` changes at the end of it.
- Worst case, `MISO` changes **60 ns** after `SCK` fell. The flop's output and the pin add about 5 ns: **65 ns**.
- The master samples `MISO` at the next rising edge, half an `SCK` period after the falling one, and needs the bit a little earlier, its **setup time**: 5 to 10 ns for a typical MCU. Wires add a few ns.

So half the `SCK` period must be at least 65 ns plus setup plus wiring, about 80 ns:

| SCK | Half period | Slack after 65 ns + 10 ns setup |
| --- | --- | --- |
| 1 MHz | 500 ns | 425 ns |
| 4 MHz | 125 ns | 50 ns |
| 5 MHz | 100 ns | 25 ns |
| 6.25 MHz | 80 ns | 5 ns |
| 8 MHz | 62.5 ns | negative |

Nothing breaks suddenly above 6 MHz; the slack runs out and the master starts reading the previous bit. The testbenches go up to 6.25 MHz. Run real hardware at 5 MHz or below.

The other directions have more room. `MOSI` must hold for 20 ns after the rising edge and the master holds it for a half period, 80 ns. Each `SCK` level must last two `clk_sys` periods (40 ns) so the synchronizer cannot miss it, which allows up to 12 MHz.

The same delays give the master its timing rules:

| Master rule | Why |
| --- | --- |
| `SCK` at most 6.25 MHz (5 MHz on hardware) | the `MISO` budget above |
| At least 100 ns from `CS` falling to the first `SCK` rising edge | the slave must see `CS` low before the first bit |
| `CS` high for at least 100 ns between frames | the slave must see `CS` high, to reset its bit counter |
| No minimum from the last `SCK` falling edge to `CS` rising | the last bit was sampled half a period earlier |

## 3. The slave

`spi_slave` turns pins into bytes and bytes into `MISO` bits. It knows nothing about the GPU.

| Port | Dir | Meaning |
| --- | --- | --- |
| `i_clk` | in | `clk_sys`, 50 MHz |
| `i_sck`, `i_mosi`, `i_cs_n` | in | asynchronous pins |
| `o_miso` | out | the pin; a register output |
| `o_rx_valid`, `o_rx_data[7:0]` | out | a one-clock pulse for each complete byte received |
| `o_frame` | out | synchronized `CS` is low |
| `i_tx_data[7:0]` | in | the byte to send next |
| `o_tx_take` | out | a one-clock pulse when the first bit of a byte has been clocked |

The receive side is a shift register and a 3-bit counter:

```verilog
if (!o_frame) r_bits <= 0;                          // CS high: start over
else if (sck_rise) begin
    r_in   <= {r_in[6:0], r_mosi[1]};               // MSB first
    r_bits <= r_bits + 1;
    if (r_bits == 7) begin o_rx_valid <= 1; o_rx_data <= {r_in[6:0], r_mosi[1]}; end
end
```

The byte is delivered on its eighth rising edge, well before the next byte begins. Resetting the bit counter while `CS` is high makes a half-sent byte harmless: the next frame starts at bit 0 whatever the master did before. Without it, one frame that ends early shifts every later byte. The testbench cuts frames short to check this.

The transmit side is a second shift register, `r_out`, with `o_miso = r_out[7]`. It uses the same counter, on falling edges:

- While `CS` is high, `r_out` loads `i_tx_data` every clock. When `CS` falls it stops, so byte 0 is on the wire before the first edge. Byte 0 is therefore a snapshot of `i_tx_data` at `CS` fall.
- On the falling edges after bits 1 to 7, shift left.
- On the falling edge after bit 8, load `i_tx_data` for the next byte.

That load cannot wait for anything: Section 2 showed there is no time to spare after the edge. So `i_tx_data` must hold the next byte well before the load. `o_tx_take` makes that possible. It pulses on the first rising edge of each byte, which tells the logic outside that byte k has started and it may now put byte k + 1 on `i_tx_data`. That leaves seven bit times to do so.

Write `spi_slave` yourself from these rules. `tb_spi_slave` (Section 7) checks each one.

## 4. Two links, one FIFO

`gpu_link` is Chapter 4.2's `uart_gpu` with the SPI slave added. Copy `uart_gpu.v` to `gpu_link.v` (keep `uart_gpu` as it is, so the Phase 4 testbenches still run) and give it these extra ports:

```verilog
input  wire i_sck, i_mosi, i_cs_n, output wire o_miso,   // SPI pins
input  wire i_vblank,                                    // fb_scanout's o_vblank, synchronized to clk_sys
```

All of `uart_gpu`'s ports stay. Both receivers write the one FIFO, and the command engine reads it without knowing where a byte came from:

```
uart_rx --\                                     /--> uart_tx
           >-- FIFO --> cmd_engine --> replies <
spi_slave -/                                    \--> reply queue --> MISO
```

Two senders can share one byte stream only if they take turns at packet boundaries, and the FIFO cannot enforce that: a byte-by-byte merge of two packets is garbage. So the rule is **one link at a time**, and SPI wins: a UART byte is written only while no SPI frame is open (`o_frame` low) and no SPI byte arrives in the same clock. A UART byte that arrives during an SPI frame is dropped.

```verilog
wire u_ok  = rv && !frame && !sv;                    // rv: UART byte, sv: SPI byte
wire wr_en = sv | u_ok;
wire [7:0] wr_data = sv ? sd : rd;
```

`o_overflow` works as before, now for both links, and is still sticky.

## 5. READY: back-pressure

The command FIFO holds 512 bytes. The master can send far faster than the engine finishes long commands: a `CLEAR` takes 76,800 clocks, 1.5 ms, and in that time a 6 MHz master sends over 1,100 bytes. The UART was slow enough that this rarely mattered; SPI is not. The master has to be told to wait, so `o_ready` also drives an SPI **`READY`** pin.

- `READY` is **high** when the master may send and **low** when it must wait.
- It is high while the FIFO has **at least 272 free bytes** (`count <= 240`): room for one largest packet (2 + 255 = 257 bytes) plus 15 bytes of slack for the synchronizer and pin delays.
- The rule for the master: **check `READY`; while it is high you may send up to 257 bytes before you check again.** Checking before each frame of at most 257 bytes is the usual way.
- While `READY` is low the master pauses. It may keep `CS` low with `SCK` idle (the slave does not mind a gap between bytes), or end the frame and start a new one later.

`uart_cts` stays on pin Y16 and is still `~o_ready`, so the UART gets the same threshold. In Chapter 4.2 `o_ready` needed only 32 free bytes; SPI's speed is why it now needs 272. The FIFO stays at 512 bytes.

## 6. Replies over SPI

**Where replies go.** The engine's reply bytes (only `FB_CRC` so far) go to the link that sent the most recent byte written into the FIFO. A flag `r_src` is set when an SPI byte is written and cleared when a UART byte is. If it is clear, replies go to `uart_tx` as before; if it is set, they go into a 16-byte **reply queue** (room for four `FB_CRC` replies). The queue copies `uart_tx`'s handshake with the engine: it is busy for one clock after each byte written, and while it is full. If the master stops reading, the engine waits at the fifth reply, the FIFO fills and `READY` falls. Nothing is lost.

**What `MISO` says.** Every frame returns bytes on `MISO`, whether the master wants them or not:

| MISO byte | Command frame | Read frame (first MOSI byte `0xFF`) |
| --- | --- | --- |
| 0 | status | status |
| 1 | count: reply bytes queued when `CS` fell (0 to 16) | count |
| 2, 3, ... | `0x00` | the first `count` reply bytes, oldest first, then `0x00` |

A **read frame** is a frame whose first MOSI byte is `0xFF`. None of its MOSI bytes enter the FIFO, and only read frames take bytes out of the reply queue. `0xFF` is never an opcode, so no command frame starts with it. In Section 4's code, `wr_en` therefore uses `s_ok` in place of `sv`: an SPI byte, unless this frame started with `0xFF` (byte 0 decides, and is itself dropped). Read frames don't change `r_src` either, since they write nothing.

Why not read by sending `NOP`s, which the parser ignores anyway? Because they would go through the FIFO. With the queue full the engine waits, the FIFO fills, `READY` falls, and the master could no longer send the bytes it needs to read the queue: a deadlock. A read frame needs no FIFO space, so the master may send one at any time, even while `READY` is low.

**The status byte:**

| Bit | Name | Meaning |
| --- | --- | --- |
| 7:6 | | always `01`. A stuck-low `MISO` reads `00` and stuck-high reads `11`, so a wrong wire is obvious |
| 5 | | reserved, 0 |
| 4 | `OVERFLOW` | sticky: the FIFO dropped a byte (same as `o_overflow`) |
| 3 | `VBLANK` | `i_vblank`, the `VSYNC` pin of Chapter 4.5 |
| 2 | `REPLY` | at least one reply byte is queued |
| 1 | | reserved, 0 |
| 0 | `READY` | the same as the `READY` pin |

A healthy idle GPU shows `0x41`: the signature and `READY`. With a reply queued it shows `0x45`.

**Building the bytes.** The glue logic between the queue and the slave drives `i_tx_data`. While `CS` is high it shows the live status byte and keeps a copy of the queue's fill level as `left`. When `CS` falls, the slave freezes the status in `r_out` and the glue stops updating `left`, so status and count are snapshots of the same moment. Then each `o_tx_take` says which byte has just begun:

| Byte that began | Put on `i_tx_data` | Also |
| --- | --- | --- |
| 0 (status) | `left` (the count) | |
| 1 (count) | read frame and `left` > 0: the oldest queued byte; else `0x00` | |
| 2 and later | read frame and `left` > 1: the queued byte after the oldest; else `0x00` | read frame and `left` > 0: remove the oldest byte (it is the one now on `MISO`), `left` -= 1 |

The glue knows whether this is a read frame from byte 0, which `o_rx_valid` delivers before byte 1 begins.

A byte leaves the queue when **its first bit is clocked**, not when the slave loads it. That matters: the slave loads the next byte after every eighth bit, even when the master ends the frame right there. Removing at load would lose a reply byte each time a master stops reading early. A byte whose first bit was never clocked stays in the queue. (A byte cut off part way is lost, so always clock whole bytes.)

**Reading a reply** takes two frames:

1. Send `FB_CRC`, `04 00`. `MISO` returns `41 00`: ready, nothing queued.
2. After the CRC is done (about 1.5 ms), send a read frame of six bytes: `FF` and five more, any value. `MISO` returns `45 04 c0 c1 c2 c3`: status, count 4 and the four CRC bytes, low byte first as on the UART.

If the count is smaller than you need, read again later. The count says how many of the bytes are real, so a `0x00` reply byte is never mistaken for filler.

## 7. Simulate

Three testbenches. All run from `build/sim/` like the earlier ones.

**`tb_spi_slave`** tests the slave alone, in about 10 seconds. A master model runs 4,000 frames with `SCK` half-periods from 1000 ns down to 80 ns (0.5 to 6.25 MHz), every edge jittered and every frame at a random phase against the 50 MHz clock. It checks the received bytes (each before the next byte begins), the `MISO` bytes and `o_frame`, cuts some frames after 1 to 7 extra bits, raises `CS` right after the last `SCK` edge (sometimes in the same instant), and checks that `MISO` never changes while `SCK` is high. It samples `MISO` 10 ns before each rising edge, like a real master with that setup time.

**`tb_link_arb`** checks the rules of Sections 4 to 6 on a `gpu_link` in about 30 seconds: a UART session, an SPI session read back with read frames, a read frame whose bytes would be a `CLEAR`, a UART byte dropped during a frame, the next reply going back to the UART, a command frame that must not deliver replies, five unread `FB_CRC` replies that stop the engine while the host sends single bytes until `READY` falls (it checks that `READY` promised room for 257 bytes, and that read frames still work while it is low), and the overflow flag.

**`tb_gpu_spi`** is the main one. It replays `sim/blit_stream.hex`, Chapter 4.8's whole stream (78,000 bytes, every Phase 4 command), into `gpu_link` over SPI and checks everything the UART testbench did: all `FB_CRC` replies, both framebuffers, the palette, the counting checks and the throughput windows. The master cuts the stream into frames of random length at packet boundaries, at random `SCK` rates, with random gaps, always honoring `READY`. At each fence it reads replies with read frames of random length. Chapter 4.8's stream has one section that sends 181 bytes "without regard to `o_ready`" right behind the biggest `BLIT`. That works at 2.5 Mbaud, where the FIFO is nearly empty at that point. An SPI master that has just used its 257-byte allowance may leave the FIFO nearly full, and 181 more bytes would overflow it, so this testbench honors `READY` there too. Expect `PASS` and a line like:

```
PASS: tb_gpu_spi (78164 bytes in 2278 frames and 2014 polls, ... 54 FB_CRC replies match, ...)
```

It takes about 4 minutes with Icarus:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_spi.vvp sim/tb_gpu_spi.v
cd build/sim && vvp -n tb_gpu_spi.vvp
```

Add the three names to `TESTS` in `projects/5.1-spi/Makefile`, and keep Chapters 4.2 to 4.8's testbenches in it: `uart_gpu` has not changed.

**When it fails.** Each message names its cause:

- *Received bytes wrong* in `tb_spi_slave`: you sample on the wrong edge, shift the wrong way, or don't reset `r_bits` while `CS` is high.
- *Delivered after the next byte began*, or *a byte missing at the end of the CS gap*: deliver each byte on its eighth rising edge, not later.
- *MISO changed while SCK was high*: you update `MISO` on the rising edge. Do it on the falling one.
- *MISO byte wrong at some rates only*: the bit arrives too late. `MISO` must come straight from a register, with no extra stage.
- *Read frame was drawn* or wrong framebuffers: a read frame's bytes reached the FIFO.
- *`o_overflow` rose*, or *`READY` stayed low for 50 ms*: `READY` is ignored or inverted, or the queue never empties.
- *Status byte does not start with 01*: `MISO` is stuck, or the status bits are in the wrong order.

## 8. The Arty top and the pins

The SPI pins go on **Pmod JB**. Pins 1 to 4 follow Digilent's SPI Pmod order (`CS`, `MOSI`, `MISO`, `SCK`), and `READY` goes on pin 7. `VSYNC` stays where Chapter 4.5 put it, on Pmod JA pin 4. The pins are the ones the Chapter 3.1 `.xdc` already lists as `jb[0]` to `jb[4]`:

| Port | Pmod pin | FPGA pin | Direction | Notes |
| --- | --- | --- | --- | --- |
| `spi_cs` | JB1 | W14 | in | active low, pulled up |
| `spi_mosi` | JB2 | Y14 | in | |
| `spi_miso` | JB3 | T11 | out | |
| `spi_sck` | JB4 | T10 | in | |
| `spi_ready` | JB7 | V16 | out | high = you may send |
| `vsync` | JA4 | Y17 | out | unchanged from Chapter 4.5 |

JB pins 5 and 11 are ground, 6 and 12 are 3.3 V. All pins are 3.3 V logic. A 5 V MCU must not drive `SCK`, `MOSI` or `CS` directly: put a level shifter or resistor divider on those three lines. `MISO` is driven even while `CS` is high, so the GPU must be the only device on its SPI bus.

Add to `boards/arty-z7-20/arty-z7-20.xdc`:

```
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports {spi_cs}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33} [get_ports {spi_mosi}]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports {spi_miso}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {spi_sck}]
set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports {spi_ready}]
```

The pull-up on `spi_cs` keeps the slave idle when nothing is plugged in.

Copy `top_artyz7.v` from 4.8, add the five ports, replace `uart_gpu` with `gpu_link`, and synchronize `fb_scanout`'s `o_vblank` (the net that already drives `vsync`) into `clk_sys`:

```verilog
wire vblank_sys;
synchronizer u_sv (.i_clk(clk_sys), .i_async(vsync), .o_sync(vblank_sys));

gpu_link #(.CLK_HZ(50_000_000), .BAUD(BAUD)) u_gpu (
    .i_clk(clk_sys), .i_rst(rst_sys | btn_rst),
    .i_rx(rst_sys | uart_rx), .o_tx(uart_tx),
    .i_sck(spi_sck), .i_mosi(spi_mosi), .i_cs_n(spi_cs), .o_miso(spi_miso),
    .i_vblank(vblank_sys),
    .o_ready(ready), .o_overflow(overflow), /* the rest as before */ );
assign uart_cts  = ~ready;
assign spi_ready = ready;
```

Add `spi_slave.v` and `gpu_link.v` to `SRCS`.

**Build.** `source ~/opt/openxc7/export.sh`, then `make` in `projects/5.1-spi/`. The reference build (yosys 30 s, place and route 50 s):

| Resource | Used | Of |
| --- | --- | --- |
| LUTs | about 6,200 | 106,400 |
| Flip-flops | about 1,600 | 106,400 |
| RAMB36E1 | 46 | 140 |
| DSP48E1 | 20 | 220 |

`clk_sys` closes at about 67 MHz against 50 required, `clk_pix` at about 140 MHz against 25. The SPI slave, the reply queue and the status logic are a small addition to Chapter 4.8's 6,000 LUTs, and the block RAM count does not change. The SPI pins have no clock constraint and nextpnr does not time them; the synchronizers make that safe.

## 9. On the board

You need a USB-SPI bridge. An **FT232H** breakout (Adafruit's is common) works with `pyftdi`, and is what `host/spi_test.py` uses. An MCU works too: any SPI peripheral in mode 0, MSB first, at 1 MHz to start. Install the library once with `.venv/bin/pip install pyftdi`. On Linux, opening the USB device needs a udev rule or `sudo`.

Wire it with short wires (under 15 cm), ground first:

| FT232H | Signal | Arty |
| --- | --- | --- |
| D0 | `SCK` | JB4 (T10) |
| D1 | `MOSI` | JB2 (Y14) |
| D2 | `MISO` | JB3 (T11) |
| D3 | `CS` | JB1 (W14) |
| D4 | `READY` | JB7 (V16) |
| D5 | `VSYNC` | JA4 (Y17) |
| GND | | JB5 or JB11 |

Program the board (`make prog`), then:

```sh
.venv/bin/python host/spi_test.py --model             # the host code against a Python stand-in: PASS
.venv/bin/python host/spi_test.py --freq 1000000      # on hardware
```

The script clears the screen, draws rectangles, lines and a triangle, and reads `FB_CRC` after each step. It compares each CRC with `gpu_model.py` (Chapter 4.8's, unchanged), prints the status byte, and counts `VSYNC` edges for 0.1 s, about 12 at 60 Hz. Expect `PASS: spi_test`. Then try 2, 4 and 5 MHz. If `FB_CRC` goes wrong only at higher rates, you have reached the `MISO` limit of Section 2: lower the rate or shorten the wires.

**On the logic analyzer** (8 channels at 24 MHz or more, probes on the SPI wires and `READY`), capture one `FB_CRC` exchange at 1 MHz and check:

- `CS` falls, then `SCK` stays low for at least 100 ns before the first rising edge.
- Eight rising edges per byte; `MOSI` is stable at each rising edge and changes only after falling ones.
- `MISO` changes only after falling edges. The first byte, `0x41` (`01000001`), is on `MISO` before the first rising edge: first bit 0, then 1.
- The read frame starts with `FF` on `MOSI`; `MISO` carries `45 04` and then the CRC from the script's output, least significant byte first.
- `READY` is high at the start. To see it fall, send four `CLEAR`s and then 300 bytes of `NOP`s in one frame without looking at `READY`: it drops while the engine is busy and comes back when it catches up.
- Raise the rate and watch `MISO` change 40 to 60 ns after each falling edge: at 6 MHz that is most of the half period.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`):

- **`spi_slave`** with the ports of Section 3: `(i_clk, i_sck, i_mosi, i_cs_n, o_miso, o_rx_valid, o_rx_data[7:0], o_frame, i_tx_data[7:0], o_tx_take)`. SPI mode 0, MSB first, frames while `CS` is low; the three pins are synchronized in `i_clk`. It must work for `SCK` half-periods down to 80 ns, with jitter, at any phase against `i_clk`, with the master rules of Section 2.
  - `o_rx_valid` pulses once per complete byte, before the next byte's `o_tx_take` and before the end of the 100 ns `CS`-high gap after its frame. A byte cut short by `CS` rising delivers nothing, and the next frame starts at bit 0.
  - `o_tx_take` pulses once for each byte whose first bit was clocked, partial bytes included.
  - `o_frame` is high within 100 ns of `CS` falling and low within 100 ns of `CS` rising.
  - `o_miso`: byte 0 is `i_tx_data` as it stood while `CS` was high (the testbench holds it steady from `CS` rising until the frame's first `o_tx_take`); each later byte is `i_tx_data` as it stands at the end of the previous byte. The testbench changes `i_tx_data` one clock after each `o_tx_take` and then holds it. `o_miso` changes only while `SCK` is low and is stable 10 ns before each rising edge.
- **`gpu_link #(CLK_HZ, BAUD)`** with the ports of `uart_gpu` plus `i_sck, i_mosi, i_cs_n, o_miso, i_vblank` (Section 4). The UART side still meets everything `uart_gpu` had to, except that `o_ready` now follows Section 5. The SPI side follows Sections 4 to 6:
  - A UART byte is dropped while `o_frame` is high. Bytes of a frame whose first byte is `0xFF` never reach the FIFO.
  - Replies go to the link that sent the most recent byte written into the FIFO. The SPI reply queue holds 16 bytes; when it is full the engine waits and nothing is lost.
  - `MISO` bytes as in Section 6's tables: status `01 0 OVERFLOW VBLANK REPLY 0 READY`, count, then (read frames only) reply bytes, each removed from the queue when its first bit is clocked, and `0x00` after them.
  - `o_ready` is high only when at least 257 bytes are free (the reference: 272), and goes high again once the engine has drained the FIFO. `o_overflow` as before.
- In `tb_gpu_spi`, the status byte's `OVERFLOW` and reserved bits are 0 throughout, and `VBLANK` and `READY` must agree with `i_vblank` and `o_ready` whenever those have not changed from 300 ns before `CS` fell to 150 ns after.

**Files from this chapter:** `tb_spi_slave.v`, `tb_link_arb.v` and `tb_gpu_spi.v` to `sim/`, and `spi_test.py` to `host/`. `host/gpu_model.py` is unchanged from Chapter 4.8. The testbenches need Chapter 4.8's `sim/blit_*` files and `rtl/common/fb_test.hex` and `pal_test.hex`.

**`tb_spi_slave`** (about 10 s): the checks of Section 7. Prints `PASS: tb_spi_slave (4000 frames, ...)`. Watchdog 400 ms simulated.

**`tb_link_arb`** (about 30 s): as listed in Section 7. Models two framebuffers on the draw ports and computes its own CRCs. Prints `PASS: tb_link_arb (...)`. Watchdog 400 ms simulated.

**`tb_gpu_spi`** (about 4 minutes): as in Section 7, reading `../../sim/blit_*` and `../../rtl/common/*.hex`. `+half=NS` forces one `SCK` half-period for every frame (for example `vvp -n tb_gpu_spi.vvp +half=80`). It fails if `o_overflow` rises, if `READY` stays low for 50 ms, on any wrong reply byte, framebuffer byte or palette entry, any bad status, count or filler byte, or any throughput window over its limit. Watchdog 2 s simulated.

By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_spi_slave.vvp sim/tb_spi_slave.v
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_link_arb.vvp sim/tb_link_arb.v
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_gpu_spi.vvp sim/tb_gpu_spi.v
cd build/sim && vvp -n tb_spi_slave.vvp && vvp -n tb_link_arb.vvp && vvp -n tb_gpu_spi.vvp
```
