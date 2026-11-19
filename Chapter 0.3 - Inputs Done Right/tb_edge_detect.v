// tb_edge_detect -- self-checking testbench for Step 0.3 (Inputs done right).
//
// Copy to sim/tb_edge_detect.v. Expects a module `edge_detect` in rtl/ (for
// example rtl/common/edge_detect.v):
//
//   module edge_detect (input  wire i_clk,
//                       input  wire i_level,
//                       output wire o_rise,
//                       output wire o_fall);
//
// Behavior checked (i_level is synchronous: it changes just after rising
// edges, as the output of a flip-flop would):
//   * The module keeps a registered copy of i_level from the previous clock,
//     starting at 0.
//   * o_rise = 1 exactly when i_level is 1 and the registered copy is 0;
//     o_fall = 1 exactly when i_level is 0 and the registered copy is 1.
//     Both are combinational, so a pulse is high during the first clock cycle
//     of the new level and is exactly one clock wide.
//   * Neither output is ever X or Z, and they are never both 1.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_edge_detect.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_edge_detect;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg  level = 1'b0;
    wire rise, fall;

    edge_detect dut (.i_clk(clk), .i_level(level), .o_rise(rise), .o_fall(fall));

    reg prev = 1'b0;                   // model of the registered copy
    always @(posedge clk) prev <= level;

    localparam integer N_CYCLES  = 2000;
    localparam integer MAX_SHOWN = 6;

    integer errors = 0;
    integer cycle;
    integer rises  = 0, falls = 0;     // pulses seen
    integer exp_r  = 0, exp_f = 0;     // pulses expected
    integer seed   = 5;
    reg     er, ef;

    task check_now;
        begin
            er = level & ~prev;
            ef = ~level & prev;
            if ((rise !== 1'b0 && rise !== 1'b1) || (fall !== 1'b0 && fall !== 1'b1)) begin
                if (errors < MAX_SHOWN)
                    $display("  cycle %0d: o_rise=%b o_fall=%b (X or Z)", cycle, rise, fall);
                errors = errors + 1;
            end else if (rise !== er || fall !== ef) begin
                if (errors < MAX_SHOWN)
                    $display("  cycle %0d: i_level=%b (previous clock %b): o_rise=%b o_fall=%b, expected %b %b",
                             cycle, level, prev, rise, fall, er, ef);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_edge_detect.vcd");
            $dumpvars(0, tb_edge_detect);
        end

        // Before the first edge: the registered copy must already be 0, so
        // with i_level = 0 both outputs are 0 (not X).
        cycle = 0;
        #1;
        check_now;

        for (cycle = 0; cycle < N_CYCLES; cycle = cycle + 1) begin
            @(posedge clk); #1;
            // Change about one cycle in three, so both single-cycle levels
            // and long runs occur.
            if (($random(seed) & 32'h7fff_ffff) % 3 == 0) level = ~level;
            #1;
            check_now;                 // right after the change: combinational?
            @(negedge clk);
            check_now;                 // middle of the cycle
            if (rise === 1'b1) rises = rises + 1;
            if (fall === 1'b1) falls = falls + 1;
            if (er) exp_r = exp_r + 1;
            if (ef) exp_f = exp_f + 1;
        end

        if (errors == 0 && (rises != exp_r || falls != exp_f || exp_r < 100)) begin
            $display("  saw %0d rises and %0d falls, expected %0d and %0d", rises, falls, exp_r, exp_f);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS tb_edge_detect: %0d rising and %0d falling edges, each one clock wide", rises, falls);
        else
            $display("FAIL tb_edge_detect: %0d error(s)", errors);
        $finish;
    end

    // Watchdog: 2,000 cycles take 20,000 ns.
    initial begin
        #200_000;
        $display("FAIL tb_edge_detect: timeout");
        $finish;
    end
endmodule

`default_nettype wire
