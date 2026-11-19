# Chapter 4.2 - Command Processor

Chapter 4.1 left the framebuffer's `clk_sys` port free for a drawing engine. This chapter connects that engine to the outside world. The host sends **commands**, small packets of bytes, over the UART. A **command processor** on the FPGA receives them, decodes them, and draws. Every drawing step in the rest of Phase 4 is one more command in this processor, and Phase 5 feeds it from SPI instead of the UART without changing it.

The first five commands are `NOP`, `SET_PALETTE`, `CLEAR`, `PLOT` and `FB_CRC`. `FB_CRC` sends back a checksum of the whole framebuffer. That is how you check real hardware against Python from now on: the host draws something, asks for the CRC, and compares it with the CRC of the same drawing in the Python model.

By the end of this chapter you will have:

- `rtl/common/cmd_engine.v`: the packet parser and the state machine that runs the commands,
- `rtl/common/uart_gpu.v`: `uart_rx` -> `fifo` -> `cmd_engine` -> `uart_tx`, all on `clk_sys`,
- `host/gpu_model.py`: packet encoders and a Python model of the GPU, shared by the host scripts and the testbench reference,
- `sim/tb_uart_gpu.v` passing, against expected results from `sim/py/ref_cmd.py`,
- `projects/4.2-cmd/`: the board, drawing what the host sends, and `host/plot_test.py` showing that its CRC equals Python's.

```
            clk_sys (50 MHz)
 uart_rx ──> uart_rx ──> fifo ─────> cmd_engine ──> draw port ──> fb_scanout (Step 4.1) ──> HDMI
  (pin)                 512 bytes       │    ▲
                         │ count        │    └─ framebuffer read-back (FB_CRC)
            uart_cts <── ~o_ready       ▼
 uart_tx <────────────── uart_tx <── reply bytes
```

## 1. Packets

A UART carries a stream of bytes with no boundaries in it. The first job is to agree where one command ends and the next begins. Every command is a **packet** with the same three parts:

    [opcode] [length] [payload: length bytes]

- **opcode**, one byte, says what to do.
- **length**, one byte, is the number of payload bytes, 0 to 255. It does not count the opcode or itself. `FB_CRC` has length 0, so its whole packet is the two bytes `04 00`.
- **payload** is the command's arguments.

