# Chapter 0.4 - UART Transmit and Receive

Until now the board could only talk to you through LEDs and a two-digit display. This chapter gives it a real conversation partner: your PC. A UART (universal asynchronous receiver-transmitter) is the oldest and simplest serial link there is, and the Go Board's USB chip already presents one to the PC as a serial port. You will build both halves, a transmitter and a receiver, and join them into an echo: every byte the PC sends comes straight back, and the display shows the last one. In Chapter 0.5 the same receiver feeds a command parser, and by Phase 4 the "command port" of your GPU is this link.

By the end of this chapter you will have:

- a way of thinking about designs as **state machines**, which you will use in almost every later chapter,
- a `uart_tx` module that sends one byte as a serial frame,
- a `uart_rx` module that finds frames on an asynchronous line and samples them at mid-bit,
- a `uart_echo` module that joins them and shows the last byte received,
- a loopback testbench that passes for all 256 byte values, and a pyserial script that sends 10,000 random bytes and gets every one back.

## 1. The UART frame

A UART sends bytes over one wire per direction, with no clock wire. The two ends agree in advance on a **baud rate**, the number of bit times per second, and each one times the bits with its own clock. This book uses the most common setting, **115,200 baud, 8N1**: 8 data bits, no parity bit, 1 stop bit.

When nothing is being sent the line **idles high**. A byte goes out as a frame of 10 bits, each lasting exactly one bit time:

```
 idle  start  d0   d1   d2   d3   d4   d5   d6   d7   stop  idle
 ‾‾‾‾‾|____|‾‾‾‾|____|‾‾‾‾|____|____|____|‾‾‾‾|____|‾‾‾‾‾‾‾‾‾‾‾‾‾
        0     1    0    1    0    0    0    1    0     1
                       byte 0x45 ('E'), sent LSB first
```

- **The start bit** is always 0. The falling edge from idle to start is the only timing reference the receiver gets; it marks "a frame begins now".
- **Eight data bits** follow, **least significant bit first**. Sending `0x45` (binary `0100_0101`) puts `1, 0, 1, 0, 0, 0, 1, 0` on the line, in that order.
- **The stop bit** is always 1. It guarantees the line is high again before the next start bit, so every frame begins with a fresh falling edge. A receiver that finds a 0 where the stop bit should be has a **framing error**: it lost track of the bits, or the other end is sending at a different speed, or the line is held low ("break").

The next frame may start right after the stop bit, with no idle time at all. A PC sending a buffer does exactly that, so both your transmitter and receiver must handle frames back-to-back.

Ten bits carry eight bits of data, so 115,200 baud moves 11,520 bytes per second. That is plenty for commands, and in Chapter 2.5 it is enough to scroll a tile map at 60 frames per second, because you send only a couple of bytes per frame.

## 2. Baud timing

Your design measures bit times by counting clocks. At 25 MHz,

  CLKS_PER_BIT = CLK_HZ / BAUD = 25,000,000 / 115,200 = 217.01

so a bit lasts **217 clocks**. Integer division rounds that down, and the resulting bit rate is 115,207 baud, 0.006% fast. That is far below anything that matters, but it is worth knowing how much error a UART can stand, because you will meet clocks where the division is less kind.

The receiver resynchronizes on every start edge, then counts on its own clock for the rest of the frame. If the two ends disagree by a fraction *e*, the error piles up bit after bit. The last sample, in the middle of the stop bit, is taken 9.5 bit times after the start edge; it lands outside the stop bit once 9.5 × *e* reaches half a bit. That allows about 5% in total, shared between the two ends and eaten into by the receiver's uncertainty about exactly when the start edge happened. In practice, keep each end within about 2%. Crystal oscillators are within 0.01%, and USB serial adapters typically within a fraction of a percent, so the real budget goes on rounding: at 25 MHz even 1,000,000 baud (25 clocks per bit, exact) or 230,400 baud (108.5, rounded to 108, 0.5% off) would work.

As in every earlier chapter, compute the count from parameters (`CLK_HZ` and `BAUD`) inside the module and derive the counter width with `$clog2`. The testbenches run your modules at 217 clocks per bit, the real setting, and also at 13 and 16, small values that keep simulations short and make off-by-one mistakes impossible to miss. Thirteen is odd, so "half a bit" is not a whole number of clocks: 6 and 7 are both fine, and the tests accept either.

## 3. State machines

