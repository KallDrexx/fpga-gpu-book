// tb_vertex_unit.v -- the vertex transform pipeline on its own (Chapter 6.5).
//
// The testbench plays a script (sim/vertex_cases.hex, made by sim/py/ref_vertex.py) against `vertex_unit`
// and compares every transformed vertex with sim/vertex_exp.hex, bit for bit. The unit sees two RAM models:
// the vertex buffer (read, one clock of latency) and the transformed-vertex buffer (written).
//
// Script words are 64 bits (16 hex digits), the top nibble is the type:
//   0  LOAD     bits [57:48] = slot, [47:0] = {z, y, x} (16 bits each, x in the low bits): written into the vertex buffer
//   1  MATRIX   bits [59:56] = part 0-3, [47:0] = entries 3*part .. 3*part+2, 16 bits each, the first in the low bits;
//               after part 3 the testbench pulses i_mat_we once with all 12 entries in i_matrix
//   2  PROJ     bits [31:16] = near, [15:0] = f: pulses i_proj_we once
//   3  RUN      bits [41:32] = first, [26:16] = count, [3:0] = idle clocks before the start pulse:
//               pulses i_start with i_first and i_count, waits for o_done, then compares slots first .. first+count-1
//   F  END
// The expected words (one per vertex of every RUN, in order) are {8'h00, flags, depth, sy, sx}. A vertex whose
// expected flags have bit 0 (NEAR) set is compared on its flags and its zero byte only: the position fields of a
// NEAR vertex are undefined and any value passes.
//
// The unit contract the testbench checks (Chapter 6.5, Section 3):
//   * i_start is only pulsed while o_busy is low. o_busy is high in the clock after i_start (when count > 0)
//     and low in the clock after o_done.
//   * o_done pulses for exactly one clock per start, after the last vertex has been written, and never otherwise.
//   * o_dst_we only fires between a start and its o_done, and only at addresses first .. first+count-1.
//   * count = 0 writes nothing and still ends with o_done.
//   * speed: at most 800 clocks per vertex plus 200 (a loose limit: the reference needs about 165).
//   * i_matrix / i_f / i_near are taken on i_mat_we / i_proj_we (they carry junk at all other times); before any,
//     the unit holds the identity matrix, f = 277, near = 1.
//
// Run from build/sim:   make test TESTS="tb_vertex_unit"       (about 10 seconds)
`timescale 1ns/1ps
`default_nettype none