Inside the payload, numbers wider than a byte are **little-endian**: low byte first. Coordinates are **signed 16-bit** (two's complement), even though the screen only needs 0-319. Step 4.3 clips rectangles with off-screen and negative corners, so the format allows them from the start. Colors are one byte, a palette index. Palette colors are three bytes, R then G then B.

The length is what makes the format robust. The parser always reads exactly `length` payload bytes, whether or not it understands the packet. That gives two rules:

- **Unknown opcode:** read the payload and drop the packet. A host written for a later step can talk to this one, and only the new commands do nothing.
- **Wrong length for a known opcode** (a `PLOT` with 4 or 6 bytes, a `CLEAR` with 0): read the payload and drop the packet. Don't guess which bytes were meant.

Either way the parser stays in step with the stream, because it never reads more or less than the length says. `NOP` is the one command that accepts any length. It is useful as padding, and it is a good test that the parser really skips payloads.

What the length can't fix is a lost or extra byte. If a byte goes missing, the parser reads the next opcode as payload, and from then on it is out of step until something resets it. Step 5.2 makes the SPI chip select reset the parser. On the UART, this chapter uses a button: `BTN0` resets the parser.

A length byte limits a packet to 255 payload bytes. Every command up to Step 4.7 fits easily: the largest, `DRAW_TRI`, needs 13. Step 4.8's asset uploads are larger and will be sent as several packets.

### The opcodes

| Opcode | Command | Length | Payload | Step |
| --- | --- | --- | --- | --- |
| `0x00` | `NOP` | any | ignored | 4.2 |
| `0x01` | `SET_PALETTE` | 4 | index, R, G, B | 4.2 |
| `0x02` | `CLEAR` | 1 | color | 4.2 |
| `0x03` | `PLOT` | 5 | x (2 bytes), y (2 bytes), color | 4.2 |
| `0x04` | `FB_CRC` | 0 | none; replies with 4 bytes | 4.2 |
| `0x05`-`0x0A` | planned: `FILL_RECT`, `DRAW_LINE`, `FLIP`, `DRAW_TRI`, `UPLOAD_ASSET`, `BLIT` | | | 4.3-4.8 |
| `0x80`-`0xFF` | never assigned | | | |

The book never uses opcodes `0x80`-`0xFF`, so a test can always use them as "unknown". The planned numbers keep the table in order, but until a step implements one, that opcode is unknown and is skipped.

A few examples, byte by byte:

    01 04  05 FF 80 00        SET_PALETTE: entry 5 = FF8000 (orange)
    02 01  05                 CLEAR to color 5
    03 05  2C 01 0A 00 07     PLOT x = 0x012C = 300, y = 10, color 7
    03 05  FF FF 0A 00 07     PLOT x = -1: off-screen, nothing changes
    04 00                     FB_CRC, reply: 4 bytes
    90 02  12 34              unknown opcode 0x90: skip 2 bytes

## 2. A FIFO between the link and the engine

### Rates

At 115,200 baud a byte takes 10 bit times (start, 8 data, stop), so bytes arrive about every 87 µs. That is 4,340 clocks of `clk_sys`. Most commands take far less: a `PLOT` is done in a few clocks. But `CLEAR` writes all 76,800 pixels at one per clock, 1.5 ms, and `FB_CRC` reads all of them. While the engine is busy, bytes keep coming, and the UART can't be paused: `uart_rx` delivers each byte once, in a one-clock pulse, and then it is gone.

So a **FIFO** sits between the two. `uart_rx` writes every byte into it. The engine reads a byte only when it is ready for one. While a `CLEAR` runs, the engine reads nothing, and the bytes that arrive wait in the FIFO. This is **back-pressure**: a slow stage holds back the stage before it, here simply by not reading. Use Chapter 0.5's `fifo` with `DEPTH=512`. Yosys puts it in one `RAMB18E1`.

One `CLEAR` lasts about 18 byte times at 115,200 baud, so 512 bytes is plenty for occasional slow commands. The FIFO absorbs bursts. It can't fix a host that asks for more work than the engine can do on average. A host sending `CLEAR`s back to back sends one every 260 µs, but each takes 1.5 ms to run. After about 200 of them (54 ms), the FIFO is full. Two ways to handle that:

- **The host paces itself.** It asks for `FB_CRC` (or later another reply) and waits for the answer before sending more. Once the reply arrives, everything before it is finished and the FIFO is empty.
- **Hardware flow control.** Many USB-serial adapters have a CTS (clear to send) input. While CTS is high (it is active low), the adapter stops sending. Wire it to a pin driven by `~o_ready`, where `o_ready` says the FIFO still has room. The board stops the host whenever the FIFO is nearly full.

This chapter's host script needs neither, but the design provides both. `o_ready` is 1 while at least 32 bytes are free. That margin matters because an adapter may finish a byte or two after CTS rises. Step 5.1 brings the same signal out as the SPI link's `READY` pin.

If a byte arrives while the FIFO is full, it is lost. Set a sticky `o_overflow` flag and show it on an LED. With a correct design and a well-behaved host, it never lights.

### Which clock does the UART run on?

The framebuffer port and the engine run on `clk_sys`, 50 MHz. The UART could stay on `clk_pix` as in Step 3.5, with an **asynchronous FIFO** between the two clocks: a FIFO whose write and read ports have separate clocks, with Gray-coded pointers synchronized across. That works, and it is exactly the kind of logic that is easy to get subtly wrong.

The simple, safe choice is to run `uart_rx` and `uart_tx` on `clk_sys` too, with `CLK_HZ = 50_000_000`. The `rx` pin has no clock at all: it is an asynchronous signal, and `uart_rx`'s two-flop `synchronizer` already brings it safely into whatever clock samples it. With the UART on `clk_sys`, the FIFO, the parser, the engine and the reply all share one clock, and the only clock crossing in the whole design remains the dual-port framebuffer and palette RAMs from Step 4.1. Step 5.1's SPI slave does the same thing: it samples `SCK` and `MOSI` with `clk_sys` instead of using `SCK` as a clock, so it writes into the same single-clock FIFO.

## 3. `uart_gpu`

`uart_gpu` holds `uart_rx`, the FIFO, `cmd_engine` and `uart_tx`, and its ports are the draw side of `fb_scanout`:

```verilog
module uart_gpu #(
    parameter integer CLK_HZ = 50_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire        i_clk,         // clk_sys
    input  wire        i_rst,         // synchronous: back to "waiting for an opcode"
    input  wire        i_rx,
    output wire        o_tx,
    output wire        o_ready,       // FIFO has room (CTS)
    output wire        o_overflow,    // sticky: a byte arrived while the FIFO was full
    output wire        o_fb_we,       // to fb_scanout's i_fb_we ...
    output wire [16:0] o_fb_addr,     //   y*320 + x
    output wire [7:0]  o_fb_wdata,
    input  wire [7:0]  i_fb_rdata,    // from o_fb_rdata, one clock after the address
    output wire        o_pal_we,
    output wire [7:0]  o_pal_addr,
    output wire [23:0] o_pal_wdata    // {R, G, B}
);
```

Outputs may be `reg` or `wire`. `i_rst` resets the parser and stops any command in progress. It need not empty the FIFO.

Keep the transport and the engine apart. `cmd_engine` sees only "a FIFO with bytes in it" and "somewhere to send reply bytes". It doesn't know about baud rates, so Step 5.1 can put an SPI slave in front of the same FIFO.

## 4. The engine's state machine

The engine does one thing at a time: fetch a byte, store it, and when a packet is complete, run it. A layout that grows well:

| State | Does |
| --- | --- |
| `FETCH` | if the FIFO isn't empty, drive `rd_en` high (combinationally, from this state) and go to `BYTE` |
| `BYTE` | the byte is on `o_rd_data` now (block-RAM FIFO: one clock later). Store it as opcode, length, or payload byte `n`. When the packet is complete, go to `EXEC`, else back to `FETCH` |
| `EXEC` | check the length for the opcode and start the command, or do nothing |
| `CLEAR` | one write per clock, 76,800 of them |
| `CRC` | one read per clock, 76,800 of them |
| `REPLY` | hand the 4 CRC bytes to `uart_tx`, one at a time |

Two clocks per byte is far faster than any link (one byte per 200 clocks even in the testbench), and it keeps the FIFO handshake obvious. Keep the payload in a small argument buffer, 16 bytes, written at position `n`. Payload bytes past 16 are still counted (so the length is honored) but not stored, since no command needs them.

`EXEC` is the one place that knows the opcodes. Each later step adds a line to its `case` and, if the command takes more than a clock, its own state:

```verilog
S_EXEC: begin
    r_state <= S_FETCH;                    // default: done, next packet
    case (r_op)
        OP_SET_PALETTE: if (r_len == 8'd4) ...  // one palette write
        OP_CLEAR:       if (r_len == 8'd1) ...  // start: address 0, r_state <= S_CLEAR
        OP_PLOT:        if (r_len == 8'd5) ...  // one write, if on screen
        OP_FB_CRC:      if (r_len == 8'd0) ...  // start: r_state <= S_CRC
        default: ;                              // NOP, unknown, wrong length: nothing
    endcase
end
```

Because the engine only fetches in `FETCH`, a long command blocks the FIFO by itself. Commands run strictly in order, so every command sees the effect of all earlier ones, and `FB_CRC` sees exactly the pixels drawn before it. Later steps may overlap work (fetching the next packet while a rectangle fills, for example), but they must keep that ordering guarantee.

### The commands

**`SET_PALETTE index R G B`:** one write, `o_pal_wdata = {R, G, B}`, the same order as Step 4.1's palette and `pal_test.hex`. It changes the color of every pixel with that index on the next frame.

**`CLEAR color`:** writes `color` to all 76,800 addresses, one per clock. Any order works. Aim for 76,800 clocks plus a few: Phase 4's throughput budget assumes one pixel per clock.

**`PLOT x y color`:** writes one pixel at `y*320 + x` if `0 <= x < 320` and `0 <= y < 240`, and **does nothing otherwise**. Compare the full signed 16-bit values. Testing only the low bits plots x = 522 at x = 10, and checking `x <= 320` writes x = 320 into the first pixel of the next row. The multiply by 320 is `(y << 8) + (y << 6)`.

**`FB_CRC`:** reads the framebuffer in address order (row 0 left to right, then row 1, ...) and replies with its CRC-32, low byte first. It is the CRC that Python's `zlib.crc32` and `binascii.crc32` compute:

| | |
| --- | --- |
| polynomial | `0x04C11DB7`, used **reflected**: shift right, XOR with `0xEDB88320` |
| initial value | `0xFFFFFFFF` |
| final XOR | `0xFFFFFFFF` |
| check value | `crc32(b"123456789") = 0xCBF43926` |

Reflected means each byte enters least significant bit first and the register shifts right. One byte at a time:

    crc = crc ^ byte
    repeat 8:  crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)

