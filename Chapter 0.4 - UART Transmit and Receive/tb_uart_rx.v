// tb_uart_rx -- self-checking testbench for Step 0.4 (UART transmit and receive).
//
// Copy to sim/tb_uart_rx.v. Expects a module `uart_rx` in rtl/ (for example
// rtl/common/uart_rx.v), which synchronizes i_rx itself:
//
//   module uart_rx #(parameter integer CLK_HZ = 25_000_000,
//                    parameter integer BAUD   = 115_200)
//                   (input  wire       i_clk,
//                    input  wire       i_rx,      // raw serial line, idles high
//                    output reg        o_valid,   // one-clock pulse per byte
//                    output reg  [7:0] o_data);   // wire outputs are fine too
//
// The testbench plays the part of the PC: it drives i_rx asynchronously (bit
// edges never line up with the clock) at the nominal bit time, and also 1.5%
// fast and 1.5% slow, which real crystals and USB adapters are well inside.
// With CPB = CLK_HZ / BAUD clocks per bit, it checks:
//   * o_valid is 0 at time zero; o_valid is never X or Z, and o_data never is
//     once the first byte has arrived. The line starts idle (high) and the
//     power-up state of your synchronizer must not produce a byte.
//   * Every good 8N1 frame produces exactly one o_valid pulse, one clock wide,
//     with the right byte on o_data, in order, including frames sent
//     back-to-back with no idle time between them.
//   * The pulse comes after the stop bit has started and no later than 6
//     clocks after it ends (the stop bit must be checked).
//   * o_data holds its value between pulses.
//   * A low glitch on an idle line, 0.1 to 0.3 bit long, produces nothing:
//     the start bit must be re-checked at mid-bit.
//   * A frame whose stop bit is 0 (a framing error), with the line then held
//     low for up to 3 bit times, produces nothing, and the next good frame
//     after the line returns high is received normally.
//
// Two instances run side by side: CLK_HZ = 25 MHz, BAUD = 115,200 (CPB = 217)
// and CLK_HZ = 1.3 MHz, BAUD = 100,000 (CPB = 13, odd, so mid-bit is not a
// whole number of clocks).
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_uart_rx.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_uart_rx_check #(
    parameter integer CLK_HZ  = 1_300_000,
    parameter integer BAUD    = 100_000,
    parameter integer SEED    = 1,
    parameter integer N_ITEMS = 200
) (
    input wire i_clk
);
    localparam integer CPB       = CLK_HZ / BAUD;
    localparam real    TCLK      = 10.0;           // ns, matches the clock in tb_uart_rx
    localparam integer MAX_SHOWN = 5;
    localparam integer QLEN      = 1024;

    reg        line = 1'b1;
    wire       valid;
    wire [7:0] data;

    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(i_clk), .i_rx(line), .o_valid(valid), .o_data(data)
    );

    integer errors = 0;
    integer seed   = SEED;
    reg     done   = 1'b0;

    // Expected bytes, in order, with the time of their start edge and their
    // bit time in clocks.
    reg [7:0] q_byte [0:QLEN-1];
    real      q_t0   [0:QLEN-1];
    real      q_bit  [0:QLEN-1];
    integer   n_sent = 0;      // good frames sent
    integer   n_rx   = 0;      // o_valid pulses seen
    integer   n_glitch = 0, n_ferr = 0, n_b2b = 0;

    task err_msg(input [8*80-1:0] msg);
        begin
            if (errors < MAX_SHOWN) $display("  [CPB=%0d] t=%0t: %0s", CPB, $time, msg);
            errors = errors + 1;
        end
    endtask

    // ---- Checker: sample outputs in the middle of each clock's low phase.
    reg       prev_valid = 1'b0;
    reg [7:0] prev_data;
    reg       have_data  = 1'b0;
    real      lat;
    always @(negedge i_clk) if (!done) begin
        if (valid !== 1'b0 && valid !== 1'b1) begin
            err_msg("o_valid is X or Z");
        end else if (valid === 1'b1) begin
            if (prev_valid === 1'b1) begin
                err_msg("o_valid high for more than one clock");
            end else if (n_rx >= n_sent) begin
                if (errors < MAX_SHOWN)
                    $display("  [CPB=%0d] t=%0t: unexpected byte %h (after %0d good frames; glitch or framing error accepted?)",
                             CPB, $time, data, n_sent);
                errors = errors + 1;
                n_rx = n_rx + 1;
            end else begin
                if (data !== q_byte[n_rx % QLEN]) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CPB=%0d] byte %0d: received %h, expected %h", CPB, n_rx, data, q_byte[n_rx % QLEN]);
                    errors = errors + 1;
                end
                lat = ($realtime - q_t0[n_rx % QLEN]) / TCLK;      // clocks since start edge
                if (lat < 9.0 * q_bit[n_rx % QLEN] || lat > 10.0 * q_bit[n_rx % QLEN] + 6.0) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CPB=%0d] byte %0d: o_valid came %0.1f clocks after the start edge, expected %0.1f..%0.1f",
                                 CPB, n_rx, lat, 9.0 * q_bit[n_rx % QLEN], 10.0 * q_bit[n_rx % QLEN] + 6.0);
                    errors = errors + 1;
                end
                n_rx = n_rx + 1;
            end
            have_data = 1'b1;
        end else if (have_data) begin
            if (^data === 1'bx)            err_msg("o_data is X or Z after a byte was received");
            else if (data !== prev_data)   err_msg("o_data changed without an o_valid pulse");
        end
        prev_valid = valid;
        prev_data  = data;
    end

    // ---- Stimulus: the "PC" side.
    real bit_ns;

    function real frand;        // uniform 0..1
        input dummy;
        begin
            frand = ($random(seed) & 32'hffff) / 65536.0;
        end
    endfunction

    task idle_bits(input real n);
        begin
            line = 1'b1;
            #(n * bit_ns);
        end
    endtask

    // One 8N1 frame. stop_ok = 0 sends a 0 stop bit and keeps the line low
    // for extra_low more bit times.
    task send_frame(input [7:0] b, input stop_ok, input real extra_low);
        integer i;
        begin
            if (stop_ok) begin
                q_byte[n_sent % QLEN] = b;
                q_t0  [n_sent % QLEN] = $realtime;
                q_bit [n_sent % QLEN] = bit_ns / TCLK;
                n_sent = n_sent + 1;
            end
            line = 1'b0;                  #(bit_ns);       // start
            for (i = 0; i < 8; i = i + 1) begin
                line = b[i];              #(bit_ns);       // data, LSB first
            end
            if (stop_ok) begin
                line = 1'b1;              #(bit_ns);       // stop
            end else begin
                line = 1'b0;              #(bit_ns * (1.0 + extra_low));
                line = 1'b1;
            end
        end
    endtask

    integer item, kind, e;
    reg [7:0] b;
    initial begin
        #1;
        if (valid !== 1'b0) err_msg("o_valid should start at 0");
        bit_ns = CPB * TCLK;
        #(3.0 * bit_ns + 3.37);                          // idle, and off the clock grid

        for (item = 0; item < N_ITEMS; item = item + 1) begin
            e = ($random(seed) & 32'h7fff_ffff) % 3;     // baud error: -1.5%, 0, +1.5%
            bit_ns = CPB * TCLK * (e == 0 ? 0.985 : (e == 1 ? 1.0 : 1.015));
            kind = ($random(seed) & 32'h7fff_ffff) % 10;
            case (item)
                0: b = 8'h00;  1: b = 8'hff;  2: b = 8'h55;  3: b = 8'haa;
                4: b = 8'h01;  5: b = 8'h80;
                default: b = $random(seed);
            endcase
            if (item < 6) kind = 0;

            if (kind <= 5) begin
                // Good frame, then 0..2 idle bits (0 = back-to-back).
                send_frame(b, 1'b1, 0.0);
                case (($random(seed) & 32'h7fff_ffff) % 3)
                    0: n_b2b = n_b2b + 1;
                    1: idle_bits(0.5 + frand(0));
                    2: idle_bits(2.0 + frand(0));
                endcase
            end else if (kind <= 7) begin
                // Burst of 4 back-to-back frames.
                send_frame(b,                1'b1, 0.0);
                send_frame($random(seed),    1'b1, 0.0);
                send_frame($random(seed),    1'b1, 0.0);
                send_frame($random(seed),    1'b1, 0.0);
                n_b2b = n_b2b + 3;
                idle_bits(1.0 + frand(0));
            end else if (kind == 8) begin
                // Glitch on the idle line.
                idle_bits(1.0);
                line = 1'b0;
                #((0.1 + 0.2 * frand(0)) * bit_ns);
                idle_bits(1.5 + frand(0));
                n_glitch = n_glitch + 1;
            end else begin
                // Framing error, line held low a little longer, then idle.
                send_frame(b, 1'b0, 3.0 * frand(0));
                idle_bits(1.0 + frand(0));
                n_ferr = n_ferr + 1;
            end
        end
        idle_bits(4.0);
        @(negedge i_clk); #1;
        done = 1'b1;

        if (n_rx != n_sent) begin
            if (errors < MAX_SHOWN)
                $display("  [CPB=%0d] %0d good frames sent but %0d bytes received", CPB, n_sent, n_rx);
            errors = errors + 1;
        end
    end
endmodule

module tb_uart_rx;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    tb_uart_rx_check #(.CLK_HZ(25_000_000), .BAUD(115_200), .SEED(5),  .N_ITEMS(150)) chk_a (.i_clk(clk));
    tb_uart_rx_check #(.CLK_HZ(1_300_000),  .BAUD(100_000), .SEED(77), .N_ITEMS(600)) chk_b (.i_clk(clk));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_rx.vcd");
            $dumpvars(0, tb_uart_rx);
        end
        wait (chk_a.done && chk_b.done);
        #1;
        if (chk_a.errors == 0 && chk_b.errors == 0)
            $display("PASS tb_uart_rx: %0d + %0d bytes received at +-1.5%% baud error; %0d glitches and %0d framing errors ignored",
                     chk_a.n_rx, chk_b.n_rx, chk_a.n_glitch + chk_b.n_glitch, chk_a.n_ferr + chk_b.n_ferr);
        else
            $display("FAIL tb_uart_rx: %0d error(s) (received %0d/%0d and %0d/%0d bytes)",
                     chk_a.errors + chk_b.errors, chk_a.n_rx, chk_a.n_sent, chk_b.n_rx, chk_b.n_sent);
        $finish;
    end

    // Watchdog: the CPB = 217 stimulus takes about 6 ms of simulated time.
    initial begin
        #25_000_000;
        $display("FAIL tb_uart_rx: timeout (received %0d/%0d and %0d/%0d bytes)",
                 chk_a.n_rx, chk_a.n_sent, chk_b.n_rx, chk_b.n_sent);
        $finish;
    end
endmodule

`default_nettype wire