module tb_vertex_unit;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         start = 1'b0, mat_we = 1'b0, proj_we = 1'b0;
    reg  [9:0]  first = 10'd0;
    reg  [10:0] count = 11'd0;
    reg [191:0] matrix = 192'd0;
    reg  [15:0] pf = 16'd0, pnear = 16'd0;
    wire        busy, done, dst_we;
    wire  [9:0] src_addr, dst_addr;
    wire [63:0] dst_data;
    reg  [47:0] src [0:1023];
    reg  [47:0] src_q = 48'd0;
    reg  [63:0] dst [0:1023];

    vertex_unit dut (.i_clk(clk), .i_start(start), .i_first(first), .i_count(count), .o_busy(busy), .o_done(done),
        .i_mat_we(mat_we), .i_matrix(matrix), .i_proj_we(proj_we), .i_f(pf), .i_near(pnear),
        .o_src_addr(src_addr), .i_src_data(src_q), .o_dst_we(dst_we), .o_dst_addr(dst_addr), .o_dst_data(dst_data));

    always @(posedge clk) begin
        src_q <= src[src_addr];
        if (dst_we) dst[dst_addr] <= dst_data;
    end

    // ---- monitors ----
    reg         running = 1'b0;
    integer     run_first = 0, run_count = 0, errors = 0, done_n = 0, start_clk = 0, clkn = 0;
    integer     writes = 0;
    always @(posedge clk) begin
        clkn <= clkn + 1;
        if (dst_we) begin
            writes <= writes + 1;
            if (!running) begin
                if (errors < 8) $display("tb_vertex_unit: o_dst_we outside a run (address %0d)", dst_addr);
                errors <= errors + 1;
            end else if (dst_addr < run_first || dst_addr >= run_first + run_count) begin
                if (errors < 8) $display("tb_vertex_unit: write to vertex %0d outside the run %0d..%0d", dst_addr, run_first, run_first + run_count - 1);
                errors <= errors + 1;
            end
        end
        if (done) begin
            done_n <= done_n + 1;
            if (!running || done_n != 0) begin
                if (errors < 8) $display("tb_vertex_unit: unexpected o_done");
                errors <= errors + 1;
            end
        end
    end

    reg [63:0] script [0:4095];
    reg [63:0] expw   [0:8191];
    integer    pc = 0, ep = 0, i, k, gap, t0, cyc;
    integer    n_runs = 0, n_vert = 0, n_flag = 0, bad_vert = 0, max_clk = 0, tot_clk = 0, tot_vert = 0;
    reg [63:0] w, e, gt;
    reg [191:0] mrow, mshadow = 192'd0;
    reg        fin = 1'b0;
    task fail(input [255:0] why);
        begin if (errors < 8) $display("tb_vertex_unit: %0s", why); errors = errors + 1; end
    endtask

    initial begin
        for (i = 0; i < 4096; i = i + 1) script[i] = 64'hF000_0000_0000_0000;
        for (i = 0; i < 1024; i = i + 1) begin src[i] = 48'd0; dst[i] = {64{1'bx}}; end
        $readmemh("../../sim/vertex_cases.hex", script);
        $readmemh("../../sim/vertex_exp.hex", expw);
        if (^expw[0] === 1'bx) begin
            $display("FAIL: tb_vertex_unit cannot read sim/vertex_cases.hex / vertex_exp.hex (run `.venv/bin/python sim/py/ref_vertex.py sim`, and run from build/sim)");
            $finish;
        end
        repeat (5) @(negedge clk);
        w = script[0];
        while (w[63:60] != 4'hF && !fin) begin
            case (w[63:60])
            4'h0: src[w[57:48]] = w[47:0];
            4'h1: begin
                mrow = mshadow;
                mrow[48*w[59:56] +: 48] = w[47:0];
                mshadow = mrow;
                if (w[59:56] == 4'd3) begin
                    @(negedge clk); matrix = mshadow; mat_we = 1'b1;
                    @(negedge clk); mat_we = 1'b0;
                    matrix = {$random, $random, $random, $random, $random, $random};   // junk: the unit must keep its own copy
                end
            end
            4'h2: begin
                @(negedge clk); pf = w[15:0]; pnear = w[31:16]; proj_we = 1'b1;
                @(negedge clk); proj_we = 1'b0; pf = $random; pnear = $random;
            end
            4'h3: begin
                run_first = w[41:32]; run_count = w[26:16]; gap = w[3:0];
                for (k = run_first; k < run_first + run_count; k = k + 1) dst[k] = {64{1'bx}};
                repeat (gap + 1) @(negedge clk);
                running = 1'b1; done_n = 0;
                first = run_first; count = run_count; start = 1'b1; start_clk = clkn;
                @(negedge clk);
                start = 1'b0;
                if (run_count != 0 && busy !== 1'b1) fail("o_busy is not high in the clock after i_start");
                cyc = 0;
                while (done_n == 0 && cyc < 800 * run_count + 200) begin @(negedge clk); cyc = cyc + 1; end
                if (done_n == 0) begin
                    $display("FAIL: tb_vertex_unit (no o_done after %0d clocks for a run of %0d vertices starting at %0d)", cyc, run_count, run_first);
                    $finish;
                end
                running = 1'b0;
                @(negedge clk);
                if (busy !== 1'b0) fail("o_busy still high in the clock after o_done");
                tot_clk = tot_clk + cyc; tot_vert = tot_vert + run_count;
                if (run_count > 0 && cyc / run_count > max_clk) max_clk = cyc / run_count;
                n_runs = n_runs + 1;
                for (k = 0; k < run_count; k = k + 1) begin
                    e = expw[ep]; gt = dst[run_first + k]; ep = ep + 1;
                    n_vert = n_vert + 1;
                    if (e[48]) n_flag = n_flag + 1;
                    if (e[48] ? (gt[63:48] !== e[63:48]) : (gt !== e)) begin
                        if (bad_vert < 8) $display("tb_vertex_unit: vertex %0d (run %0d): got %h, expected %h%s", run_first + k, n_runs, gt, e, e[48] ? " (NEAR: flags only)" : "");
                        bad_vert = bad_vert + 1;
                    end
                end
                repeat (10) @(negedge clk);                       // a late write or a second o_done is caught by the monitors
            end
            default: ;
            endcase
            pc = pc + 1;
            w = script[pc];
        end
        repeat (20) @(negedge clk);
        fin = 1'b1;
        if (bad_vert != 0) $display("FAIL: tb_vertex_unit (%0d of %0d vertices wrong)", bad_vert, n_vert);
        else if (errors != 0) $display("FAIL: tb_vertex_unit (%0d protocol errors)", errors);
        else $display("PASS: tb_vertex_unit (%0d runs, %0d vertices bit-exact, %0d of them NEAR; %0d clocks per vertex on average, %0d at most)",
                      n_runs, n_vert, n_flag, tot_vert ? tot_clk / tot_vert : 0, max_clk);
        $finish;
    end

    initial begin #(2_000_000_000); $display("FAIL: tb_vertex_unit watchdog timeout"); $finish; end
endmodule
`default_nettype wire