Write that as a Verilog `function` with a `for` loop. It unrolls into about 32 small XOR trees, one clock per byte, and the reference build runs it at 169 MHz. Start from `0xFFFFFFFF`, feed bytes 0 to 76,799, then send `~crc` as bytes `crc[7:0]`, `crc[15:8]`, `crc[23:16]`, `crc[31:24]`. On the host, `struct.unpack("<I", reply)` gives the number.

The read port has one clock of latency, so the address runs one clock ahead of the data. Keep a "data valid" flag alongside the address, and stop after the last byte has been consumed, not when the last address goes out. The two classic bugs are CRC-ing one stale byte at the start and missing the last one. Both give a CRC that is wrong for every image, so the testbench catches them at once.

The reply goes to `uart_tx` one byte at a time: present the byte with `i_valid` for one clock while `o_busy` is low, then wait for `o_busy` to fall again. The engine may wait for all four bytes (0.35 ms at 115,200) or hand them to a small transmit FIFO and move on. Both are fine.

## 5. The Python model

`host/gpu_model.py` from this chapter is the single definition of the protocol on the PC side. Copy it to `host/`. It has

- packet builders: `nop()`, `set_palette(i, 0xRRGGBB)`, `clear(c)`, `plot(x, y, c)`, `fb_crc()`, and `packet(op, payload)` for anything else,
- `Gpu`, a model with a framebuffer and a palette. `Gpu.feed(bytes)` parses any slice of a byte stream by the rules in Section 1 and returns the reply bytes,
- `crc32()` (zlib) and `crc32_bitwise()`, the per-bit version above, which match.

