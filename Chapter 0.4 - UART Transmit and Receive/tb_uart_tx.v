// tb_uart_tx -- self-checking testbench for Step 0.4 (UART transmit and receive).
//
// Copy to sim/tb_uart_tx.v. Expects a module `uart_tx` in rtl/ (for example
// rtl/common/uart_tx.v):
//
//   module uart_tx #(parameter integer CLK_HZ = 25_000_000,
//                    parameter integer BAUD   = 115_200)
//                   (input  wire       i_clk,
//                    input  wire       i_valid,   // "please send i_data"
//                    input  wire [7:0] i_data,
//                    output reg        o_tx,      // serial line, idles high
//                    output reg        o_busy);   // wire outputs are fine too
//
// With CPB = CLK_HZ / BAUD (integer division) clocks per bit, it checks:
//   * o_tx is 1 and o_busy is 0 at time zero; neither is ever X or Z.
//   * A byte is accepted on a rising edge where i_valid = 1 and o_busy = 0.
//     Nothing else starts a frame, and i_valid while busy is ignored.
//   * o_tx falls (start bit) right after the accepting edge or one edge later.
//   * The frame is: start bit 0, data bits LSB first, stop bit 1, every bit
//     lasting exactly CPB clocks. The data sent is i_data as it was at the
//     accepting edge, even though i_data keeps changing afterwards.
//   * o_busy is 1 from the accepting edge until at least the last clock of
//     the stop bit, and 0 again no more than 2 clocks after the stop bit ends.
//   * With i_valid held high, consecutive frames start 10*CPB to 10*CPB+2
//     clocks apart (back-to-back, no wasted idle time).
//   * o_tx is 1 whenever no frame is being sent.
//
// Two instances run side by side: CLK_HZ = 25 MHz, BAUD = 115,200 (CPB = 217,
// the real Go Board setting) and CLK_HZ = 1.3 MHz, BAUD = 100,000 (CPB = 13).
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_uart_tx.vcd.

`timescale 1ns/1ps
`default_nettype none

