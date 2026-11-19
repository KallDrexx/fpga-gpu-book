# Chapter 0.5 - FIFO and Register Protocol

Chapter 0.4 gave the board a serial link. Right now that link carries bytes that mean nothing; the echo just sends them back. This chapter gives the bytes meaning. You will put a FIFO behind the UART receiver, build a small parser that reads two kinds of packets out of it, `WRITE addr data` and `READ addr`, and connect the parser to a bank of registers. Register 0 drives the LEDs, so the PC can switch them on and off. Register 1 drives the display.

This is the start of the GPU's command port. In Step 2.5 these registers become the tile map, the palette and the scroll offsets. In Phase 4 the parser grows into a command processor, and in Phase 5 an SPI link feeds the same FIFO. The FIFO also introduces the FPGA's **block RAM**, the memory every later video chapter depends on.

By the end of this chapter you will have:

- an understanding of the iCE40's block RAM and how to write Verilog that yosys maps onto it,
- a `fifo` module with full and empty flags and a word count, stored in one block RAM,
- a `reg_parser` module that decodes packets and holds 16 registers,
- a `uart_regs` module that joins `uart_rx`, the FIFO, the parser and `uart_tx`,
- a testbench that fills and drains the FIFO and checks its flags at every count, and a Python script that sets the LEDs and reads back every register it wrote.

## 1. Why a FIFO

A **FIFO** (first in, first out) is a queue: words go in at one end and come out of the other in the same order. You need one wherever a producer and a consumer run at different rates, or at the same average rate but not in step.

Here the producer is `uart_rx`. It delivers a byte every 10 bit times whether or not anyone is ready for it, and it has no way to wait: if nobody takes the byte, it is overwritten by the next one. The consumer is the parser. Most of the time it handles a byte in a clock or two, far faster than bytes arrive. But sometimes it has to wait. A READ needs a reply, and the reply has to wait for the transmitter. Later, a command such as "fill this rectangle" will keep the drawing engine busy for thousands of clocks while more command bytes keep arriving. The FIFO holds those bytes until the consumer catches up.

A FIFO only absorbs bursts. If the consumer is slower *on average*, any FIFO eventually fills, and then the producer must be told to stop. That signal is called **back-pressure**: the `o_full` flag is the simplest form of it. A UART cannot push back on the PC, so for now a byte that arrives at a full FIFO is lost. Phase 5 adds a `READY` pin so the microcontroller waits. For this chapter's two commands the FIFO never gets close to full (Section 6 explains why), but the parser's testbench makes it fill on purpose.

## 2. Block RAM on the iCE40

So far every bit of storage in your designs has been a flip-flop. The HX1K has 1,280 of them, one per logic cell, and they are expensive: a 512-byte buffer built from flip-flops would need 4,096, more than three times the whole chip. For real storage the FPGA has dedicated memory blocks.

The HX1K has **16 block RAMs**, each an `SB_RAM40_4K` cell holding **4 Kbit**, for 64 Kbit (8 KB) in total. Each block has one write port and one read port, each with its own address, and it can be arranged as:

| Arrangement | Words x bits |
| --- | --- |
| widest | 256 x 16 |
| | 512 x 8 |
| | 1,024 x 4 |
| deepest | 2,048 x 2 |

A 512-entry, 8-bit FIFO fills exactly one block. A 16-entry FIFO would also take a whole block, because the block is the unit; there is no saving in making it smaller. A 1,024 x 8 memory takes two blocks, and a 513 x 8 one takes two as well. Keep power-of-two sizes in mind when you budget memory, and remember the total: 8 KB is all there is on this board. Step 2.5 spends 3 KB of it on a tile map and tile graphics.

The most important property of block RAM is that **reading is synchronous**. You present an address before a clock edge, and the data appears after that edge. There is no way to look up a word combinationally and use it in the same clock. That one clock of latency shapes every design that uses block RAM, starting with the FIFO's read side and the parser in Section 5.

### Writing Verilog that becomes block RAM

You do not normally instantiate `SB_RAM40_4K` yourself. You describe a memory as an array and yosys **infers** a block RAM when the description matches what the hardware can do. The pattern is:

