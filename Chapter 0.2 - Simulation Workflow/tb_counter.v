// tb_counter -- self-checking testbench for Step 0.2 (Simulation workflow).
//
// Copy to sim/tb_counter.v. Expects a module `counter` in rtl/ (for example
// rtl/common/counter.v):
//
//   module counter #(parameter integer WIDTH = 8,
//                    parameter integer MAX   = (1 << WIDTH) - 1)
//                   (input  wire             i_clk,
//                    input  wire             i_en,
//                    output reg  [WIDTH-1:0] o_count,   // wire is fine too
//                    output wire             o_wrap);
//
// Behavior checked:
//   * o_count is 0 at time zero and never X or Z.
//   * On a rising edge of i_clk with i_en = 1, o_count goes up by one, or
//     back to 0 if it was MAX. With i_en = 0 it holds.
//   * o_wrap is combinational: 1 exactly when i_en = 1 and o_count == MAX,
//     i.e. "the next edge wraps". It must be 0 whenever i_en = 0.
//
// Part 1 drives i_en randomly into three instances with different parameters
// (default MAX, a MAX that is not a power of two minus one, and a tiny one)
// and compares them against a model every clock. Part 2 chains two counters
// the way the board does: a divider whose o_wrap is a one-clock "tick", and
// an 8-bit display counter enabled by that tick. With the divider's MAX = 9 a
// simulated "second" is 10 clocks, and the test checks the display counter
// steps 00, 01, ... FF, 00 exactly one step per second.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_counter.vcd.

`timescale 1ns/1ps
`default_nettype none