The blinker, the counter and the debouncer each did one thing over and over. A UART does a sequence: wait for work, send a start bit, send eight data bits, send a stop bit, go back to waiting. The standard way to build a sequence in hardware is a **finite state machine** (FSM): a register holding which step you are in, and logic that decides, every clock, what to do in that step and whether to move to another.

Draw it before you write it. For the transmitter:

```
            i_valid
   +------+ (latch byte)  +-------+  CPB clocks  +------+ 8 x CPB clocks +------+
   | IDLE |-------------->| START |------------->| DATA |--------------->| STOP |
   +------+               +-------+              +------+                +------+
      ^                                                                     |
      +---------------------------- CPB clocks -----------------------------+
```

In Verilog, give each state a name with `localparam`, keep the current state in a register, and describe everything in one clocked block with a `case` on the state:

```verilog
localparam [1:0] S_IDLE = 2'd0, S_START = 2'd1, S_DATA = 2'd2, S_STOP = 2'd3;
reg [1:0] r_state = S_IDLE;

always @(posedge i_clk) begin
    case (r_state)
        S_IDLE:  if (/* condition */) r_state <= S_START;
        S_START: ...
        default: r_state <= S_IDLE;      // unreachable states go home
    endcase
end
```

A few habits make state machines easy to get right:

- **One clocked block, nonblocking assignments.** The state register and the registers it controls (counters, shift registers, outputs) all change together at the edge, so nothing can see a half-updated state. There is a classic alternative style that splits the next-state logic into a separate `always @(*)` block. It works too, but for designs this size the one-block style has fewer ways to go wrong (no latches, no missing cases).
- **Register the outputs.** Drive `o_tx` from a register set inside the state machine, not from a combinational decode of the state. A registered output cannot glitch, which matters on a wire leaving the chip.
- **Counters live alongside the state.** "Stay in START for 217 clocks" is a state plus a counter that resets on entry and is compared with `CPB - 1`. "Eight data bits" is a bit index counting 0 to 7. Chapter 0.1's off-by-one warning applies twice here: once to the clock count and once to the bit count.
- **Every state has a way out**, and a `default` sends any impossible state back to idle.

You do not need four states. A transmitter can also be IDLE plus one "sending" state with a 10-bit shift register holding `{1'b1, data, 1'b0}` (stop, data, start), shifting right and putting bit 0 on the line every `CPB` clocks. Both styles are good; pick the one that reads more clearly to you.

## 4. The transmitter

`uart_tx` has a simple handshake:

| Port | Meaning |
| --- | --- |
| `i_valid`, `i_data[7:0]` | "Please send this byte." |
| `o_busy` | "I am sending; do not ask yet." |
| `o_tx` | the serial line, 1 when idle |

A byte is **accepted** on a rising edge where `i_valid` is 1 and `o_busy` is 0. At that edge, copy `i_data` into the transmitter's own register; the caller may change `i_data` immediately afterwards, and the testbench changes it every clock to make sure you do not keep reading it. `i_valid` while busy is simply ignored.

The rules the testbench holds you to:

- **The start bit begins at once**: `o_tx` goes low right after the accepting edge (or, at the latest, one edge later).
- **Every bit lasts exactly CPB clocks**, the stop bit included.
- **`o_busy` goes high at the accepting edge** and stays high through the stop bit; it may fall at the earliest on the last clock of the stop bit, and must fall within 2 clocks after the stop bit ends. If `o_busy` rose a clock late, a caller holding `i_valid` high would get the same byte accepted twice.
- **Back-to-back**: with `i_valid` held high, one frame follows another 10 × CPB to 10 × CPB + 2 clocks apart. The receiver must cope with a PC that sends with no gaps, and the echo in Section 7 depends on the transmitter keeping up.

Write `uart_tx` with parameters `CLK_HZ` and `BAUD`. Initialize `o_tx` to 1 and `o_busy` to 0; a transmit line that powers up low sends the PC a spurious start bit. The whole module is a state register (or a busy flag), a clock counter, a bit counter and a shift register: around 25 flip-flops.

## 5. The receiver

Receiving is the harder half, because the receiver has no say in when bytes arrive. Its input is an asynchronous signal from another chip with another clock, exactly the situation Chapter 0.3 prepared you for.

**Synchronize first.** Pass `i_rx` through your `synchronizer` inside `uart_rx`, and let nothing else read the raw pin. The two clocks of latency shift every sample point by the same amount, so they cost nothing.