`sim/py/ref_cmd.py` (copy it there) builds the testbench's stream, runs it through `Gpu`, and writes the expected results. Run it once and commit what it writes:

```sh
.venv/bin/python sim/py/ref_cmd.py sim
```

    ref_cmd: 8547 bytes, 6 FB_CRC replies, 19 packets ignored -> sim/cmd_*.hex

It reads Chapter 4.1's `ref_scanout.py` for the starting image, so the framebuffer starts as the test image, just as it does on the board after `make prog`.

## 6. Simulate: `tb_uart_gpu`

Copy `tb_uart_gpu.v` into `sim/`. The testbench plays both host and framebuffer. It sends the stream into `i_rx` as real serial bytes, through your `uart_rx`, at 2.5 Mbaud: 20 clocks per bit, so a byte takes 200 clocks instead of 4,340. It decodes your `o_tx`. It keeps its own framebuffer and palette on the draw ports, with the same one-clock read as `fb_scanout`, and compares them with `cmd_fb.hex` and `cmd_pal.hex` at the end.

```
$ make test TESTS=tb_uart_gpu
  PASS  tb_uart_gpu
$ cat build/sim/tb_uart_gpu.log
PASS: tb_uart_gpu (8547 bytes sent, 6 FB_CRC replies match, 385028 pixel writes, 6 palette writes, o_ready low 209237 clocks)
```

