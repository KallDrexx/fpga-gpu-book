// tb_blinker -- self-checking testbench for rtl/common/blinker.v
//
// Uses CLK_HZ = 1000 so a "second" is 1000 clocks. Checks that:
//   * the LED starts low and is never X/Z,
//   * every toggle comes exactly HALF clocks after the previous one
//     (so the duty cycle is exactly 50%),
//   * 60 full blinks take exactly 60 "seconds" (60 * CLK_HZ clocks).
// Ends with one PASS or FAIL line.

`timescale 1ns/1ps
`default_nettype none

module tb_blinker;
    localparam integer CLK_HZ    = 1000;
    localparam integer BLINK_HZ  = 1;
    localparam integer HALF      = CLK_HZ / (2 * BLINK_HZ);
    localparam integer N_BLINKS  = 60;
    localparam integer N_TOGGLES = 2 * N_BLINKS;

    reg clk = 1'b0;
    always #5 clk = ~clk;

    wire led;

    blinker #(.CLK_HZ(CLK_HZ), .BLINK_HZ(BLINK_HZ)) dut (
        .i_clk (clk),
        .o_led (led)
    );

    integer errors  = 0;
    integer toggles = 0;
    integer since   = 0;   // clocks since the last toggle
    integer total   = 0;   // clocks since start
    reg     prev;
    reg     done    = 1'b0;

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_blinker.vcd");
            $dumpvars(0, tb_blinker);
        end

        #1;
        if (led !== 1'b0) begin
            $display("ERROR: led should start at 0, got %b", led);
            errors = errors + 1;
        end
        prev = led;

        while (!done) begin
            @(posedge clk); #1;           // sample just after the edge
            since = since + 1;
            total = total + 1;

            if (led !== 1'b0 && led !== 1'b1) begin
                $display("ERROR: led is %b at clock %0d", led, total);
                errors = errors + 1;
                done = 1'b1;
            end else if (led !== prev) begin
                if (since != HALF) begin
                    $display("ERROR: toggle %0d came after %0d clocks, expected %0d",
                             toggles + 1, since, HALF);
                    errors = errors + 1;
                end
                toggles = toggles + 1;
                since   = 0;
                prev    = led;
                if (toggles == N_TOGGLES) done = 1'b1;
            end else if (since > HALF) begin
                $display("ERROR: no toggle for %0d clocks (stuck?)", since);
                errors = errors + 1;
                done = 1'b1;
            end
        end

        if (total != N_BLINKS * CLK_HZ) begin
            $display("ERROR: %0d blinks took %0d clocks, expected %0d",
                     N_BLINKS, total, N_BLINKS * CLK_HZ);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS tb_blinker: %0d blinks in %0d clocks, 50%% duty", N_BLINKS, total);
        else
            $display("FAIL tb_blinker: %0d error(s)", errors);
        $finish;
    end

    // Watchdog: a hung design must still produce a FAIL line.
    initial begin
        #(10 * (N_BLINKS + 2) * CLK_HZ * 10);
        $display("FAIL tb_blinker: timeout");
        $finish;
    end
endmodule

`default_nettype wire