There is one wrinkle. Your synchronizer powers up holding 0, so for the first two clocks after configuration the receiver sees the line low, which looks like the start of a frame. The next rule takes care of it.

**Find the start bit, then confirm it at mid-bit.** In the idle state, wait for the synchronized line to be 0 (a falling edge from idle). Then count half a bit, `(CPB - 1) / 2` or `CPB / 2` clocks, and look again. If the line is still 0, it is a real start bit, and you are now standing in its middle. If it is 1, it was a glitch (or the power-up artifact) and you go back to idle. The testbench sends low pulses of 0.1 to 0.3 bit on an idle line and expects nothing to come out.

**Sample every bit in its middle.** From the middle of the start bit, wait CPB clocks to reach the middle of data bit 0, sample it, wait CPB clocks, sample bit 1, and so on. Shift each sample in from the top (`{rx, shift[7:1]}`) so that bit 0, sent first, ends in position 0.

```
 line      ‾‾‾|_____start_____|‾‾‾‾‾d0‾‾‾‾‾|_____d1______|‾‾‾ ...
 samples          ^ confirm           ^ d0          ^ d1
              |<- CPB/2 ->|<---- CPB ---->|<--- CPB --->|
```

The middle is the right place because it is as far as possible from both edges, which is what gives the timing budget of Section 2: the sample can drift almost half a bit before it lands in the wrong bit. Sampling right after the edge instead leaves no margin, and the testbench, which sends 1.5% fast and 1.5% slow, will catch it.

**Check the stop bit.** CPB clocks after data bit 7, sample the stop bit.

- If it is 1, the frame is good. Put the byte on `o_data`, pulse `o_valid` high for exactly one clock, and go straight back to idle. Reporting and returning at mid-stop, instead of waiting out the stop bit, means you are watching for the next start edge half a bit early, so a back-to-back frame from a slightly fast sender is never missed.
- If it is 0, it is a framing error. Discard the byte (no `o_valid`), then **wait until the line is high** before looking for a new start bit. Otherwise a line held low (a break, or a sender at the wrong baud rate) looks like an endless stream of start bits, and the receiver invents bytes out of it. The testbench sends frames with a 0 stop bit followed by up to 3 more bit times of low line.

`o_data` must hold its value between pulses. The display shows it, and in Chapter 0.5 a FIFO captures it on `o_valid`.

Many UARTs sample each bit several times (16× oversampling) and take a majority vote. That buys some noise immunity on long, noisy cables, at the cost of more logic. For a few centimetres of board trace and a USB chip, one sample in the middle of each bit is the standard choice, and it is what the tests expect.

## 6. Testing both halves

This chapter comes with four testbenches. Before running them, write the smallest one of your own: instantiate `uart_tx` and `uart_rx` with `CLK_HZ = 1_600_000` and `BAUD = 100_000` (16 clocks per bit), wire `o_tx` to `i_rx`, send one byte, and wait for `o_valid`. Open it in GTKWave with the transmitter's line, the receiver's state, its counters and `o_valid`, and confirm that each sample lands in the middle of a bit. It takes twenty lines, and it is the quickest way to see whether your state machines do what your diagrams say.

Then run the supplied ones:

- **`tb_uart_tx`** checks the transmitter clock by clock against a model, at 217 and at 13 clocks per bit. It requests bytes at random, holds `i_valid` high for back-to-back runs, and leaves long idle gaps. It reports a wrong bit value separately from a bit edge in the wrong place.
- **`tb_uart_rx`** plays the PC. It drives `i_rx` with delays in fractions of a nanosecond, so bit edges never line up with your clock, at the nominal rate and 1.5% fast and slow. It sends single frames, bursts of back-to-back frames, glitches and framing errors, and checks that exactly the good bytes come out, in order, each as a one-clock pulse.
- **`tb_uart_loopback`** is the step's "Done when" test. It wires your transmitter into your receiver and sends all 256 byte values back-to-back, at 217 and at 13 clocks per bit.
- **`tb_uart_echo`** checks the echo design of the next section.

Then break things on purpose. Compare the clock counter against `CPB` instead of `CPB - 1`. Send the data MSB first. Skip the mid-bit confirmation of the start bit. Accept a 0 stop bit. Each should fail with a message that points at the mistake. Note that the off-by-one in the transmitter fails `tb_uart_tx` at both bit rates, but `tb_uart_loopback` only at 13 clocks per bit: at 217, a bit one clock too long is 0.5% slow, and your receiver is designed to tolerate exactly that. A loopback test alone cannot catch two halves that are wrong in a compatible way, which is why each half has its own test against an independent model.