It takes about 15 seconds. `make sim-tb_uart_gpu` also works but writes a large `.vcd`. The stream runs in this order:

1. `FB_CRC` of the preloaded 4.1 test image. Its pixels all differ, so a traversal in the wrong order fails here, which a CRC of a cleared screen could never show.
2. `NOP`s of length 0, 7, 3 and 200. The payloads contain complete `PLOT` and `CLEAR` packets, which must not run.
3. Five `SET_PALETTE`s, with R, G and B all different.
4. Unknown opcodes (`0x80`, `0xFF`, `0xC3`, `0xEE`), also hiding valid packets.
5. Known opcodes with wrong lengths: `PLOT` with 4 and 6 bytes, `CLEAR` with 0 and 2, `FB_CRC` with 1 (which must not reply), `SET_PALETTE` with 3 and 5.
6. `PLOT`s at the four corners and next to them, then 21 off-screen ones: just past each edge, at -1, at 522 and 276 (which only the low bits would put on screen), and at -32,768 and 32,767.
7. **Back-pressure, no flow control.** A `CLEAR`, followed at once by 464 bytes, sent without looking at `o_ready`. The `CLEAR` lasts about 384 byte times at this baud, so the FIFO holds nearly 400 bytes at its peak. Then an `FB_CRC` with 422 bytes right behind it: the plots after it must not change its answer.
8. **Back-pressure with flow control.** Four `CLEAR`s (300,000 clocks), then 1,000 random plots (one in ten off-screen) mixed with `NOP`s, unknown opcodes, a palette write and an `FB_CRC`, sent as fast as `o_ready` allows. The FIFO fills, `o_ready` drops, and the testbench waits.

At each **fence** the testbench waits for the replies so far. If one is wrong, it computes the CRC of its own copy of the framebuffer, which tells you whether your pixels or your CRC unit are wrong:

    tb_uart_gpu: FB_CRC reply 1 is b79da7a9 (bytes a9 a7 9d b7 in the order sent), expected a9a79db7
    tb_uart_gpu:   the framebuffer is right at this point (its CRC is a9a79db7): check the CRC unit (...)

That one is a reply sent high byte first. Some other symptoms:

- **Every reply wrong, framebuffer right:** a CRC parameter. A non-reflected CRC gives `1963650d` for the first reply, a missing final XOR `56586248`, one byte missing from the end `145994cb`.
- **A reply never comes ("fence 2 waited 20 ms"):** the parser lost its place. At fence 2 (sections 2-6), usually the length is off by one, or a `NOP` or unknown packet's payload is not skipped. At fence 3 (section 7), the engine is probably fetching bytes while `CLEAR` runs and throwing them away.
- **`o_overflow rose`:** bytes arrived with the FIFO full. In section 7 (`honoring o_ready: 0`) the FIFO is too small: it needs about 400 bytes there, so build it with 512. In section 8 (`honoring o_ready: 1`) `o_ready` promises room that isn't there.
- **Palette entry 0 is `563412`, expected `123456`:** the bytes are in the wrong order, B, G, R.
- **First wrong pixel at x=53 y=0, or at an off-screen point's wrapped position:** x and y swapped, or clipping by the low bits.

## 7. The Arty top

`projects/4.2-cmd/top_artyz7.v` is Step 4.1's top with the switch logic replaced by `uart_gpu` on `clk_sys`:

- `uart_gpu #(.CLK_HZ(50_000_000), .BAUD(BAUD))`, with `BAUD` a top parameter (default 115,200), `i_rx` = `rst_sys | uart_rx` so the receiver sees an idle line until the clock is good, and `i_rst` = `rst_sys` or `btn[0]` through a `synchronizer`,
- its draw ports straight to `fb_scanout`, which keeps `FB_FILE` and `PAL_FILE` so the test image appears at power-up,
- `uart_cts = ~o_ready`,
- `led[0]` = `o_locked`, `led[1]` = `o_overflow`, `led[2]` = `~o_ready`.

