// tb_uart_loopback -- self-checking testbench for Step 0.4 (UART transmit and
// receive). This is the step's "Done when" test: transmitter into receiver.
//
// Copy to sim/tb_uart_loopback.v. Expects `uart_tx` and `uart_rx` in rtl/ (and
// the `synchronizer` from Chapter 0.3 if your receiver uses it):
//
//   module uart_tx #(parameter integer CLK_HZ = 25_000_000, parameter integer BAUD = 115_200)
//                   (input wire i_clk, input wire i_valid, input wire [7:0] i_data,
//                    output reg o_tx, output reg o_busy);
//   module uart_rx #(parameter integer CLK_HZ = 25_000_000, parameter integer BAUD = 115_200)
//                   (input wire i_clk, input wire i_rx,
//                    output reg o_valid, output reg [7:0] o_data);
//
// For each of two parameter sets, CLK_HZ = 25 MHz / BAUD = 115,200 (CPB = 217)
// and CLK_HZ = 1.3 MHz / BAUD = 100,000 (CPB = 13), o_tx is wired straight to
// i_rx. All 256 byte values are sent back-to-back (i_valid held high, the next
// byte presented as soon as one is accepted), in a scrambled order, and the
// receiver must deliver exactly those 256 bytes, in order, each as a single
// one-clock o_valid pulse.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_uart_loopback.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_uart_loopback_check #(
    parameter integer CLK_HZ = 1_300_000,
    parameter integer BAUD   = 100_000
) (
    input wire i_clk
);
    localparam integer CPB       = CLK_HZ / BAUD;
    localparam integer MAX_SHOWN = 5;

    reg        valid = 1'b0;
    reg  [7:0] data  = 8'h00;
    wire       busy, line, rx_valid;
    wire [7:0] rx_data;

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .i_clk(i_clk), .i_valid(valid), .i_data(data), .o_tx(line), .o_busy(busy)
    );
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_rx (
        .i_clk(i_clk), .i_rx(line), .o_valid(rx_valid), .o_data(rx_data)
    );

    // Send order: i * 167 + 89 (mod 256) visits every value exactly once.
    function [7:0] order(input integer i);
        order = (i * 167 + 89) & 8'hff;
    endfunction

    integer errors  = 0;
    integer n_sent  = 0;
    integer n_rx    = 0;
    integer last_rx = 0;       // clock of the last o_valid
    integer last_tx = 0;       // clock of the last acceptance
    integer cyc     = 0;
    reg     prev_v  = 1'b0;
    reg     done    = 1'b0;

    // Sender: hold i_valid; present the next byte after each acceptance.
    initial begin
        @(negedge i_clk);
        data  = order(0);
        valid = 1'b1;
        while (n_sent < 256) begin
            @(posedge i_clk);
            if (valid && busy === 1'b0) begin
                n_sent  = n_sent + 1;
                last_tx = cyc;
                @(negedge i_clk);
                if (n_sent < 256) data = order(n_sent);
                else              valid = 1'b0;
            end
        end
    end

    // Receiver check, in the middle of each clock's low phase.
    always @(negedge i_clk) if (!done) begin
        cyc = cyc + 1;
        if (rx_valid !== 1'b0 && rx_valid !== 1'b1) begin
            if (errors < MAX_SHOWN) $display("  [CPB=%0d] o_valid is %b at clock %0d", CPB, rx_valid, cyc);
            errors = errors + 1;
        end else if (rx_valid) begin
            if (prev_v) begin
                if (errors < MAX_SHOWN) $display("  [CPB=%0d] o_valid high for more than one clock at clock %0d", CPB, cyc);
                errors = errors + 1;
            end else begin
                if (n_rx >= 256) begin
                    if (errors < MAX_SHOWN) $display("  [CPB=%0d] extra byte %h received after all 256", CPB, rx_data);
                    errors = errors + 1;
                end else if (rx_data !== order(n_rx)) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CPB=%0d] byte %0d: received %h, expected %h", CPB, n_rx, rx_data, order(n_rx));
                    errors = errors + 1;
                end
                n_rx    = n_rx + 1;
                last_rx = cyc;
            end
        end
        prev_v = rx_valid;
        // Done once everything is sent and nothing has arrived for 3 frame times.
        if (n_sent == 256 && cyc > last_tx + 3 * 10 * CPB && cyc > last_rx + 3 * 10 * CPB) begin
            done = 1'b1;
            if (n_rx != 256) begin
                if (errors < MAX_SHOWN) $display("  [CPB=%0d] 256 bytes sent, %0d received", CPB, n_rx);
                errors = errors + 1;
            end
        end
    end
endmodule

module tb_uart_loopback;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    tb_uart_loopback_check #(.CLK_HZ(25_000_000), .BAUD(115_200)) chk_a (.i_clk(clk));
    tb_uart_loopback_check #(.CLK_HZ(1_300_000),  .BAUD(100_000)) chk_b (.i_clk(clk));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_loopback.vcd");
            $dumpvars(0, tb_uart_loopback);
        end
        wait (chk_a.done && chk_b.done);
        #1;
        if (chk_a.errors == 0 && chk_b.errors == 0)
            $display("PASS tb_uart_loopback: all 256 byte values looped back at CPB = 217 and CPB = 13");
        else
            $display("FAIL tb_uart_loopback: %0d error(s) (CPB=217: %0d/256 received, CPB=13: %0d/256 received)",
                     chk_a.errors + chk_b.errors, chk_a.n_rx, chk_b.n_rx);
        $finish;
    end

    // Watchdog: 256 frames at CPB = 217 are about 556,000 clocks (5.6 ms).
    initial begin
        #12_000_000;
        $display("FAIL tb_uart_loopback: timeout (CPB=217: %0d/256 sent, %0d received; CPB=13: %0d/256 sent, %0d received)",
                 chk_a.n_sent, chk_a.n_rx, chk_b.n_sent, chk_b.n_rx);
        $finish;
    end
endmodule

`default_nettype wire
