// tb_light_unit.v -- the lighting unit alone (Chapter 6.7).
//
// The testbench plays the command engine's part: it models the normal RAM and the transformed-record RAM (1,024 words each,
// registered read, one clock of latency), gives the unit a matrix and a light with one-clock strobes while it is idle,
// starts it on a range of slots and, when o_done comes, compares byte 7 of every record in the range with the expected
// intensity from sim/light_cases.hex (written by sim/py/ref_light.py --files sim; bit-exact against host/light_model.py).
//
// Checked for every case:
//   * the intensity in byte 7 of each record of the range is exactly the model's, and bytes 6:0 are unchanged
//     (the records start as junk, with a junk byte 7 that is not the answer);
//   * no record outside the range is written;
//   * o_busy is high two clocks after i_start, o_done is one clock long, o_busy is low from the clock after it,
//     the unit finishes within 600 clocks per slot + 100, and a range of 0 slots finishes with o_done and no writes;
//   * the matrix and the light are kept between runs: every second case starts a second time, with junk on i_matrix and
//     i_light (and no strobe) and must give the same bytes again;
//   * no X on o_busy, o_done or the write port; o_rec_we only while o_busy (or in the clock of o_done: the last write may go out with it).
// The unit is free in latency, order and how it computes the light direction (before the first vertex, or once per vertex).
//
// Run from build/sim:   make test TESTS="tb_light_unit"      (a few seconds)
`timescale 1ns/1ps
`default_nettype none

