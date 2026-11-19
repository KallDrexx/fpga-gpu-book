// tb_synchronizer -- self-checking testbench for Step 0.3 (Inputs done right).
//
// Copy to sim/tb_synchronizer.v. Expects a module `synchronizer` in rtl/ (for
// example rtl/common/synchronizer.v):
//
//   module synchronizer (input  wire i_clk,
//                        input  wire i_async,
//                        output reg  o_sync);   // a wire output is fine too
//
// Behavior checked:
//   * o_sync is 0 at time zero and never X or Z.
//   * o_sync is i_async delayed by exactly two flip-flops: the value o_sync
//     shows after rising edge k is the value i_async had at rising edge k-2.
//     One flop (no protection) or three flops (wasted latency) both fail.
//
// i_async changes at random moments between clock edges, the way a button or
// an unrelated clock domain would, sometimes several times per clock period
// (pulses shorter than a clock may or may not be seen; that is allowed, as
// long as o_sync matches what was actually present at each edge).
//
// Simulation cannot show metastability itself; it only checks the structure.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_synchronizer.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_synchronizer;
    reg clk = 1'b0;
    always #5 clk = ~clk;              // rising edges at 5, 15, 25, ... ns

    reg  async = 1'b0;
    wire sync;

    synchronizer dut (.i_clk(clk), .i_async(async), .o_sync(sync));

    // Model: the value of i_async at the last two rising edges.
    reg m1 = 1'b0, m2 = 1'b0;
    always @(posedge clk) begin
        m2 <= m1;
        m1 <= async;
    end

    localparam integer N_CYCLES  = 3000;
    localparam integer MAX_SHOWN = 6;

    integer errors  = 0;
    integer cycle   = 0;
    integer changes = 0;           // number of times o_sync changed
    integer seed    = 7;
    reg     prev    = 1'b0;
    reg     done    = 1'b0;

    // Check in the middle of the low phase, where everything is settled.
    always @(negedge clk) if (!done) begin
        cycle = cycle + 1;
        if (sync !== 1'b0 && sync !== 1'b1) begin
            if (errors < MAX_SHOWN) $display("  cycle %0d: o_sync is %b", cycle, sync);
            errors = errors + 1;
        end else if (sync !== m2) begin
            if (errors < MAX_SHOWN)
                $display("  cycle %0d: o_sync = %b, expected %b (i_async two edges ago); now i_async = %b, one edge ago = %b",
                         cycle, sync, m2, async, m1);
            errors = errors + 1;
        end
        if (sync !== prev) changes = changes + 1;
        prev = sync;
    end

    // Stimulus: random changes at random times between edges.
    integer i, k, d;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_synchronizer.vcd");
            $dumpvars(0, tb_synchronizer);
        end

        #1;
        if (sync !== 1'b0) begin
            $display("  o_sync should start at 0, got %b", sync);
            errors = errors + 1;
        end

        for (i = 0; i < N_CYCLES / 4; i = i + 1) begin
            @(posedge clk);
            // 1 to 4 changes, 1-2 ns apart, all within 8 ns of the edge: never on one.
            k = 1 + ($random(seed) & 3);
            while (k > 0) begin
                d = 1 + (($random(seed) & 32'h7fff_ffff) % 2);   // 1 or 2 ns
                #(d);
                async = ($random(seed) & 1);
                k = k - 1;
            end
            // Then hold for 0 to 6 more clocks so long runs are tested too.
            k = ($random(seed) & 32'h7fff_ffff) % 7;
            repeat (k) @(posedge clk);
        end
        repeat (4) @(posedge clk);
        @(negedge clk);
        done = 1'b1;

        if (errors == 0 && changes < 100) begin
            $display("  o_sync changed only %0d times; stimulus not reaching the output?", changes);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS tb_synchronizer: o_sync = i_async delayed by exactly 2 clocks over %0d cycles", cycle);
        else
            $display("FAIL tb_synchronizer: %0d error(s)", errors);
        $finish;
    end

    // Watchdog: the stimulus needs well under 10,000 clocks (100,000 ns).
    initial begin
        #500_000;
        $display("FAIL tb_synchronizer: timeout");
        $finish;
    end
endmodule

`default_nettype wire
