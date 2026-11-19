// tb_uart_echo -- self-checking testbench for Step 0.4 (UART transmit and
// receive): the echo design that runs on the board.
//
// Copy to sim/tb_uart_echo.v. Expects a module `uart_echo` in rtl/ (for
// example rtl/common/uart_echo.v), built from your uart_rx and uart_tx:
//
//   module uart_echo #(parameter integer CLK_HZ = 25_000_000,
//                      parameter integer BAUD   = 115_200)
//                     (input  wire       i_clk,
//                      input  wire       i_rx,      // raw serial line from the PC
//                      output wire       o_tx,      // serial line to the PC
//                      output reg  [7:0] o_last);   // last byte received; wire is fine too
//
// Runs with CLK_HZ = 1.6 MHz and BAUD = 100,000, so CPB = 16 clocks per bit.
// The testbench is the PC: it drives i_rx asynchronously at exactly the
// nominal bit time and decodes o_tx. It checks:
//   * o_tx is 1 and o_last is 00 at time zero, and neither is ever X or Z.
//   * Every byte sent comes back on o_tx, unchanged and in order, as a
//     correct 8N1 frame (start 0, LSB first, stop 1), and nothing else does.
//   * That includes bursts of 32 bytes sent back-to-back with no idle time,
//     the way a PC sends a buffer. The echo must not drop a byte when one
//     arrives while the previous one is still being transmitted.
//   * By the end of each byte's stop bit, o_last shows that byte.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_uart_echo.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_uart_echo;
    localparam integer CLK_HZ    = 1_600_000;
    localparam integer BAUD      = 100_000;
    localparam integer CPB       = CLK_HZ / BAUD;  // 16
    localparam real    BIT_NS    = CPB * 10.0;
    localparam integer N_BURSTS  = 8;
    localparam integer BURST     = 32;
    localparam integer N_SINGLE  = 40;
    localparam integer N_TOTAL   = N_BURSTS * BURST + N_SINGLE;
    localparam integer MAX_SHOWN = 6;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg        rx = 1'b1;
    wire       tx;
    wire [7:0] last;

    uart_echo #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rx(rx), .o_tx(tx), .o_last(last)
    );

    integer   errors = 0;
    integer   seed   = 404;
    reg [7:0] sent [0:N_TOTAL-1];
    integer   n_sent = 0;
    integer   n_echo = 0;
    integer   cyc    = 0;
    integer   last_echo = 0;
    reg       done   = 1'b0;

    task err_msg(input [8*80-1:0] msg);
        begin
            if (errors < MAX_SHOWN) $display("  t=%0t: %0s", $time, msg);
            errors = errors + 1;
        end
    endtask

    // ---- Decode o_tx, the way the PC's UART would: find the falling edge of
    // the start bit, then sample each bit in its middle.
    reg [7:0] got;
    integer   i;
    always @(posedge clk) cyc = cyc + 1;
    initial begin
        #1;
        if (tx !== 1'b1)    err_msg("o_tx should start at 1 (idle)");
        if (last !== 8'h00) err_msg("o_last should start at 00");
        forever begin
            @(negedge clk);
            if (tx !== 1'b0 && tx !== 1'b1) begin
                err_msg("o_tx is X or Z");
                @(posedge tx);
            end else if (tx === 1'b0) begin
                repeat (CPB / 2 - 1) @(negedge clk);            // middle of the start bit
                if (tx !== 1'b0) err_msg("start bit on o_tx shorter than half a bit");
                for (i = 0; i < 8; i = i + 1) begin
                    repeat (CPB) @(negedge clk);
                    got[i] = tx;
                end
                repeat (CPB) @(negedge clk);
                if (tx !== 1'b1) begin
                    if (errors < MAX_SHOWN) $display("  echo %0d: stop bit is %b, expected 1", n_echo, tx);
                    errors = errors + 1;
                end
                if (n_echo >= n_sent) begin
                    if (errors < MAX_SHOWN) $display("  echo %0d: byte %h came back, but only %0d were sent", n_echo, got, n_sent);
                    errors = errors + 1;
                end else if (got !== sent[n_echo]) begin
                    if (errors < MAX_SHOWN) $display("  echo %0d: got %h back, expected %h", n_echo, got, sent[n_echo]);
                    errors = errors + 1;
                end
                n_echo    = n_echo + 1;
                last_echo = cyc;
                // Stay here until the line is idle again.
                while (tx === 1'b0) @(negedge clk);
            end
        end
    end

    // ---- The PC's transmitter.
    task send_byte(input [7:0] b);
        integer k;
        begin
            sent[n_sent] = b;
            n_sent = n_sent + 1;
            rx = 1'b0; #(BIT_NS);
            for (k = 0; k < 8; k = k + 1) begin
                rx = b[k]; #(BIT_NS);
            end
            rx = 1'b1; #(BIT_NS);
            // End of the stop bit: the display must already show this byte.
            if (last !== b) begin
                if (errors < MAX_SHOWN) $display("  byte %0d: o_last = %h at the end of its stop bit, expected %h", n_sent - 1, last, b);
                errors = errors + 1;
            end
        end
    endtask

    task wait_drain;
        begin
            while (n_echo < n_sent && cyc < last_echo + 40 * CPB) #(BIT_NS);
            #(3 * BIT_NS);
        end
    endtask

    integer bu, j;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_echo.vcd");
            $dumpvars(0, tb_uart_echo);
        end
        #(4 * BIT_NS + 2.71);                           // idle, off the clock grid

        // Single bytes with idle gaps between them.
        for (j = 0; j < N_SINGLE; j = j + 1) begin
            send_byte(j == 0 ? 8'h00 : (j == 1 ? 8'hff : $random(seed)));
            #((2.0 + (($random(seed) & 32'hff) / 64.0)) * BIT_NS);
        end
        wait_drain;

        // Back-to-back bursts.
        for (bu = 0; bu < N_BURSTS; bu = bu + 1) begin
            for (j = 0; j < BURST; j = j + 1) send_byte($random(seed));
            wait_drain;
        end

        #(20 * BIT_NS);
        done = 1'b1;
        if (n_echo != N_TOTAL) begin
            if (errors < MAX_SHOWN) $display("  %0d bytes sent, %0d echoed", N_TOTAL, n_echo);
            errors = errors + 1;
        end
        if (errors == 0)
            $display("PASS tb_uart_echo: %0d bytes echoed, including %0d back-to-back bursts of %0d; o_last = %h",
                     n_echo, N_BURSTS, BURST, last);
        else
            $display("FAIL tb_uart_echo: %0d error(s), %0d of %0d bytes echoed", errors, n_echo, N_TOTAL);
        $finish;
    end

    // Watchdog: the stimulus is about 3,300 bit times (0.53 ms); allow four
    // times that.
    initial begin
        #2_000_000;
        $display("FAIL tb_uart_echo: timeout, %0d of %0d sent, %0d echoed", n_sent, N_TOTAL, n_echo);
        $finish;
    end
endmodule

`default_nettype wire