module tb_light_unit;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         start = 1'b0, mat_we = 1'b0, light_we = 1'b0;
    reg  [9:0]  first = 10'd0;
    reg  [10:0] count = 11'd0;
    reg [191:0] matrix = 192'd0;
    reg  [63:0] light = 64'd0;
    wire        busy, done, rec_we;
    wire  [9:0] nrm_addr, rec_raddr, rec_waddr;
    wire [63:0] rec_wdata;
    reg  [47:0] nrm_q = 48'd0;
    reg  [63:0] rec_q = 64'd0;

    reg [47:0] nrm [0:1023];
    reg [63:0] rec [0:1023];
    reg        rec_written [0:1023];
    reg [63:0] cases [0:262143];

    light_unit dut (.i_clk(clk), .i_start(start), .i_first(first), .i_count(count), .o_busy(busy), .o_done(done),
        .i_mat_we(mat_we), .i_matrix(matrix), .i_light_we(light_we), .i_light(light),
        .o_nrm_addr(nrm_addr), .i_nrm_data(nrm_q), .o_rec_raddr(rec_raddr), .i_rec_data(rec_q),
        .o_rec_we(rec_we), .o_rec_waddr(rec_waddr), .o_rec_wdata(rec_wdata));

    // the RAMs: registered read; the write lands at the clock edge it is presented
    integer x_bad = 0, busy_bad = 0;
    always @(posedge clk) begin
        nrm_q <= nrm[nrm_addr];
        rec_q <= rec[rec_raddr];
        if (rec_we === 1'b1) begin
            rec[rec_waddr] <= rec_wdata;
            rec_written[rec_waddr] <= 1'b1;
        end
        if ((^{busy, done, rec_we}) === 1'bx) x_bad = x_bad + 1;
        if (rec_we === 1'b1 && busy !== 1'b1 && done !== 1'b1) busy_bad = busy_bad + 1;
    end

    // done is one clock long, busy drops the clock after
    reg done_q = 1'b0;
    integer done_bad = 0;
    always @(posedge clk) begin
        done_q <= done;
        if (done_q && done) done_bad = done_bad + 1;
        if (done_q && busy) done_bad = done_bad + 1;
    end

    integer ncases, pos, c, k, i, cnt, fst, bad_cases = 0, total = 0, errs = 0, clocks, tot_clocks = 0, max_clocks = 0, run;
    reg [63:0] w, hdr;
    reg [191:0] mv;
    reg [63:0] expw [0:1023];
    reg [47:0] nw;
    reg [63:0] want;
    reg [7:0] got_i;
    reg        timeout;

    function [63:0] junk(input integer s);
        junk = {8'hEE, 8'h11 + s[7:0], 16'h5A00 ^ s[15:0] * 16'd37, 16'hC0DE ^ s[15:0], 16'd0} ^ {s[9:0], 54'd0};
    endfunction

    task run_once(input integer expect_zero_writes);
        begin
            for (i = 0; i < 1024; i = i + 1) begin rec[i] = junk(i); rec_written[i] = 1'b0; end
            @(negedge clk);
            start = 1'b1; first = fst[9:0]; count = cnt[10:0];
            @(negedge clk);
            start = 1'b0; first = 10'hxxx; count = 11'hxxx;
            clocks = 0; timeout = 1'b0;
            while (done !== 1'b1 && !timeout) begin
                @(negedge clk);
                clocks = clocks + 1;
                if (clocks == 2 && busy !== 1'b1) begin
                    if (errs < 8) $display("tb_light_unit: case %0d: o_busy not high two clocks after i_start", c);
                    errs = errs + 1;
                end
                if (clocks > 600 * cnt + 100) timeout = 1'b1;
            end
            if (timeout) begin
                $display("tb_light_unit: case %0d: no o_done within %0d clocks", c, 600 * cnt + 100);
                errs = errs + 1;
            end
            @(negedge clk);
            @(negedge clk);
            if (busy !== 1'b0) begin
                if (errs < 8) $display("tb_light_unit: case %0d: o_busy still high after o_done", c);
                errs = errs + 1;
            end
            tot_clocks = tot_clocks + clocks;
            if (clocks > max_clocks) max_clocks = clocks;
            // compare: in range byte 7 and the rest; out of range nothing written
            for (i = 0; i < 1024; i = i + 1) begin
                if (i >= fst && i < fst + cnt) begin
                    want = junk(i);
                    want[63:56] = expw[i - fst][7:0];
                    if (rec[i] !== want) begin
                        if (errs < 8) $display("tb_light_unit: case %0d slot %0d (first %0d count %0d): record %h, expected %h", c, i, fst, cnt, rec[i], want);
                        errs = errs + 1;
                    end
                end else if (rec_written[i]) begin
                    if (errs < 8) $display("tb_light_unit: case %0d: slot %0d is outside the range %0d..%0d and was written", c, i, fst, fst + cnt - 1);
                    errs = errs + 1;
                end
            end
        end
    endtask

    initial begin
        for (i = 0; i < 1024; i = i + 1) begin nrm[i] = 48'hxxxxxxxxxxxx; rec[i] = 64'd0; rec_written[i] = 1'b0; end
        $readmemh("../../sim/light_cases.hex", cases);
        if (^cases[0] === 1'bx) begin
            $display("FAIL: tb_light_unit cannot read sim/light_cases.hex (run `.venv/bin/python sim/py/ref_light.py --files sim`, and run from build/sim)");
            $finish;
        end
        ncases = cases[0];
        pos = 1;
        repeat (5) @(negedge clk);
        for (c = 0; c < ncases; c = c + 1) begin
            hdr = cases[pos]; pos = pos + 1;
            cnt = hdr[47:32]; fst = hdr[31:16];
            mv = {cases[pos + 2], cases[pos + 1], cases[pos]}; pos = pos + 3;
            w = cases[pos]; pos = pos + 1;
            for (k = 0; k < cnt; k = k + 1) begin
                nw = cases[pos + k][47:0];
                nrm[fst + k] = nw;
                expw[k] = cases[pos + cnt + k];
            end
            pos = pos + 2 * cnt;
            // matrix and light with one-clock strobes (junk on the buses otherwise)
            @(negedge clk); matrix = mv; mat_we = 1'b1;
            @(negedge clk); mat_we = 1'b0; matrix = {6{32'hDEADBEEF}};
            @(negedge clk); light = w; light_we = 1'b1;
            @(negedge clk); light_we = 1'b0; light = 64'hFEDCBA9876543210;
            repeat (2) @(negedge clk);
            run_once(0);
            if (c % 2 == 1 && cnt > 0) begin               // again, with no new matrix or light: they must have been kept
                run_once(0);
            end
            total = total + cnt;
            for (k = 0; k < cnt; k = k + 1) nrm[fst + k] = 48'hxxxxxxxxxxxx;
        end
        repeat (10) @(negedge clk);
        if (errs != 0) $display("FAIL: tb_light_unit (%0d wrong results or protocol errors, see above)", errs);
        else if (x_bad != 0) $display("FAIL: tb_light_unit (X on o_busy, o_done or o_rec_we in %0d clocks)", x_bad);
        else if (busy_bad != 0) $display("FAIL: tb_light_unit (o_rec_we outside o_busy and o_done, %0d times)", busy_bad);
        else if (done_bad != 0) $display("FAIL: tb_light_unit (o_done longer than one clock, or o_busy high after it: %0d times)", done_bad);
        else $display("PASS: tb_light_unit (%0d cases, %0d vertices bit-exact; slowest run %0d clocks)", ncases, total, max_clocks);
        $finish;
    end

    initial begin #(200_000_000); $display("FAIL: tb_light_unit watchdog timeout"); $finish; end
endmodule
`default_nettype wire
