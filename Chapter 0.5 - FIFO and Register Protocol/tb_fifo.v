// tb_fifo -- self-checking testbench for Step 0.5 (FIFO and register
// protocol): the synchronous FIFO.
//
// Copy to sim/tb_fifo.v. Expects a module `fifo` in rtl/ (for example
// rtl/common/fifo.v):
//
//   module fifo #(parameter integer WIDTH = 8,
//                 parameter integer DEPTH = 512)          // a power of two
//                (input  wire             i_clk,
//                 input  wire             i_wr_en,
//                 input  wire [WIDTH-1:0] i_wr_data,
//                 input  wire             i_rd_en,
//                 output reg  [WIDTH-1:0] o_rd_data,       // wire is fine too
//                 output wire             o_empty,         // reg is fine too
//                 output wire             o_full,
//                 output reg  [$clog2(DEPTH):0] o_count);  // 0 .. DEPTH
//
// Rules checked after every rising edge, against a model:
//   * At time zero the FIFO is empty: o_count = 0, o_empty = 1, o_full = 0.
//   * A write is accepted on an edge where i_wr_en = 1 and o_full = 0; a read
//     on an edge where i_rd_en = 1 and o_empty = 0. Both use the flags as they
//     were just before the edge, so a write to a full FIFO is ignored even if
//     a read happens on the same edge, and a read from an empty FIFO is
//     ignored even if a write happens on the same edge.
//   * o_count is the number of words held. o_empty is 1 exactly when it is 0,
//     o_full exactly when it is DEPTH. They are never X or Z.
//   * After an accepted read, o_rd_data shows the oldest word (block RAM
//     style: the word appears after the reading edge, not before it). It
//     holds that value until the next accepted read.
//
// Three FIFOs run side by side: (WIDTH, DEPTH) = (8, 16), (8, 512) and
// (16, 32). Each is filled to full one word at a time and drained to empty,
// with the flags and count checked at every count, then has writes pushed at
// it while full and reads while empty, simultaneous reads and writes at
// empty, full and in between, and many thousands of random cycles that wrap
// the pointers repeatedly.
//
// Prints exactly one PASS or FAIL line, then $finish. Run with +vcd to dump
// tb_fifo.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_fifo_check #(
    parameter integer WIDTH = 8,
    parameter integer DEPTH = 16,
    parameter integer SEED  = 1
) (
    input wire i_clk
);
    localparam integer CW        = $clog2(DEPTH) + 1;
    localparam integer MAX_SHOWN = 5;

    reg              wr_en   = 1'b0;
    reg              rd_en   = 1'b0;
    reg  [WIDTH-1:0] wr_data = {WIDTH{1'b0}};
    wire [WIDTH-1:0] rd_data;
    wire             empty, full;
    wire [CW-1:0]    count;

    fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH)) dut (
        .i_clk(i_clk),
        .i_wr_en(wr_en), .i_wr_data(wr_data),
        .i_rd_en(rd_en), .o_rd_data(rd_data),
        .o_empty(empty), .o_full(full), .o_count(count)
    );

    integer errors = 0;
    integer seed   = SEED;
    integer cycles = 0;
    reg     done   = 1'b0;

    // ---- The model: a circular queue of the words the FIFO should hold.
    reg [WIDTH-1:0] q [0:DEPTH-1];
    integer         head = 0, n = 0;     // oldest word, words held
    reg [WIDTH-1:0] exp_rd;              // what o_rd_data should show
    reg             rd_known = 1'b0;     // no read yet: o_rd_data is free
    integer         n_writes = 0, n_reads = 0;
    integer         max_seen = 0;
    reg [WIDTH-1:0] next_word = {WIDTH{1'b0}};

    task err_msg(input [8*96-1:0] msg);
        begin
            if (errors < MAX_SHOWN)
                $display("  fifo #(%0d, %0d), cycle %0d: %0s", WIDTH, DEPTH, cycles, msg);
            errors = errors + 1;
        end
    endtask

    // Compare the outputs with the model. Called between edges.
    task check;
        reg [8*96-1:0] m;
        begin
            if (^{empty, full, count} === 1'bx) begin
                $sformat(m, "o_empty/o_full/o_count contain X or Z (o_count = %b)", count);
                err_msg(m);
            end else begin
                if (count != n) begin
                    $sformat(m, "o_count = %0d, expected %0d", count, n);
                    err_msg(m);
                end
                if (empty !== (n == 0)) begin
                    $sformat(m, "o_empty = %b with %0d word(s) held", empty, n);
                    err_msg(m);
                end
                if (full !== (n == DEPTH)) begin
                    $sformat(m, "o_full = %b with %0d of %0d word(s) held", full, n, DEPTH);
                    err_msg(m);
                end
            end
            if (rd_known && rd_data !== exp_rd) begin
                $sformat(m, "o_rd_data = %h, expected %h (after read #%0d)", rd_data, exp_rd, n_reads);
                err_msg(m);
            end
        end
    endtask

    // One clock: drive the inputs for the next rising edge, update the model
    // with what that edge must do, wait for it, then check the result.
    task cycle(input w, input r);
        reg acc_w, acc_r;
        begin
            wr_en   = w;
            rd_en   = r;
            wr_data = w ? next_word : {$random(seed)};   // junk when not writing
            acc_w   = w && (n != DEPTH);
            acc_r   = r && (n != 0);
            if (acc_r) begin
                exp_rd   = q[head];
                rd_known = 1'b1;
                head     = (head + 1) % DEPTH;
                n        = n - 1;
                n_reads  = n_reads + 1;
            end
            if (acc_w) begin
                q[(head + n) % DEPTH] = next_word;
                n        = n + 1;
                n_writes = n_writes + 1;
            end
            if (w) next_word = {$random(seed)};
            if (n > max_seen) max_seen = n;
            @(posedge i_clk);
            cycles = cycles + 1;
            @(negedge i_clk);
            check;
        end
    endtask

    // Random traffic: write with probability pw/256, read with pr/256.
    task random_run(input integer len, input integer pw, input integer pr);
        integer k;
        begin
            for (k = 0; k < len; k = k + 1)
                cycle((($random(seed) & 255) < pw), (($random(seed) & 255) < pr));
        end
    endtask

    integer k, seg;
    initial begin
        k = $random(seed);                       // warm up: small seeds start near 0
        next_word = {$random(seed)};
        @(negedge i_clk);
        check;                                   // time zero: empty

        repeat (3) cycle(1'b0, 1'b0);            // idle
        repeat (4) cycle(1'b0, 1'b1);            // reads while empty: ignored

        // Fill one word at a time: flags at every count from 1 to DEPTH.
        for (k = 0; k < DEPTH; k = k + 1) cycle(1'b1, 1'b0);
        repeat (5) cycle(1'b1, 1'b0);            // writes while full: ignored
        cycle(1'b1, 1'b1);                       // full: the read wins, the write is ignored
        cycle(1'b1, 1'b0);                       // full again
        repeat (3) cycle(1'b0, 1'b0);

        // Drain one word at a time: flags at every count down to 0.
        for (k = 0; k < DEPTH; k = k + 1) cycle(1'b0, 1'b1);
        repeat (5) cycle(1'b0, 1'b1);            // reads while empty: ignored
        repeat (4) cycle(1'b0, 1'b0);            // o_rd_data must hold

        cycle(1'b1, 1'b1);                       // empty: the write wins, the read is ignored
        repeat (20) cycle(1'b1, 1'b1);           // steady state at one word
        repeat (DEPTH / 2) cycle(1'b1, 1'b0);    // part full
        repeat (20) cycle(1'b1, 1'b1);           // simultaneous in the middle
        repeat (3) cycle(1'b0, 1'b1);

        // Random traffic, biased in turn towards full, empty and balanced.
        for (seg = 0; seg < 4; seg = seg + 1) begin
            random_run(2 * DEPTH + 40, 220, 60);
            random_run(DEPTH + 20, 128, 128);
            random_run(2 * DEPTH + 40, 60, 220);
            random_run(DEPTH + 20, 256, 256);
            random_run(DEPTH + 20, 200, 180);
        end

        // Finish by draining everything that is left.
        while (n > 0) cycle(1'b0, 1'b1);
        repeat (3) cycle(1'b0, 1'b1);

        if (max_seen != DEPTH) err_msg("internal: the test never reached full");
        done = 1'b1;
    end
endmodule

module tb_fifo;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    tb_fifo_check #(.WIDTH(8),  .DEPTH(16),  .SEED(5))  chk_a (.i_clk(clk));
    tb_fifo_check #(.WIDTH(8),  .DEPTH(512), .SEED(17)) chk_b (.i_clk(clk));
    tb_fifo_check #(.WIDTH(16), .DEPTH(32),  .SEED(29)) chk_c (.i_clk(clk));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_fifo.vcd");
            $dumpvars(0, tb_fifo);
        end

        wait (chk_a.done && chk_b.done && chk_c.done);
        #1;
        if (chk_a.errors + chk_b.errors + chk_c.errors == 0)
            $display("PASS tb_fifo: depths 16, 512 and 32 filled, drained and randomly exercised; %0d words through, flags right at every count",
                     chk_a.n_reads + chk_b.n_reads + chk_c.n_reads);
        else
            $display("FAIL tb_fifo: %0d error(s)", chk_a.errors + chk_b.errors + chk_c.errors);
        $finish;
    end

    // Watchdog: the DEPTH = 512 run is about 20,000 clocks (200,000 ns).
    initial begin
        #2_000_000;
        $display("FAIL tb_fifo: timeout");
        $finish;
    end
endmodule

`default_nettype wire