```verilog
reg [7:0] mem [0:511];

always @(posedge i_clk) begin
    if (w_we) mem[r_waddr] <= i_wdata;     // synchronous write
    if (w_re) r_rdata  <= mem[r_raddr];    // synchronous, registered read
end
```

The rules that keep yosys happy:

- **Write on a clock edge.** One write port, written in a clocked block.
- **Register the read.** The read must be assigned in a clocked block, as above. The read-enable (`if (w_re)`) is fine, and the block RAM's output register holds the old value while it is low.
- **No reset on the memory or its read register.** Block RAM contents cannot be cleared in one clock, and the output register has no reset input. Give `mem` no reset, and do not reset `r_rdata`.
- **An asynchronous read cannot be a block RAM.** If you write `assign o_rdata = mem[r_raddr];`, yosys has nowhere to put it. Other FPGA families (including the Arty Z7's) can build small memories out of LUTs, but the iCE40 cannot. Yosys then builds the whole memory from flip-flops and multiplexers, and a 512-byte FIFO no longer fits on the chip.
- **Do not read and write the same address on the same edge** and depend on the result. Which value you get is not something to rely on. A FIFO never does this: when it is empty it does not read, and when it is full it does not write.

Initial contents (from `$readmemh`) are allowed and come in Chapter 2.2.

To check that the RAM was inferred, read the report. nextpnr's utilization summary has an `ICESTORM_RAM` line; after this chapter it should say `1/16`. If it says `0/16` and the logic-cell count has jumped by thousands, the memory turned into flip-flops. Section 7 has you make this mistake on purpose.

## 3. The FIFO

A FIFO is a block RAM used as a ring. A **write pointer** says where the next word goes; a **read pointer** says where the oldest word is. Each write stores a word at the write pointer and advances it; each read takes the word at the read pointer and advances it. Both wrap around from `DEPTH - 1` to 0. If `DEPTH` is a power of two the wrap is free: a pointer of `$clog2(DEPTH)` bits simply overflows.

```
          rd_ptr            wr_ptr
            v                 v
   +----+----+----+----+----+----+----+----+
   |    | w0 | w1 | w2 | w3 |    |    |    |      DEPTH = 8, count = 4
   +----+----+----+----+----+----+----+----+
```

### Full, empty and the count

When the pointers are equal, the FIFO is either empty or full. The pointers alone cannot tell you which. There are two standard fixes:

- **Keep a count.** A register from 0 to `DEPTH`, plus one on a write, minus one on a read, unchanged when both or neither happen. Empty is `count == 0`, full is `count == DEPTH`. Note the width: it must hold `DEPTH` itself, so it is `$clog2(DEPTH) + 1` bits.
- **Give each pointer one extra bit.** The low bits address the RAM; the top bit flips on every wrap. Equal pointers mean empty; pointers equal except for the top bit mean full.

This book's FIFO exports the count as `o_count` (it is handy for debugging, and Phase 5's `READY` signal is a comparison on it), so the first method is the obvious one. Either works internally.

### The rules

The interface has a write side (`i_wr_en`, `i_wr_data`), a read side (`i_rd_en`, `o_rd_data`) and the status outputs `o_empty`, `o_full` and `o_count`. The behaviour must be exact, because both testbenches check it edge by edge:

- **A write is accepted** on a rising edge where `i_wr_en = 1` and `o_full = 0`. A write to a full FIFO is ignored.
- **A read is accepted** on a rising edge where `i_rd_en = 1` and `o_empty = 0`. A read from an empty FIFO is ignored.
- **Both decisions use the flags as they were before the edge.** So when the FIFO is full, a read and a write on the same edge give a read only: the count drops to `DEPTH - 1`. When it is empty, they give a write only. In between, both happen and the count does not change.
- **Read data comes after the edge.** After an accepted read, `o_rd_data` holds the word that was read, and it keeps that value until the next accepted read. This follows directly from the block RAM: the reading edge is the edge that loads the RAM's output register.

```
 clk        _|‾|_|‾|_|‾|_|‾|_|‾|_
 i_rd_en    ____|‾‾‾‾‾‾‾|________     two reads accepted
 o_rd_data  ==== ?  |  w0 |  w1 ====  each word appears after its reading edge
 o_count    ===  2  |  1  |  0  ====
 o_empty    ____________|‾‾‾‾‾‾‾‾‾
```