## 7. The echo

`uart_echo` joins the two halves: every byte `uart_rx` delivers goes to `uart_tx`, and also to an 8-bit register `o_last` that drives the display. It looks as if you could simply wire `o_valid` to `i_valid` and `o_data` to `i_data`. Work through the timing of a back-to-back stream, though:

- Byte 1 arrives at mid-stop, 9.5 bit times after its start edge. The transmitter starts echoing it.
- Byte 2 arrives 10 bit times later, at 19.5.
- The transmitter is busy until 9.5 + 10 = 19.5 bit times, plus a clock or two of handshake.

So byte 2 arrives just before the transmitter is free, and `i_valid` is ignored because `o_busy` is still high. Byte 2 is lost. The fix is one byte of storage: when a byte arrives, copy it into a holding register and set a "pending" flag; drive the transmitter from those, and clear the flag when the transmitter accepts the byte. The transmitter's own shift register holds the byte in flight, the holding register holds the next one, and the echo never drops a byte while the PC sends at the same rate as the FPGA.

If the PC sends even slightly faster than the FPGA can send back, the backlog grows by a fraction of a clock per byte, and over thousands of back-to-back bytes one byte of storage is eventually not enough. The real fix is a FIFO, which is the next chapter. For now, the host script sends in chunks of 64 bytes and reads each chunk back before sending more; inside a chunk it still sends back-to-back.

Make `uart_echo` with parameters `CLK_HZ` and `BAUD`, ports `i_clk`, `i_rx`, `o_tx` and `o_last[7:0]`, with `o_last` starting at `00`. `tb_uart_echo` sends 40 spaced bytes and then eight bursts of 32 back-to-back bytes at 16 clocks per bit, decodes everything that comes back, and checks that `o_last` shows each byte by the end of its stop bit. Try the direct-wiring version first and watch it fail on the bursts.

## 8. The board design

Make `projects/0.4-uart-echo/` with a `Makefile` and a `top_goboard.v`. Put `uart_tx.v`, `uart_rx.v` and `uart_echo.v` in `rtl/common/` and list them in `SRCS`, along with `synchronizer.v` and `hex_to_7seg.v`. Set:

```make
TESTS := tb_uart_tx tb_uart_rx tb_uart_loopback tb_uart_echo
```

`top_goboard` takes a `CLK_HZ` parameter (default 25,000,000) and is only wiring:

- `i_UART_RX` (pin 73) into a `uart_echo` with `BAUD = 115_200`, and its `o_tx` out on `o_UART_TX` (pin 74),
- `o_last[7:4]` to digit 1 and `o_last[3:0]` to digit 2 through two `hex_to_7seg` decoders, segments inverted, on the pins from Chapter 0.2.

The names are from the FPGA's point of view: `i_UART_RX` is what the FPGA receives (the USB chip's transmit pin), `o_UART_TX` is what it sends. Getting this backwards is the most common UART wiring mistake, and the `.pcf` names make it hard to do here.

**Read the report.** Expect about 80 flip-flops (the receiver is the largest part, at roughly 35) and well under 200 logic cells, and 17 `SB_IO`: the clock, two UART pins and 14 segments.

## 9. Bringing it up on the board

1. From the repository root, run `make test`. All four new testbenches must print `PASS`, along with every earlier one.
2. In `projects/0.4-uart-echo/`, run `make clean prog`. The display shows `00`.
3. **Find the serial port.** The Go Board's FTDI chip has two channels. One programs the flash; the other is the UART. On Linux they appear as `/dev/ttyUSB0` and `/dev/ttyUSB1`, and the UART is usually the second; on Windows look for two COM ports in Device Manager; on macOS, `/dev/cu.usbserial-*`. On Linux your user needs to be in the `dialout` group (or equivalent) to open it.
4. **Type at it.** Open a terminal program at 115,200 baud, 8N1, no flow control, for example `picocom -b 115200 /dev/ttyUSB1` or `screen /dev/ttyUSB1 115200`. Terminals do not print what you type, so each character you see is the FPGA's echo. Type `A` and the display shows `41`, its ASCII code; `a` shows `61`, Enter shows `0D`. Close the terminal before the next step, since only one program can hold the port.
5. **The 10,000-byte test.** Copy `uart_echo_test.py` from this chapter to `host/uart_echo_test.py`. Host-side scripts live in `host/` from now on. Install pyserial (`pip install -r requirements.txt` if you listed it in Chapter 0.1), then run:

   ```sh
   python3 host/uart_echo_test.py /dev/ttyUSB1
   ```

   It sends 10,000 random bytes in chunks of 64, checks every echoed byte, and prints one line. On `PASS` it also names the last byte it sent, and the display must show that byte.

