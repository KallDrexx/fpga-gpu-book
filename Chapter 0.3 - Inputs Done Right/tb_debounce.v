// tb_debounce -- self-checking testbench for Step 0.3 (Inputs done right).
//
// Copy to sim/tb_debounce.v. Expects a module `debounce` in rtl/ (for example
// rtl/common/debounce.v):
//
//   module debounce #(parameter integer CLK_HZ      = 25_000_000,
//                     parameter integer DEBOUNCE_MS = 10)
//                    (input  wire i_clk,
//                     input  wire i_in,       // already synchronized
//                     output reg  o_out);     // a wire output is fine too
//
// With N = CLK_HZ / 1000 * DEBOUNCE_MS (the number of clocks in DEBOUNCE_MS):
//   * o_out is 0 at time zero and never X or Z.
//   * o_out takes the value of i_in only after i_in has differed from o_out
//     for N consecutive rising edges. Any run shorter than N clocks is ignored,
//     however many such runs there are in a row.
//   * After a clean change of i_in, o_out follows within N to N+2 clocks
//     (the slack allows for slightly different counter designs).
//
// Two instances run side by side, (CLK_HZ, DEBOUNCE_MS) = (100_000, 10), so
// N = 1000, and (50_000, 4), so N = 200. A different N for each catches
// numbers hard-coded inside the module. The stimulus is synchronous (it
// changes just after rising edges), since the debouncer sits behind a
// synchronizer.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_debounce.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_debounce_check #(
    parameter integer CLK_HZ      = 100_000,
    parameter integer DEBOUNCE_MS = 10,
    parameter integer SEED        = 1
) (
    input wire i_clk
);
    localparam integer N         = CLK_HZ / 1000 * DEBOUNCE_MS;
    localparam integer MAX_SHOWN = 5;

    reg  in = 1'b0;
    wire out;

    debounce #(.CLK_HZ(CLK_HZ), .DEBOUNCE_MS(DEBOUNCE_MS)) dut (
        .i_clk(i_clk), .i_in(in), .o_out(out)
    );

    integer errors      = 0;
    integer cyc         = 0;   // rising edges so far
    integer changes     = 0;   // o_out changes so far
    integer last_change = 0;   // cyc of the most recent o_out change
    integer t_set       = 0;   // cyc at which i_in was last changed
    integer seed        = SEED;
    reg     prev_out    = 1'b0;
    reg     done        = 1'b0;

    always @(posedge i_clk) cyc = cyc + 1;

    // Watch o_out in the middle of the low phase.
    always @(negedge i_clk) if (!done) begin
        if (out !== 1'b0 && out !== 1'b1) begin
            if (errors < MAX_SHOWN) $display("  [N=%0d] o_out is %b at clock %0d", N, out, cyc);
            errors = errors + 1;
        end else if (out !== prev_out) begin
            changes     = changes + 1;
            last_change = cyc;
            prev_out    = out;
        end
    end

    // Change i_in just after the next rising edge. The DUT first sees the new
    // value at the edge after that.
    task set(input v);
        begin
            @(posedge i_clk); #1;
            in    = v;
            t_set = cyc;
        end
    endtask

    // Hold i_in for n more samples (set() already started the first one).
    task hold(input integer n);
        begin
            if (n > 1) repeat (n - 1) @(posedge i_clk);
        end
    endtask

    task error(input [8*64-1:0] what, input integer got, input integer lo, input integer hi);
        begin
            if (errors < MAX_SHOWN) begin
                if (lo == hi)
                    $display("  [N=%0d] %0s: got %0d, expected %0d", N, what, got, lo);
                else
                    $display("  [N=%0d] %0s: got %0d, expected %0d..%0d", N, what, got, lo, hi);
            end
            errors = errors + 1;
        end
    endtask

    // A pulse of exactly len samples away from the current output, then
    // back, then a long quiet stretch. o_out must not move.
    task pulse(input integer len);
        integer n_before;
        begin
            n_before = changes;
            set(~out);
            hold(len);
            set(out);
            hold(N + 5);
            if (changes != n_before) error("o_out changes after a short pulse", changes - n_before, 0, 0);
        end
    endtask

    // A clean change: o_out must follow within N..N+2 clocks, exactly once.
    task step(input v);
        integer n_before;
        begin
            n_before = changes;
            set(v);
            hold(N + 10);
            if (changes != n_before + 1)
                error("o_out changes after a clean step", changes - n_before, 1, 1);
            else if (last_change - t_set < N || last_change - t_set > N + 2)
                error("clocks from clean step to o_out change", last_change - t_set, N, N + 2);
        end
    endtask

    // Bouncing: runs of random length 1..N/4 alternating, for about 2N clocks
    // in total (longer than the debounce time), ending on v and then holding.
    // o_out must change once, N..N+2 clocks after the last bounce.
    task bounce(input v);
        integer n_before, total, len;
        begin
            n_before = changes;
            total  = 0;
            while (total < 2 * N) begin
                len = 1 + ($random(seed) & 32'h7fff_ffff) % (N / 4);
                set(~in);
                hold(len);
                total = total + len;
            end
            if (in !== v) set(v);
            hold(N + 10);
            if (changes != n_before + 1)
                error("o_out changes for one bouncy transition", changes - n_before, 1, 1);
            else if (last_change - t_set < N || last_change - t_set > N + 2)
                error("clocks from last bounce to o_out change", last_change - t_set, N, N + 2);
            else if (out !== v)
                error("o_out after bouncy transition", out, v, v);
        end
    endtask

    integer k;
    initial begin
        #1;
        if (out !== 1'b0) error("o_out at time zero", out, 0, 0);

        // Quiet input: nothing happens.
        hold(3 * N);
        if (changes != 0) error("o_out changes with i_in held at 0", changes, 0, 0);

        // Short high pulses from a low output.
        pulse(1); pulse(2); pulse(3); pulse(N / 2); pulse(N - 2); pulse(N - 1);

        step(1'b1);

        // Short low pulses from a high output.
        pulse(1); pulse(N / 3); pulse(N - 1);

        step(1'b0);

        // Bouncy presses and releases.
        for (k = 0; k < 5; k = k + 1) begin
            bounce(1'b1);
            bounce(1'b0);
        end

        done = 1'b1;
    end
endmodule

module tb_debounce;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    tb_debounce_check #(.CLK_HZ(100_000), .DEBOUNCE_MS(10), .SEED(3)) chk_a (.i_clk(clk));
    tb_debounce_check #(.CLK_HZ(50_000),  .DEBOUNCE_MS(4),  .SEED(9)) chk_b (.i_clk(clk));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_debounce.vcd");
            $dumpvars(0, tb_debounce);
        end

        wait (chk_a.done && chk_b.done);
        #1;
        if (chk_a.errors == 0 && chk_b.errors == 0)
            $display("PASS tb_debounce: short pulses and bounces ignored, clean changes follow after N clocks (N = 1000 and 200)");
        else
            $display("FAIL tb_debounce: %0d error(s)", chk_a.errors + chk_b.errors);
        $finish;
    end

    // Watchdog: the N = 1000 instance needs about 60,000 clocks (600,000 ns).
    initial begin
        #3_000_000;
        $display("FAIL tb_debounce: timeout");
        $finish;
    end
endmodule

`default_nettype wire