The same rule in code is the whole design, nearly. Compute `w_wr = i_wr_en && !o_full` and `w_rd = i_rd_en && !o_empty`, use `w_wr` for the RAM write and the write pointer, `w_rd` for the RAM read and the read pointer, and both for the count. Make `o_empty` and `o_full` either combinational comparisons on the count or registers updated alongside it; both pass the tests.

The other common style is the **first-word-fall-through** (FWFT, or "show-ahead") FIFO, where `o_rd_data` already shows the oldest word while `o_empty` is 0, and `i_rd_en` means "I have taken it". It is convenient for the consumer but needs an extra register stage in front of the block RAM. This book uses the plain style above, so the consumer must deal with the one-clock latency itself, which is a habit worth building now.

Write `fifo` with parameters `WIDTH` (default 8) and `DEPTH` (default 512, a power of two). Give the pointers and count initial values of 0, and leave `mem` and `o_rd_data` without initial values or resets.

### Testing it

`tb_fifo` is this step's "Done when" test for the FIFO. It runs three FIFOs side by side, (`WIDTH`, `DEPTH`) = (8, 16), (8, 512) and (16, 32), and checks every output against a model after every rising edge. It fills each one a word at a time and drains it, which visits every count from 0 to `DEPTH` and back, so the flags are checked at every count. It writes while full and reads while empty, does simultaneous reads and writes at empty, at full and in the middle, and then runs thousands of random cycles that wrap the pointers many times over.

The bugs it is good at finding are the classic ones: `o_full` one word early (comparing with `DEPTH - 1`), a read pointer that advances on a read from an empty FIFO, a count that goes up on a simultaneous read and write, and a count register one bit too narrow, which makes "full" look like "empty".

## 4. The register protocol

The PC talks to the board in **packets**. Each one starts with an opcode byte that says what it is and how many bytes follow:

| Packet | Bytes | Reply |
| --- | --- | --- |
| WRITE | `0x57` (`'W'`), addr, data | none |
| READ | `0x52` (`'R'`), addr | one byte: the register's value |

Behind the parser is a bank of 16 eight-bit registers:

| Address | Register |
| --- | --- |
| `0x00` | LEDs: bit 0 is LED 1, ..., bit 3 is LED 4 (bits 7:4 are stored but unused) |
| `0x01` | the 7-segment display, as two hex digits |
| `0x02` to `0x0F` | general-purpose, read/write |
| `0x10` to `0xFF` | not present: writes are ignored, reads return `00` |

All 16 registers are `00` at power-up. A byte that arrives where an opcode is expected and is neither `'W'` nor `'R'` is skipped.

A few design decisions are hidden in this small table:

- **Binary, fixed-length packets.** Text commands (`"W 00 0F\n"`) are easy to type but cost a parser for hex digits and whitespace, and take three times as many bytes. Binary packets whose length is set by the opcode are trivial to parse. The opcodes are printable letters anyway, so they are easy to spot in a hex dump or on a logic analyzer.
- **No reply to WRITE.** Writes are the common case and should go as fast as the link allows. If the PC needs to know that its writes have happened, it sends a READ: the parser handles packets strictly in order, so when the reply comes back, every earlier write has already taken effect. That is how the host script uses it.
- **Unused addresses do not alias.** Decoding only `addr[3:0]` would be smaller, but then address `0x10` would quietly overwrite register 0. A missing register should be missing. Both testbenches write to `0x10`, `0x1F`, `0xFF` and others and check that registers 0 to 15 are untouched.
- **Weak resynchronization.** Skipping unknown bytes lets the parser recover from junk between packets. It cannot recover from a byte lost *inside* a packet: the parser then reads the next opcode as data and stays one byte out of step until it happens to realign. Over a USB serial link with a FIFO in front, lost bytes do not happen. Step 5.2 adds a real recovery rule (raising SPI chip-select resets the parser) when the link gets less forgiving.

## 5. The parser

The parser is a state machine of the Chapter 0.4 kind. It waits for an opcode, then collects the bytes that follow:

```
                      'W'                 addr              data
            +------+ ----> +---------+ --------> +---------+ ---------> back to OP
  other --> |  OP  |       | W_ADDR  |           | W_DATA  |  (write the register)
  (skip)    +------+ ----> +---------+           +---------+
              ^       'R'  +---------+   addr    +---------+
              |            | R_ADDR  | --------> |  REPLY  | -- transmitter accepts --+
              |            +---------+ (look up) +---------+                          |
              +---------------------------------------------------------------------+
```

Every state except REPLY moves on when it receives a byte. REPLY moves on when the transmitter accepts the reply. Two details need care.

**The FIFO's one-clock latency.** The parser pops a byte by raising `o_rd_en` while `i_empty` is 0. The byte appears on `i_rd_data` *after* that edge. So the parser must remember that it popped (a one-bit register, set on the popping edge) and use the byte on the next clock. The simplest correct scheme alternates: pop, then use, then pop again. That handles a byte every two clocks, about a thousand times faster than the UART delivers them (one byte per 2,170 clocks at 115,200 baud). Using `i_rd_data` in the same clock as `o_rd_en` reads the previous byte instead, and the testbench catches that at once.

**Do not pop what you cannot use.** In REPLY the parser cannot accept a new byte; it is still waiting for the transmitter. If it pops anyway, the byte is gone. So `o_rd_en` must be 0 in REPLY, and also on the clock where the byte just popped is about to send the parser into REPLY. The alternating scheme takes care of that second case automatically: the byte that leads to REPLY is used on a clock when nothing is being popped.

**Talking to the transmitter.** `uart_tx` accepts a byte on an edge where `i_valid = 1` and `o_busy = 0`, and raises `o_busy` at that same edge. In REPLY, the parser waits for `i_tx_busy` to be 0, presents the reply with `o_tx_valid = 1` for a clock, and returns to OP. A valid that is ignored because the transmitter is busy is not a reply sent, so the parser must keep the reply until it is truly accepted. A parser that fires `o_tx_valid` once and moves on works most of the time and loses a reply the first time the transmitter is still busy.

**The registers** are 16 eight-bit flip-flop registers, 128 flip-flops in all, with a 16-way multiplexer to read them. They could live in a block RAM, but the LEDs and display need registers 0 and 1 on wires all the time, and a block RAM only shows one word at a time. 16 bytes of flip-flops is affordable; Step 2.5's 2 KB tile map is not, and goes into block RAM. Capture the reply value when the READ's address byte arrives (`00` for addresses `0x10` and up) in a reply register `o_tx_data`.

### Testing the parser

At the system level, nothing in this chapter can make the transmitter the bottleneck: a READ is 2 bytes in and 1 byte out, so a reply always finishes before the next READ has even arrived. A parser that drops replies when the transmitter is busy would pass every end-to-end test. So `tb_reg_parser` tests it on its own. It connects your `reg_parser` to your `fifo` (16 deep) and plays both UART halves itself. It pushes bytes into the FIFO as fast as the FIFO accepts them, and plays a transmitter that, after accepting a reply, stays busy for a random 2 to 200 clocks in some phases and 3,000 clocks in others. With a slow transmitter, the parser sits in REPLY, the FIFO fills to full behind it, and any lost byte or lost reply shows up as a wrong reply or a missing one. The testbench also checks that `o_reg0` and `o_reg1` step through exactly the values written to registers 0 and 1, in order.

## 6. Putting it together

`uart_regs` is wiring:

```
 i_rx --> uart_rx --(o_valid, o_data)--> fifo --(rd)--> reg_parser --(tx_valid, tx_data)--> uart_tx --> o_tx
                    wr_en, wr_data       512 x 8         |    ^  busy                          |
                                                         |    +--------------------------------+
                                                         +--> o_reg0, o_reg1
```

`uart_rx`'s one-clock `o_valid` pulse is the FIFO's write enable and its `o_data` the write data. Use `fifo #(.WIDTH(8), .DEPTH(512))`, one block RAM. Leave the FIFO's `o_full` unconnected for now, or better, think about what you would do with it: in Phase 4 it becomes a status bit the host can read.

