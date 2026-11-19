// tb_blinker -- self-checking testbench for Step 0.1 (Toolchain and blink).
//
// Copy to sim/tb_blinker.v. Expects a module `blinker` in rtl/ (for example
// rtl/common/blinker.v):
//
//   module blinker #(parameter integer CLK_HZ = 25_000_000,
//                    parameter integer BLINK_HZ = 1)
//                   (input wire i_clk, output reg o_led);
//
// A real second is 25,000,000 clocks, far too slow to simulate comfortably,
// so the testbench overrides CLK_HZ with small values. Two instances run side
// by side with different parameters, which catches constants hard-coded
// inside the module:
//
//   A: CLK_HZ = 1000, BLINK_HZ = 1  -> toggles every 500 clocks
//   B: CLK_HZ =  600, BLINK_HZ = 3  -> toggles every 100 clocks
//
// For each instance it checks that:
//   * o_led is 0 at power-up and never X or Z,
//   * every toggle comes exactly CLK_HZ / (2 * BLINK_HZ) clocks after the
//     previous one (the first one counted from time zero), so the duty cycle
//     is exactly 50%,
//   * 60 full blinks take exactly 60 * CLK_HZ / BLINK_HZ clocks
//     ("60 blinks in 60 seconds").
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_blinker.vcd.

`timescale 1ns/1ps
`default_nettype none

// Watches one blinker output and counts errors.
module tb_blinker_check #(
    parameter integer CLK_HZ   = 1000,
    parameter integer BLINK_HZ = 1,
    parameter integer N_BLINKS = 60
) (
    input  wire i_clk,
    input  wire i_led
);
    localparam integer HALF      = CLK_HZ / (2 * BLINK_HZ);
    localparam integer N_TOGGLES = 2 * N_BLINKS;
    localparam integer EXPECTED  = N_BLINKS * 2 * HALF;

    integer errors  = 0;
    integer toggles = 0;
    integer since   = 0;   // clocks since the last toggle
    integer total   = 0;   // clocks since time zero
    reg     prev;
    reg     done    = 1'b0;

    // Only the first few errors per instance are printed.
    localparam integer MAX_SHOWN = 5;

    initial begin
        #1;
        if (i_led !== 1'b0) begin
            $display("  [CLK_HZ=%0d BLINK_HZ=%0d] o_led should start at 0, got %b",
                     CLK_HZ, BLINK_HZ, i_led);
            errors = errors + 1;
        end
        prev = 1'b0;

        while (!done) begin
            @(posedge i_clk); #1;          // sample just after the edge
            since = since + 1;
            total = total + 1;

            if (i_led !== 1'b0 && i_led !== 1'b1) begin
                $display("  [CLK_HZ=%0d BLINK_HZ=%0d] o_led is %b at clock %0d",
                         CLK_HZ, BLINK_HZ, i_led, total);
                errors = errors + 1;
                done   = 1'b1;
            end else if (i_led !== prev) begin
                if (since != HALF) begin
                    if (errors < MAX_SHOWN)
                        $display("  [CLK_HZ=%0d BLINK_HZ=%0d] toggle %0d came %0d clocks after the previous one, expected %0d",
                                 CLK_HZ, BLINK_HZ, toggles + 1, since, HALF);
                    errors = errors + 1;
                end
                toggles = toggles + 1;
                since   = 0;
                prev    = i_led;
                if (toggles == N_TOGGLES) done = 1'b1;
            end else if (since > 2 * HALF) begin
                $display("  [CLK_HZ=%0d BLINK_HZ=%0d] no toggle for %0d clocks (stuck?)",
                         CLK_HZ, BLINK_HZ, since);
                errors = errors + 1;
                done   = 1'b1;
            end
        end

        if (errors == 0 && total != EXPECTED) begin
            $display("  [CLK_HZ=%0d BLINK_HZ=%0d] %0d blinks took %0d clocks, expected %0d",
                     CLK_HZ, BLINK_HZ, N_BLINKS, total, EXPECTED);
            errors = errors + 1;
        end
    end
endmodule

module tb_blinker;
    reg clk = 1'b0;
    always #5 clk = ~clk;              // 100 MHz simulated; only clock counts matter

    wire led_a, led_b;

    blinker #(.CLK_HZ(1000), .BLINK_HZ(1)) dut_a (.i_clk(clk), .o_led(led_a));
    blinker #(.CLK_HZ(600),  .BLINK_HZ(3)) dut_b (.i_clk(clk), .o_led(led_b));

    tb_blinker_check #(.CLK_HZ(1000), .BLINK_HZ(1)) chk_a (.i_clk(clk), .i_led(led_a));
    tb_blinker_check #(.CLK_HZ(600),  .BLINK_HZ(3)) chk_b (.i_clk(clk), .i_led(led_b));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_blinker.vcd");
            $dumpvars(0, tb_blinker);
        end

        wait (chk_a.done && chk_b.done);
        #1;
        if (chk_a.errors == 0 && chk_b.errors == 0)
            $display("PASS tb_blinker: 60 blinks in exactly 60 s at 50%% duty, for 2 parameter sets");
        else
            $display("FAIL tb_blinker: %0d error(s)", chk_a.errors + chk_b.errors);
        $finish;
    end

    // Watchdog: if a checker never finishes, still print a FAIL line.
    // The slow instance needs 60,000 clocks (600,000 ns); allow twice that.
    initial begin
        #1_200_000;
        $display("FAIL tb_blinker: timeout (chk_a %0d/120 toggles, chk_b %0d/120 toggles)",
                 chk_a.toggles, chk_b.toggles);
        $finish;
    end
endmodule

`default_nettype wire