// Drives random enables into one counter and checks it against a model.
module tb_counter_check #(
    parameter integer WIDTH = 4,
    parameter integer MAX   = (1 << WIDTH) - 1,
    parameter integer SEED  = 1,
    parameter integer N     = 2000
) (
    input wire i_clk
);
    reg              en = 1'b0;
    wire [WIDTH-1:0] count;
    wire             wrap;

    counter #(.WIDTH(WIDTH), .MAX(MAX)) dut (
        .i_clk   (i_clk),
        .i_en    (en),
        .o_count (count),
        .o_wrap  (wrap)
    );

    localparam integer MAX_SHOWN = 5;

    integer errors = 0;
    integer wraps  = 0;
    integer cycle;
    integer model  = 0;
    integer seed   = SEED;
    reg     done   = 1'b0;

    task fail_msg(input [8*48-1:0] what, input integer got, input integer exp);
        begin
            if (errors < MAX_SHOWN)
                $display("  [WIDTH=%0d MAX=%0d] cycle %0d, en=%b: %0s = %0d, expected %0d",
                         WIDTH, MAX, cycle, en, what, got, exp);
            errors = errors + 1;
        end
    endtask

    initial begin
        #1;
        for (cycle = 0; cycle < N && errors < 20; cycle = cycle + 1) begin
            // Outputs are stable in the middle of the low phase. Check them,
            // then pick the enable for the next rising edge.
            @(negedge i_clk);
            if (^count === 1'bx || wrap !== 1'b0 && wrap !== 1'b1) begin
                if (errors < MAX_SHOWN)
                    $display("  [WIDTH=%0d MAX=%0d] cycle %0d: o_count=%b o_wrap=%b (X or Z)",
                             WIDTH, MAX, cycle, count, wrap);
                errors = errors + 1;
            end else begin
                if (count != model) fail_msg("o_count", count, model);
                if (wrap != (en && model == MAX)) fail_msg("o_wrap", wrap, en && model == MAX);
            end

            // Mostly enabled, so the counter wraps often; sometimes long
            // stretches of holds, so holding at MAX is exercised too.
            en = ($random(seed) & 3) != 0;
            #1;
            if (wrap != (en && model == MAX)) fail_msg("o_wrap (after en change)", wrap, en && model == MAX);

            @(posedge i_clk);
            if (en) begin
                if (model == MAX) begin
                    model = 0;
                    wraps = wraps + 1;
                end else
                    model = model + 1;
            end
        end
        if (errors == 0 && wraps < 3) begin
            $display("  [WIDTH=%0d MAX=%0d] counter wrapped only %0d times", WIDTH, MAX, wraps);
            errors = errors + 1;
        end
        done = 1'b1;
    end
endmodule

module tb_counter;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    // ---- Part 1: random enables, three parameter sets -------------------
    tb_counter_check #(.WIDTH(4),                .SEED(11)) chk_a (.i_clk(clk));
    tb_counter_check #(.WIDTH(8), .MAX(199),     .SEED(22), .N(3000)) chk_b (.i_clk(clk));
    tb_counter_check #(.WIDTH(3), .MAX(4),       .SEED(33)) chk_c (.i_clk(clk));

    // ---- Part 2: divider + display counter, as on the board -------------
    localparam integer TICK_MAX = 9;           // one "second" = 10 clocks
    localparam integer SECOND   = TICK_MAX + 1;

    wire [3:0] div_count;
    wire       tick;
    wire [7:0] value;
    wire       value_wrap;

    counter #(.WIDTH(4), .MAX(TICK_MAX)) u_div (
        .i_clk(clk), .i_en(1'b1), .o_count(div_count), .o_wrap(tick)
    );
    counter #(.WIDTH(8)) u_value (
        .i_clk(clk), .i_en(tick), .o_count(value), .o_wrap(value_wrap)
    );

    integer chain_errors = 0;
    integer clocks = 0;
    integer ticks  = 0;
    integer steps  = 0;
    integer since  = 0;
    reg [7:0] prev = 8'h00;
    reg       chain_done = 1'b0;

    // Count ticks in the middle of the low phase, where they are stable.
    always @(negedge clk)
        if (!chain_done) begin
            if (tick === 1'b1) ticks = ticks + 1;
            else if (tick !== 1'b0) begin
                if (chain_errors < 5) $display("  [chain] tick is %b at clock %0d", tick, clocks);
                chain_errors = chain_errors + 1;
            end
        end

    initial begin
        #1;
        if (value !== 8'h00) begin
            $display("  [chain] display counter should start at 00, got %h", value);
            chain_errors = chain_errors + 1;
        end
        // 256 steps take the counter 00 -> FF -> 00.
        while (!chain_done) begin
            @(posedge clk); #1;
            clocks = clocks + 1;
            since  = since + 1;
            if (^value === 1'bx) begin
                $display("  [chain] display counter is %b at clock %0d", value, clocks);
                chain_errors = chain_errors + 1;
                chain_done = 1'b1;
            end else if (value !== prev) begin
                if (value !== prev + 8'd1) begin
                    if (chain_errors < 5)
                        $display("  [chain] display went %h -> %h, expected %h", prev, value, prev + 8'd1);
                    chain_errors = chain_errors + 1;
                end
                if (since != SECOND) begin
                    if (chain_errors < 5)
                        $display("  [chain] step to %h came %0d clocks after the previous one, expected %0d",
                                 value, since, SECOND);
                    chain_errors = chain_errors + 1;
                end
                steps = steps + 1;
                since = 0;
                prev  = value;
                if (steps == 256) chain_done = 1'b1;
            end else if (since > 2 * SECOND) begin
                $display("  [chain] display stuck at %h for %0d clocks", value, since);
                chain_errors = chain_errors + 1;
                chain_done = 1'b1;
            end
        end
        if (chain_errors == 0 && (value !== 8'h00 || ticks != 256)) begin
            $display("  [chain] after 256 seconds: display %h (expected 00), %0d ticks (expected 256)",
                     value, ticks);
            chain_errors = chain_errors + 1;
        end
    end

    // ---- Verdict --------------------------------------------------------
    integer total;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_counter.vcd");
            $dumpvars(0, tb_counter);
        end

        wait (chk_a.done && chk_b.done && chk_c.done && chain_done);
        #1;
        total = chk_a.errors + chk_b.errors + chk_c.errors + chain_errors;
        if (total == 0)
            $display("PASS tb_counter: 3 parameter sets match the model; 00..FF at one step per tick");
        else
            $display("FAIL tb_counter: %0d error(s)", total);
        $finish;
    end

    // Watchdog: the longest part needs about 3,000 clocks (30,000 ns).
    initial begin
        #200_000;
        $display("FAIL tb_counter: timeout (display counter made %0d of 256 steps)", steps);
        $finish;
    end
endmodule

`default_nettype wire