Add one line to `boards/arty-z7-20/arty-z7-20.xdc`, after `uart_tx`, for Pmod JA pin 3:

    set_property -dict {PACKAGE_PIN Y16 IOSTANDARD LVCMOS33} [get_ports {uart_cts}]

The project's `SRCS` are Step 4.1's plus `uart_rx.v`, `uart_tx.v`, `fifo.v`, `cmd_engine.v` and `uart_gpu.v`. Set `TESTS := tb_uart_gpu`.

## 8. Build and read the report

Run `make` in `projects/4.2-cmd/`. The reference build took 29 seconds:

```
== cmd: resource use (xc7z020clg400-1) ==
  SLICE_LUTX:    1330/ 106400     1%
  SLICE_FFX:     426/ 106400     0%
  CARRY4:      74/  13300     0%
  ...
  BUFGCTRL:       3/     32     9%
  MMCME2_ADV:       1/      4    25%
  RAMB18E1:       1/    280     0%
  RAMB36E1:      19/    140    13%
  Max frequency for clock 'clk_sys': 169.03 MHz (PASS at 50.00 MHz)
  Max frequency for clock 'clk_pix': 118.30 MHz (PASS at 25.00 MHz)
  Max frequency for clock 'clk_sys': 169.03 MHz (PASS at 50.00 MHz)
```

