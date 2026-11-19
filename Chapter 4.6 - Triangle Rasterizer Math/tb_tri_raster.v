// tb_tri_raster.v -- self-checking testbench for tri_raster (Chapter 4.6).
//
// Part 1: 10,000 triangles for a 64x64 frame (sim/tri_vectors.hex). For each one, the pixels the
//         DUT streams out must be exactly the pixels of host/raster_model.py: same count, same
//         set (checked by a hash), none twice, none outside the clamped bounding box.
// Part 2: 24 grids of triangles that share edges (sim/tri_grid.hex). After each grid, every
//         pixel of the frame must have been covered exactly once.
//
// Run from build/sim/:   vvp -n tb_tri_raster.vvp          (about 1.5 minutes)
// Prints one PASS or FAIL line at the end (plus the first few problems before it).
`timescale 1ns/1ps
`default_nettype none

module tb_tri_raster;
    localparam W = 64, H = 64;
    localparam N_VEC = 10000, N_GRID = 7434;        // lines in the two files (ref_tri.py prints them)

    reg         i_clk = 0;
    reg         i_start = 0;
    reg  [15:0] i_x0 = 0, i_y0 = 0, i_x1 = 0, i_y1 = 0, i_x2 = 0, i_y2 = 0;
    wire        o_busy, o_valid, o_done;
    wire [15:0] o_x, o_y;

    tri_raster #(.SCREEN_W(W), .SCREEN_H(H)) dut (
        .i_clk(i_clk), .i_start(i_start),
        .i_x0(i_x0), .i_y0(i_y0), .i_x1(i_x1), .i_y1(i_y1), .i_x2(i_x2), .i_y2(i_y2),
        .o_busy(o_busy), .o_valid(o_valid), .o_x(o_x), .o_y(o_y), .o_done(o_done));

    always #10 i_clk = ~i_clk;                      // 50 MHz; everything is driven and sampled on the falling edge

    reg [143:0] vec  [0:N_VEC-1];
    reg [143:0] gvec [0:N_GRID-1];
    integer     stamp [0:W*H-1];                    // which triangle last wrote each pixel
    integer     gcnt  [0:W*H-1];                    // part 2: how often each pixel was covered

    integer n_vec, n_grid, n_err, n_bad_tri, total_pix, total_clocks, max_clocks;
    integer i, k, tri_id, group, line_no;           // line_no: line of the current file, from 0
    reg     in_grid;
    reg [1023:0] dir;
    reg [1023:0] fname;

    // Current triangle
    integer cx0, cy0, cx1, cy1, cx2, cy2;
    integer bx0, by0, bx1, by1;                     // clamped bounding box, inclusive (empty if bx0 > bx1)
    integer exp_cnt, got_cnt, cells, limit, cyc;
    reg [31:0] exp_hash, got_hash;
    reg        tri_bad, fin, shown;

    function integer imin(input integer a, input integer b); imin = (a < b) ? a : b; endfunction
    function integer imax(input integer a, input integer b); imax = (a > b) ? a : b; endfunction

    function [31:0] phash(input integer idx);       // the same as raster_model.pixel_hash
        reg [31:0] m;
        begin
            m = (idx + 1) * 32'h9E3779B1;
            phash = m ^ (m >> 15);
        end
    endfunction

    task complain(input [8*40-1:0] what);
        begin
            tri_bad = 1;
            n_err = n_err + 1;
            if (n_err <= 12)
                $display("  %0s %0d (%0d,%0d) (%0d,%0d) (%0d,%0d): %0s", in_grid ? "tri_grid.hex line" : "triangle",
                         line_no, cx0, cy0, cx1, cy1, cx2, cy2, what);
        end
    endtask

    // Draw one triangle and check it. With sig = 1 also check count and hash against the word.
    task run_tri(input [143:0] word, input sig);
        integer x, y, idx;
        begin
            cx0 = $signed(word[143:128]); cy0 = $signed(word[127:112]);
            cx1 = $signed(word[111:96]);  cy1 = $signed(word[95:80]);
            cx2 = $signed(word[79:64]);   cy2 = $signed(word[63:48]);
            exp_cnt  = word[47:32];
            exp_hash = word[31:0];
            bx0 = imax(imin(cx0, imin(cx1, cx2)), 0);  bx1 = imin(imax(cx0, imax(cx1, cx2)), W - 1);
            by0 = imax(imin(cy0, imin(cy1, cy2)), 0);  by1 = imin(imax(cy0, imax(cy1, cy2)), H - 1);
            cells = (bx0 > bx1 || by0 > by1) ? 0 : (bx1 - bx0 + 1) * (by1 - by0 + 1);
            limit = 2 * cells + 1000;                // one test per clock passes with room to spare
            tri_id = tri_id + 1;
            tri_bad = 0; got_cnt = 0; got_hash = 0; fin = 0; cyc = 0;

            @(negedge i_clk);
            if (o_done !== 1'b0 || o_valid !== 1'b0) complain("o_done or o_valid in the clock after o_done");
            i_x0 = word[143:128]; i_y0 = word[127:112]; i_x1 = word[111:96];
            i_y1 = word[95:80];   i_x2 = word[79:64];   i_y2 = word[63:48];
            i_start = 1;
            @(negedge i_clk);
            i_start = 0;                             // the DUT must have latched the corners
            i_x0 = $random; i_y0 = $random; i_x1 = $random; i_y1 = $random; i_x2 = $random; i_y2 = $random;

            while (!fin) begin
                if (cyc == 0 && o_busy !== 1'b1) complain("o_busy not high the clock after i_start");
                if (o_valid === 1'b1) begin
                    x = $signed(o_x); y = $signed(o_y);
                    if (x < bx0 || x > bx1 || y < by0 || y > by1) begin
                        complain("pixel outside the clamped bounding box");
                        if (n_err <= 12) $display("    pixel (%0d,%0d), box (%0d,%0d)-(%0d,%0d)", x, y, bx0, by0, bx1, by1);
                    end else begin
                        idx = y * W + x;
                        if (stamp[idx] == tri_id) begin
                            complain("pixel delivered twice");
                            if (n_err <= 12) $display("    pixel (%0d,%0d)", x, y);
                        end else begin
                            stamp[idx] = tri_id;
                            gcnt[idx] = gcnt[idx] + 1;
                            got_cnt = got_cnt + 1;
                            got_hash = got_hash + phash(idx);
                        end
                    end
                end else if (o_valid !== 1'b0) complain("o_valid is X");
                if (o_done === 1'b1) begin
                    fin = 1;
                    if (o_busy !== 1'b0) complain("o_busy still high with o_done");
                    if (o_valid !== 1'b0) complain("o_valid high with o_done");
                end else begin
                    cyc = cyc + 1;
                    if (cyc > limit) begin
                        complain("no o_done in time");
                        fin = 1;
                    end else
                        @(negedge i_clk);
                end
            end
            total_clocks = total_clocks + cyc + 2;
            if (cyc + 2 > max_clocks) max_clocks = cyc + 2;
            if (sig && !tri_bad) begin
                if (got_cnt != exp_cnt) begin
                    complain("wrong pixel count");
                    if (n_err <= 12) $display("    got %0d pixels, expected %0d", got_cnt, exp_cnt);
                end else if (got_hash !== exp_hash) begin
                    complain("right count, wrong pixels");
                end
            end
            if (sig) total_pix = total_pix + got_cnt;
            if (tri_bad) begin
                n_bad_tri = n_bad_tri + 1;
                if (!shown) begin                    // draw the first bad one
                    shown = 1;
                    if (in_grid) $display("  DUT coverage of tri_grid.hex line %0d:", line_no);
                    else $display("  DUT coverage of triangle %0d (compare `ref_tri.py --show %0d`):", line_no, line_no);
                    for (y = 0; y < H; y = y + 1) begin
                        for (x = 0; x < W; x = x + 1) $write("%0s", (stamp[y*W + x] == tri_id) ? "#" : ".");
                        $write("\n");
                    end
                end
            end
        end
    endtask

    task check_group;
        integer p, bad;
        begin
            bad = 0;
            for (p = 0; p < W * H; p = p + 1) begin
                if (gcnt[p] != 1) begin
                    bad = bad + 1;
                    if (bad <= 3 && n_err < 12) $display("  grid %0d: pixel (%0d,%0d) covered %0d times", group, p % W, p / W, gcnt[p]);
                end
                gcnt[p] = 0;
            end
            if (bad != 0) n_err = n_err + 1;
            if (bad != 0) n_bad_tri = n_bad_tri + 1;
        end
    endtask

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = "../../sim/";
        for (i = 0; i < N_VEC; i = i + 1) vec[i] = {144{1'bx}};
        for (i = 0; i < N_GRID; i = i + 1) gvec[i] = {144{1'bx}};
        $readmemh({dir, "tri_vectors.hex"}, vec);
        $readmemh({dir, "tri_grid.hex"}, gvec);
        n_vec = N_VEC; n_grid = N_GRID;
        if ((^vec[N_VEC-1]) === 1'bx || (^gvec[N_GRID-1]) === 1'bx) begin
            $display("FAIL: tb_tri_raster cannot read its files (run `.venv/bin/python sim/py/ref_tri.py sim`, then run from build/sim)");
            $finish;
        end
        for (i = 0; i < W * H; i = i + 1) stamp[i] = -1;
        n_err = 0; n_bad_tri = 0; total_pix = 0; total_clocks = 0; max_clocks = 0; tri_id = 0; shown = 0;

        repeat (4) @(negedge i_clk);
        if (o_busy !== 1'b0 || o_valid !== 1'b0) begin
            $display("FAIL: tb_tri_raster o_busy and o_valid must be low while idle");
            $finish;
        end

        in_grid = 0;
        for (i = 0; i < n_vec; i = i + 1) begin line_no = i; run_tri(vec[i], 1); end

        group = 0;
        for (i = 0; i < W * H; i = i + 1) gcnt[i] = 0;     // part 2 counts from zero
        in_grid = 1;
        for (i = 0; i < n_grid; i = i + 1) begin
            if (gvec[i][47:32] != group) begin check_group; group = gvec[i][47:32]; end
            line_no = i;
            run_tri(gvec[i], 0);
        end
        check_group;

        if (n_err == 0)
            $display("PASS: tb_tri_raster (%0d triangles, %0d pixels matched, %0d grid triangles in %0d grids tile the frame once, worst triangle %0d clocks)",
                     n_vec, total_pix, n_grid, group + 1, max_clocks);
        else
            $display("FAIL: tb_tri_raster (%0d problems in %0d triangles/grids, first ones above)", n_err, n_bad_tri);
        $finish;
    end

    initial begin
        #4_000_000_000;                              // 4 s simulated
        $display("FAIL: tb_tri_raster watchdog timeout");
        $finish;
    end

    initial if ($test$plusargs("vcd")) begin
        $dumpfile("tb_tri_raster.vcd");
        $dumpvars(0, tb_tri_raster);
    end
endmodule
`default_nettype wire