Why have a FIFO at all, if the parser is faster than the line? Because "the parser is faster than the line" is true only of this chapter's two commands. The moment a command takes longer than a byte time to execute (in Phase 4, clearing the framebuffer takes 1.5 ms, about 17 byte times at 115,200 baud), the bytes behind it must wait somewhere. The plumbing is built now, while it is simple to test.

`tb_uart_regs` checks the assembled design over the serial line at 16 clocks per bit, as `tb_uart_echo` did. It reads every register at power-up, writes them all and reads them back, writes out-of-range addresses, uses `'W'` and `'R'` as addresses and data, sends junk between packets, and finishes with long back-to-back bursts of random packets. It also checks that `o_reg0` and `o_reg1` show each new value within one bit time after the WRITE's last byte.

## 7. The board design

Make `projects/0.5-uart-regs/` with a `Makefile` and a `top_goboard.v`. Put `fifo.v`, `reg_parser.v` and `uart_regs.v` in `rtl/common/` and list them in `SRCS` with `uart_rx.v`, `uart_tx.v`, `synchronizer.v` and `hex_to_7seg.v`. Set:

```make
TESTS := tb_fifo tb_reg_parser tb_uart_regs
```

`top_goboard` takes a `CLK_HZ` parameter (default 25,000,000) and is only wiring:

- `i_UART_RX` (pin 73) into a `uart_regs` with `BAUD = 115_200`, and its `o_tx` out on `o_UART_TX` (pin 74),
- `o_reg0[0]` to `o_LED_1` (pin 56), `o_reg0[1]` to `o_LED_2` (57), `o_reg0[2]` to `o_LED_3` (59) and `o_reg0[3]` to `o_LED_4` (60),
- `o_reg1[7:4]` to digit 1 and `o_reg1[3:0]` to digit 2 through two `hex_to_7seg` decoders, segments inverted, on the pins from Chapter 0.2.

**Read the report.** Look first for `ICESTORM_RAM 1/16`: that is your FIFO. Expect roughly 250 flip-flops (the 16 registers are half of them, the two UARTs most of the rest) and a few hundred logic cells, about a third of the chip. `SB_IO` should be 21: the clock, two UART pins, four LEDs and 14 segments.

**Break the inference on purpose.** Change the FIFO's read to an asynchronous one: drive `o_rd_data` from `mem[r_rd_ptr]` with an `assign` (the testbench timing changes too, so do not run `make test` on it). Rebuild and read the report. `ICESTORM_RAM` drops to 0, and nextpnr either fails because the design does not fit in 1,280 cells or reports a cell count several times larger than before. Change it back, and keep this failure in mind: whenever a video chapter's resource report looks wrong, check `ICESTORM_RAM` first.

## 8. Bringing it up on the board

1. From the repository root, run `make test`. The three new testbenches must print `PASS`, along with every earlier one.
2. In `projects/0.5-uart-regs/`, run `make clean prog`. The display shows `00` and all four LEDs are off. Check `ICESTORM_RAM 1/16` in the report.
3. **Poke a register by hand.** Binary bytes are awkward to type into a terminal, so use Python. With the serial port from Chapter 0.4 (often `/dev/ttyUSB1` on Linux):

   ```sh
   python3 -c "import serial; s = serial.Serial('/dev/ttyUSB1', 115200, timeout=1); \
   s.write(b'W\x00\x0f' b'W\x01\x42' b'R\x00'); print(s.read(1).hex())"
   ```

   All four LEDs light, the display shows `42`, and the command prints `0f`, register 0 read back. Try `b'W\x10\x00'` and see that nothing changes.
4. **The register test.** Copy `reg_test.py` from this chapter to `host/reg_test.py` and run:

   ```sh
   python3 host/reg_test.py /dev/ttyUSB1
   ```

   A light walks across the four LEDs and back, then the script writes random values to all 16 registers fifty times, reading every one back, checks the out-of-range address, and prints one line. On `PASS`, LEDs 1 and 3 are lit and the display shows `5A`. Use `--leds` and `--display` to leave other values, for example `--leds 0xf`.

If the script reports "sent 1 READ(s), got 0 reply byte(s) back", the parser never replied. Check that the transmitter is wired and that `uart_tx` sees `o_tx_valid`, then rerun `tb_reg_parser`. If replies come back but hold values that belong to other registers or other packets, the parser is probably using `i_rd_data` one clock too early, and `tb_reg_parser` will show it. If the LEDs work but read-back fails only for registers 0 and 1, look at how `o_reg0` and `o_reg1` are taken from the register file.