// Drives one uart_tx and checks every clock of its output.
module tb_uart_tx_check #(
    parameter integer CLK_HZ   = 1_300_000,
    parameter integer BAUD     = 100_000,
    parameter integer SEED     = 1,
    parameter integer N_FRAMES = 120
) (
    input wire i_clk
);
    localparam integer CPB       = CLK_HZ / BAUD;
    localparam integer MAX_SHOWN = 5;

    reg        valid = 1'b0;
    reg  [7:0] data  = 8'h00;
    wire       tx, busy;

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(i_clk), .i_valid(valid), .i_data(data), .o_tx(tx), .o_busy(busy)
    );

    integer errors   = 0;
    integer frames   = 0;      // frames completed
    integer cyc      = 0;      // rising edges so far
    integer seed     = SEED;
    reg     done     = 1'b0;

    // Model state
    integer phase    = 0;      // 0 idle, 1 accepted (waiting for start), 2 in frame, 3 frame over
    integer t_acc    = 0;      // edge of acceptance
    integer t_start  = 0;      // first edge after which o_tx was 0
    integer t_prev_start = -1;
    integer starts   = 0;      // frames started
    integer pos;
    integer bad_pos  = -1;

    function [8*12-1:0] bitname(input integer b);
        begin
            case (b)
                0:       bitname = "start bit";
                9:       bitname = "stop bit";
                default: bitname = (b == 1) ? "data bit 0" : (b == 2) ? "data bit 1" :
                                   (b == 3) ? "data bit 2" : (b == 4) ? "data bit 3" :
                                   (b == 5) ? "data bit 4" : (b == 6) ? "data bit 5" :
                                   (b == 7) ? "data bit 6" : "data bit 7";
            endcase
        end
    endfunction
    reg [9:0] frame  = 10'h3ff;
    integer mode     = 0;      // stimulus mode: 0 random, 1 held, 2 sparse
    reg     acc;
    integer k;

    task err(input [8*72-1:0] msg);
        begin
            if (errors < MAX_SHOWN)
                $display("  [CPB=%0d] clock %0d: %0s", CPB, cyc, msg);
            errors = errors + 1;
        end
    endtask

    initial begin
        #1;
        if (tx !== 1'b1)   err("o_tx should start at 1 (idle)");
        if (busy !== 1'b0) err("o_busy should start at 0");

        while (!done) begin
            @(posedge i_clk);
            // Inputs are stable here and o_busy still holds its old value.
            acc = (valid === 1'b1) && (busy === 1'b0);
            cyc = cyc + 1;
            if (acc) begin
                if (phase == 1 || phase == 2) err("byte accepted while a frame was still in progress");
                frame = {1'b1, data, 1'b0};
                t_acc = cyc;
                phase = 1;
            end
            #1;
            if ((tx !== 1'b0 && tx !== 1'b1) || (busy !== 1'b0 && busy !== 1'b1)) begin
                err("o_tx or o_busy is X or Z");
                done = 1'b1;
            end

            case (phase)
                0, 3: begin
                    if (tx !== 1'b1) begin
                        err("o_tx went low with no byte accepted");
                        phase = 0;
                    end
                    if (phase == 3 && busy === 1'b1 && cyc > t_start + 10 * CPB + 1) begin
                        err("o_busy still 1 more than 2 clocks after the stop bit ended");
                        phase = 0;
                    end
                    if (phase == 0 && busy === 1'b1) begin
                        err("o_busy is 1 with no frame in progress");
                    end
                end
                1: begin
                    if (busy !== 1'b1) err("o_busy not 1 right after the byte was accepted");
                    if (tx === 1'b0) begin
                        t_start = cyc;
                        phase   = 2;
                        starts  = starts + 1;
                        if (t_prev_start >= 0 && mode == 1 &&
                            (t_start - t_prev_start > 10 * CPB + 2)) begin
                            if (errors < MAX_SHOWN)
                                $display("  [CPB=%0d] with i_valid held, frames start %0d clocks apart, expected %0d..%0d",
                                         CPB, t_start - t_prev_start, 10 * CPB, 10 * CPB + 2);
                            errors = errors + 1;
                        end
                    end else if (cyc > t_acc + 1) begin
                        err("no start bit within 1 clock of accepting the byte");
                        phase = 0;
                    end
                end
            endcase

            if (phase == 2) begin
                k   = (cyc - t_start) / CPB;    // bit index: 0 start, 1..8 data, 9 stop
                pos = (cyc - t_start) % CPB;    // clock within the bit
                if (pos == 0) bad_pos = -1;
                // The value is judged in the middle of the bit; a mismatch
                // anywhere else means the bit boundaries are in the wrong place.
                if (pos == CPB / 2) begin
                    if (tx !== frame[k]) begin
                        if (errors < MAX_SHOWN)
                            $display("  [CPB=%0d] frame %0d (byte %h): %0s is %b, expected %b",
                                     CPB, starts - 1, frame[8:1], bitname(k), tx, frame[k]);
                        errors = errors + 1;
                        done   = 1'b1;
                    end
                end else if (tx !== frame[k] && bad_pos < 0) begin
                    bad_pos = pos;
                end
                if (pos == CPB - 1 && bad_pos >= 0 && !done) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CPB=%0d] frame %0d (byte %h): o_tx wrong at clock %0d of %0s; every bit must last exactly CPB = %0d clocks",
                                 CPB, starts - 1, frame[8:1], bad_pos, bitname(k), CPB);
                    errors = errors + 1;
                    done   = 1'b1;
                end
                if (cyc < t_start + 10 * CPB - 1 && busy !== 1'b1) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CPB=%0d] frame %0d: o_busy dropped during %0s, before the last clock of the stop bit",
                                 CPB, starts - 1, bitname(k));
                    errors = errors + 1;
                    done   = 1'b1;
                end
                if (cyc == t_start + 10 * CPB - 1) begin
                    frames       = frames + 1;
                    t_prev_start = t_start;
                    phase        = 3;
                end
            end

            if (errors > 0) done = 1'b1;             // stop at the first problem
            if (frames >= N_FRAMES && phase == 0) done = 1'b1;
            if (frames >= N_FRAMES && phase == 3 && busy === 1'b0) done = 1'b1;

            // Drive new inputs at the falling edge.
            @(negedge i_clk);
            if (phase == 3 && busy === 1'b0) phase = 0;
            data = $random(seed);
            mode = (frames / 20) % 3;
            case (mode)
                0: valid = $random(seed) & 1;                        // random requests
                1: valid = 1'b1;                                     // held: back to back
                2: valid = (($random(seed) & 32'hff) == 0);          // rare requests, idle gaps
            endcase
        end
    end
endmodule

module tb_uart_tx;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    tb_uart_tx_check #(.CLK_HZ(25_000_000), .BAUD(115_200), .SEED(11), .N_FRAMES(90))  chk_a (.i_clk(clk));
    tb_uart_tx_check #(.CLK_HZ(1_300_000),  .BAUD(100_000), .SEED(29), .N_FRAMES(300)) chk_b (.i_clk(clk));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_tx.vcd");
            $dumpvars(0, tb_uart_tx);
        end
        wait (chk_a.done && chk_b.done);
        #1;
        if (chk_a.errors == 0 && chk_b.errors == 0)
            $display("PASS tb_uart_tx: %0d + %0d frames with exact 8N1 timing at CPB = 217 and 13",
                     chk_a.frames, chk_b.frames);
        else
            $display("FAIL tb_uart_tx: %0d error(s)", chk_a.errors + chk_b.errors);
        $finish;
    end

    // Watchdog: 90 frames at CPB = 217 with idle gaps take about 250,000
    // clocks (2.5 ms). Allow four times that.
    initial begin
        #10_000_000;
        $display("FAIL tb_uart_tx: timeout (%0d and %0d frames done)", chk_a.frames, chk_b.frames);
        $finish;
    end
endmodule

`default_nettype wire
