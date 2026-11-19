// tb_button_counter -- self-checking testbench for Step 0.3 (Inputs done right).
//
// Copy to sim/tb_button_counter.v. Expects a module `button_counter` in rtl/
// (for example rtl/common/button_counter.v), built from your synchronizer,
// debounce, edge_detect and counter modules:
//
//   module button_counter #(parameter integer CLK_HZ      = 25_000_000,
//                           parameter integer DEBOUNCE_MS = 10)
//                          (input  wire       i_clk,
//                           input  wire       i_button,   // raw, asynchronous, high = pressed
//                           output wire [7:0] o_count);   // a reg output is fine too
//
// The testbench runs it with CLK_HZ = 100_000 (one clock = 10 us of "real"
// time) and DEBOUNCE_MS = 10, so the debounce time is N = 1000 clocks and
// 1 ms is 100 clocks. It drives i_button asynchronously (changes never line
// up with clock edges) and checks:
//   * o_count is 0 at time zero and never X or Z.
//   * Each press bounces: random glitches for 5 ms, both when pressed and when
//     released. Each press adds exactly one to o_count, and nothing else does.
//   * The increment arrives between N and N+8 clocks after the last bounce of
//     the press (synchronizer + debouncer + edge detector + counter latency).
//   * Short noise spikes (0.3 to 3 ms) while idle, and short drop-outs while
//     held, never count.
//   * After 20 presses o_count is 8'h14.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_button_counter.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_button_counter;
    localparam integer CLK_HZ      = 100_000;
    localparam integer DEBOUNCE_MS = 10;
    localparam integer N           = CLK_HZ / 1000 * DEBOUNCE_MS;   // 1000 clocks
    localparam real    MS          = 1000.0;     // 1 ms = 100 clocks of 10 ns
    localparam integer N_PRESSES   = 20;
    localparam integer MAX_SHOWN   = 6;

    reg clk = 1'b0;
    always #5 clk = ~clk;              // rising edges at 5, 15, 25, ... ns

    reg        button = 1'b0;
    wire [7:0] count;

    button_counter #(.CLK_HZ(CLK_HZ), .DEBOUNCE_MS(DEBOUNCE_MS)) dut (
        .i_clk(clk), .i_button(button), .o_count(count)
    );

    integer errors   = 0;
    integer cyc      = 0;              // rising edges so far
    integer incs     = 0;              // increments seen
    integer last_inc = 0;              // cyc of the most recent increment
    integer seed     = 2026;
    reg [7:0] prev   = 8'h00;
    reg       done   = 1'b0;

    always @(posedge clk) cyc = cyc + 1;

    // Watch o_count in the middle of the low phase.
    always @(negedge clk) if (!done) begin
        if (^count === 1'bx) begin
            if (errors < MAX_SHOWN) $display("  o_count is %b at clock %0d", count, cyc);
            errors = errors + 1;
        end else if (count !== prev) begin
            if (count !== prev + 8'd1) begin
                if (errors < MAX_SHOWN)
                    $display("  o_count jumped %h -> %h at clock %0d", prev, count, cyc);
                errors = errors + 1;
            end
            incs     = incs + 1;
            last_inc = cyc;
            prev     = count;
        end
    end

    // A random delay in ns, never a whole number, so changes never land on
    // a clock edge.
    function real rnd(input real lo, input real hi);
        begin
            rnd = lo + (hi - lo) * (($random(seed) & 32'hffff) / 65536.0) + 0.37;
        end
    endfunction

    // Bounce for about 5 ms, toggling at random intervals (0.03 to 0.4 ms),
    // then settle on v. Returns the clock count at the last change.
    task bounce_to(input v, output integer settled);
        real t, d;
        begin
            t = 0.0;
            while (t < 5.0 * MS) begin
                button  = ~button;
                settled = cyc;
                d = rnd(30.0, 400.0);
                t = t + d;
                #(d);
            end
            if (button !== v) begin
                button  = v;
                settled = cyc;
            end
        end
    endtask

    // Hold the current level for ms milliseconds, with an optional short
    // glitch of the opposite level in the middle.
    task hold_ms(input real ms, input real glitch_ms);
        begin
            if (glitch_ms > 0.0) begin
                #(ms * MS / 2.0);
                button = ~button;
                #(glitch_ms * MS);
                button = ~button;
                #(ms * MS / 2.0);
            end else
                #(ms * MS);
        end
    endtask

    task expect_none(input integer n_before, input [8*40-1:0] what);
        begin
            if (incs != n_before) begin
                if (errors < MAX_SHOWN)
                    $display("  %0s: o_count changed %0d time(s), expected 0", what, incs - n_before);
                errors = errors + 1;
            end
        end
    endtask

    integer p, n_before, settled, lat;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_button_counter.vcd");
            $dumpvars(0, tb_button_counter);
        end

        #1;
        if (count !== 8'h00) begin
            $display("  o_count should start at 00, got %h", count);
            errors = errors + 1;
        end

        // Idle with noise spikes shorter than the debounce time.
        n_before = incs;
        hold_ms(20.0, 0.3);
        hold_ms(20.0, 3.0);
        hold_ms(20.0, 0.0);
        expect_none(n_before, "noise while idle");

        for (p = 1; p <= N_PRESSES; p = p + 1) begin
            // Press: bounce, then held for 30 to 60 ms. Every few presses a
            // short drop-out happens while held.
            n_before = incs;
            bounce_to(1'b1, settled);
            hold_ms(30.0 + rnd(0.0, 30.0), (p % 4 == 0) ? 2.0 : 0.0);
            if (incs != n_before + 1) begin
                if (errors < MAX_SHOWN)
                    $display("  press %0d: o_count changed %0d time(s), expected exactly 1", p, incs - n_before);
                errors = errors + 1;
            end else begin
                lat = last_inc - settled;
                if (lat < N || lat > N + 8) begin
                    if (errors < MAX_SHOWN)
                        $display("  press %0d: increment came %0d clocks after the bouncing stopped, expected %0d..%0d",
                                 p, lat, N, N + 8);
                    errors = errors + 1;
                end
            end

            // Release: bounce, then idle for 30 to 60 ms. Must not count.
            n_before = incs;
            bounce_to(1'b0, settled);
            hold_ms(30.0 + rnd(0.0, 30.0), (p % 5 == 0) ? 1.0 : 0.0);
            expect_none(n_before, "release");
        end

        done = 1'b1;
        if (errors == 0 && count !== N_PRESSES) begin
            $display("  after %0d presses o_count = %h, expected %h", N_PRESSES, count, N_PRESSES[7:0]);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS tb_button_counter: %0d bouncy presses counted exactly once each (o_count = %h)",
                     N_PRESSES, count);
        else
            $display("FAIL tb_button_counter: %0d error(s), o_count = %h after %0d presses",
                     errors, count, N_PRESSES);
        $finish;
    end

    // Watchdog: 20 presses take at most about 2.6 s of "real" time, which is
    // 2,600,000 ns here. Allow twice that.
    initial begin
        #6_000_000;
        $display("FAIL tb_button_counter: timeout (o_count = %h)", count);
        $finish;
    end
endmodule

`default_nettype wire