(The report's `tail -n 3` catches the `clk_sys` line twice. The numbers are the same.)

- Compared with Step 4.1 (852 LUTs, 192 flip-flops), the command processor costs about 480 LUTs and 230 flip-flops, a fraction of a percent of the chip.
- **`RAMB18E1` 1** is the 512-byte FIFO. In `build/yosys.log` it shows as `mapping memory top_artyz7.u_gpu.u_fifo.mem via $__XILINX_BLOCKRAM_TDP_`, next to Step 4.1's lines for the framebuffer (`BLOCKRAM_TDP`) and the palette (`LUTRAM_SDP`: the palette is still in LUTs, not block RAM).
- **`clk_sys` reaches 169 MHz.** The CRC's 8-bit loop isn't the slowest path. In the reference build, the critical path is the compare of the 17-bit draw address with 76,799 that ends `CLEAR` and `FB_CRC`, driving many clock enables.

## 9. On the board

Wire the USB-serial adapter as in Step 3.5: adapter TXD to JA pin 1, RXD to JA pin 2, GND to JA pin 5. If your adapter has a CTS input, you can connect it to JA pin 3, but this test doesn't need it. Connect the monitor and run `make prog`. Then:

1. The Chapter 4.1 test image appears and LD0 lights.
2. Run the host script (copy `host/plot_test.py` next to `gpu_model.py`). With `--seed 7` it should print the lines below. The CRCs and the byte count are what `gpu_model` computes for that seed, so your hardware must match them exactly; the time is roughly what 7,711 bytes take at 115,200 baud (0.67 s), and yours will differ a little:

    ```
    $ .venv/bin/python host/plot_test.py /dev/ttyUSB0 --seed 7
    framebuffer CRC before the test: a9a79db7 = Chapter 4.1's test image, untouched since power-up
    sent 7711 bytes (1000 pixels on screen, 100 off screen, seed 7) in 0.68 s
    hardware CRC 1fd9969a, Python CRC 1fd9969a
    PASS: plot_test
    ```

    It first checks the CRC of the untouched test image, which tests your CRC unit on its own. Then it sets palette entry 0 to dark blue, clears, plots 1,000 random pixels plus 100 off-screen ones, and compares the final CRC with `gpu_model`'s. Without `--seed` it picks a new drawing each run and prints the seed, so a failing run can be repeated.
3. The screen is dark blue with 1,000 colored dots. LD1 (overflow) stays dark.
4. Run it again. The first CRC is now the previous run's drawing, not the test image, and the new run passes too.
5. If there's no reply at all, check the wiring and the baud rate, then press BTN0 (it resets the parser) and run the script again. If the CRCs differ, `tb_uart_gpu` should have failed too. Run it.

**Done when:** `tb_uart_gpu` passes, and `plot_test.py` reports the hardware CRC equal to the Python CRC after 1,000 random pixels.

## Testbench requirements

**Modules you provide** (in `rtl/common/`, found by `-y rtl/common`):

- `uart_gpu #(CLK_HZ, BAUD)`, with the ports in Section 3. The testbench sets `CLK_HZ = 50_000_000`, `BAUD = 2_500_000`.
  - `i_rx`/`o_tx`: 8N1 at `BAUD`, idle high. The testbench drives `i_rst` high for the first 10 clocks, then low.
  - Packets `[opcode][length][payload]` as in Section 1: little-endian signed 16-bit coordinates. Opcodes `0x00` `NOP` (any length), `0x01` `SET_PALETTE` (4: index, R, G, B, written as `{R,G,B}`), `0x02` `CLEAR` (1), `0x03` `PLOT` (5: x, y, color; off-screen does nothing), `0x04` `FB_CRC` (0). Unknown opcodes and wrong lengths: payload skipped, nothing happens, no reply.
  - `FB_CRC` replies with 4 bytes, low byte first: zlib's CRC-32 of the 76,800 bytes read at addresses 0-76,799 in order. Commands run in order, and each `FB_CRC` covers exactly the commands before it.
  - Draw ports: `o_fb_we` writes `o_fb_wdata` at `o_fb_addr` (must be below 76,800) on a rising edge. `i_fb_rdata` is the byte at the address presented before an edge, after that edge (write-first). `o_pal_we` writes `o_pal_wdata` to entry `o_pal_addr`. Neither write enable may be X after reset.
  - A FIFO between the receiver and the engine: Chapter 0.5's `fifo` with its default `DEPTH=512`. In section 7 the testbench sends 464 bytes right behind a `CLEAR` and 422 right behind an `FB_CRC`, without looking at `o_ready`. About 390 of them wait in the FIFO at once (384 arrive during each 76,800-clock command), and none may be lost.
  - `o_ready`: when 1, the FIFO has room for at least 2 more bytes (the reference uses 32). It must be 1 whenever the FIFO is less than half full. The testbench checks it before starting each byte in flow-controlled sections.
  - `o_overflow` must stay 0 throughout (tie it to 0 if you don't detect overflow).
- `uart_rx`, `uart_tx`, `fifo` from Chapters 0.4/0.5, and `synchronizer`, unchanged.

**Files from this chapter:** `tb_uart_gpu.v` to `sim/`, `ref_cmd.py` to `sim/py/`, `gpu_model.py` and `plot_test.py` to `host/`. Chapter 4.1's `sim/py/ref_scanout.py`, `rtl/common/fb_test.hex` and `pal_test.hex`. Run `.venv/bin/python sim/py/ref_cmd.py sim` once to write `sim/cmd_stream.hex`, `cmd_replies.hex`, `cmd_fb.hex` and `cmd_pal.hex`, and commit them.

**Testbench:** `sim/tb_uart_gpu.v`, top module `tb_uart_gpu`, run from `build/sim/` by `make test` (about 15 seconds). It reads `../../rtl/common/fb_test.hex`, `pal_test.hex` and `../../sim/cmd_*.hex`. 50 MHz clock. It checks every reply byte, all 76,800 framebuffer bytes and all 256 palette entries, `o_overflow`, out-of-range and X writes, and prints one `PASS` or `FAIL` line. Watchdog: 200 ms simulated in all, and a fence gives up after waiting 20 ms for a reply. By hand:

```sh
iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/sim/tb_uart_gpu.vvp sim/tb_uart_gpu.v
cd build/sim && vvp -n tb_uart_gpu.vvp
```