If nothing echoes but the display changes, the receiver works and the transmitter or its pin does not. If nothing at all happens, you probably have the wrong port, or the TX and RX names swapped. Garbled characters mean a baud-rate mismatch: check the terminal setting and that `CLK_HZ` reaches the UART. If the script fails after a few thousand bytes with "only 63 of 64 bytes came back", the echo is dropping a byte under back-to-back load: recheck the holding register from Section 7, then `tb_uart_echo`.

**Done when:** `make test` prints PASS for every testbench, including `tb_uart_loopback` sending all 256 byte values from your transmitter into your receiver, and `uart_echo_test.py` sends 10,000 random bytes and gets every one back, with the display showing the last.

## Testbench requirements

**Modules you provide**, each in a file named after the module under `rtl/` (for example `rtl/common/`), with outputs either `reg` or `wire`:

```verilog
module uart_tx #(
    parameter integer CLK_HZ = 25_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire       i_clk,
    input  wire       i_valid,
    input  wire [7:0] i_data,
    output reg        o_tx,
    output reg        o_busy
);

module uart_rx #(
    parameter integer CLK_HZ = 25_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire       i_clk,
    input  wire       i_rx,       // raw, asynchronous; synchronize inside
    output reg        o_valid,
    output reg  [7:0] o_data
);

module uart_echo #(
    parameter integer CLK_HZ = 25_000_000,
    parameter integer BAUD   = 115_200
) (
    input  wire       i_clk,
    input  wire       i_rx,
    output wire       o_tx,
    output reg  [7:0] o_last
);
```

With `CPB = CLK_HZ / BAUD`:

- `uart_tx`: `o_tx = 1`, `o_busy = 0` at time zero. Accepts `i_data` on an edge with `i_valid = 1` and `o_busy = 0`. Start bit begins right after that edge (or one edge later); 8N1, LSB first, every bit exactly CPB clocks. `o_busy` is 1 from the accepting edge until at least the last clock of the stop bit, and 0 within 2 clocks after it. Held `i_valid` gives frames 10·CPB to 10·CPB+2 clocks apart. Tested at (`CLK_HZ`, `BAUD`) = (25,000,000, 115,200) and (1,300,000, 100,000).
- `uart_rx`: `o_valid = 0` at time zero, no byte from power-up or from glitches up to 0.3 bit. Confirms the start bit at mid-bit, samples at mid-bit, checks the stop bit. Each good frame gives one one-clock `o_valid` pulse between the start of the stop bit and 6 clocks after its end, with the byte on `o_data`, held until the next pulse. Frames with a 0 stop bit give nothing, and the receiver waits for the line to go high afterwards. Works back-to-back and at ±1.5% baud error. Tested at the same two parameter sets.
- `uart_echo`: `uart_rx` into `uart_tx` through a one-byte holding register, so back-to-back bytes are not dropped. `o_tx = 1` and `o_last = 00` at time zero; `o_last` shows each received byte by the end of its stop bit. Tested at (1,600,000, 100,000).
- `synchronizer` from Chapter 0.3 must also be in `rtl/` if your receiver uses it.

**Testbenches:** copy `tb_uart_tx.v`, `tb_uart_rx.v`, `tb_uart_loopback.v` and `tb_uart_echo.v` from this chapter to `sim/` (top modules named after their files). **Host script:** copy `uart_echo_test.py` to `host/`; it needs Python 3 and pyserial.

**Run them:** from the repository root, `make test`. Without the Makefiles:

```sh
mkdir -p build/sim
for tb in tb_uart_tx tb_uart_rx tb_uart_loopback tb_uart_echo; do
    iverilog -g2012 -Wall -Wno-timescale -o build/sim/$tb.vvp -y rtl/common sim/$tb.v
    (cd build/sim && vvp -n $tb.vvp)              # add +vcd to write <tb>.vcd
done
```

Each prints one line starting with `PASS` or `FAIL`. On the board: `python3 host/uart_echo_test.py <port>` prints `PASS` or `FAIL` the same way (options: `--count`, `--chunk`, `--baud`, `--seed`).