**Done when:** `make test` prints PASS for every testbench, including `tb_fifo` filling and draining the FIFO with the flags checked at every count, and `reg_test.py` sets the LEDs and reads back every register it wrote.

## Testbench requirements

**Modules you provide**, each in a file named after the module under `rtl/` (for example `rtl/common/`), with outputs either `reg` or `wire`:

```verilog
module fifo #(
    parameter integer WIDTH = 8,
    parameter integer DEPTH = 512                // a power of two
) (
    input  wire                   i_clk,
    input  wire                   i_wr_en,
    input  wire [WIDTH-1:0]       i_wr_data,
    input  wire                   i_rd_en,
    output reg  [WIDTH-1:0]       o_rd_data,
    output wire                   o_empty,
    output wire                   o_full,
    output reg  [$clog2(DEPTH):0] o_count
);

module reg_parser (
    input  wire       i_clk,
    input  wire       i_empty,      // from the FIFO
    input  wire [7:0] i_rd_data,
    output wire       o_rd_en,
    input  wire       i_tx_busy,    // to and from uart_tx
    output reg        o_tx_valid,
    output reg  [7:0] o_tx_data,
    output wire [7:0] o_reg0,       // register 0 (LEDs)
    output wire [7:0] o_reg1        // register 1 (display)
);

module uart_regs #(
    parameter integer CLK_HZ = 25_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire       i_clk,
    input  wire       i_rx,
    output wire       o_tx,
    output wire [7:0] o_reg0,
    output wire [7:0] o_reg1
);
```

- `fifo`: empty at time zero. Write accepted on an edge with `i_wr_en = 1` and `o_full = 0`, read on an edge with `i_rd_en = 1` and `o_empty = 0`, both judged by the flags before the edge. `o_count` = words held, `o_empty` exactly when it is 0, `o_full` exactly when it is `DEPTH`; never X. After an accepted read, `o_rd_data` shows the word read and holds it until the next read. Tested at (`WIDTH`, `DEPTH`) = (8, 16), (8, 512), (16, 32).
- `reg_parser`: protocol and register map of Section 4, registers `00` at power-up, `o_tx_valid = 0` at time zero. Pops with `o_rd_en` and uses `i_rd_data` after the popping edge (tested behind your `fifo #(8, 16)`). A reply is sent only on an edge with `o_tx_valid = 1` and `i_tx_busy = 0`; the testbench then holds `i_tx_busy` high for 1 to 3,000 clocks. Exactly one reply per READ, in order; no byte may be lost while waiting.
- `uart_regs`: `uart_rx` into `fifo #(8, 512)` into `reg_parser` into `uart_tx`. `o_tx = 1` and `o_reg0 = o_reg1 = 00` at time zero; `o_reg0`/`o_reg1` show a written value within one bit time after the end of the WRITE's last stop bit. Tested at (`CLK_HZ`, `BAUD`) = (1,600,000, 100,000).
- `uart_rx` and `uart_tx` from Chapter 0.4 and `synchronizer` from Chapter 0.3 must also be in `rtl/`.

**Testbenches:** copy `tb_fifo.v`, `tb_reg_parser.v` and `tb_uart_regs.v` from this chapter to `sim/` (top modules named after their files). **Host script:** copy `reg_test.py` to `host/`; it needs Python 3 and pyserial.

**Run them:** from the repository root, `make test`. Without the Makefiles:

```sh
mkdir -p build/sim
for tb in tb_fifo tb_reg_parser tb_uart_regs; do
    iverilog -g2012 -Wall -Wno-timescale -o build/sim/$tb.vvp -y rtl/common sim/$tb.v
    (cd build/sim && vvp -n $tb.vvp)              # add +vcd to write <tb>.vcd
done
```

Each prints one line starting with `PASS` or `FAIL`. On the board: `python3 host/reg_test.py <port>` prints `PASS` or `FAIL` the same way (options: `--rounds`, `--leds`, `--display`, `--baud`, `--seed`).
