// tb_tri_shade.v -- self-checking testbench for the interpolating rasterizer (Chapter 6.3).
//
// Two instances of tri_raster #(.SHADE(1)): one for a 64x64 frame (3,000 triangles from
// sim/interp_small.hex) and one for the real 320x240 frame (100 triangles from sim/interp_big.hex,
// with 300-pixel spans and corners at the int16 limits). For every triangle the pixels the DUT
// streams out between i_start and o_done must be exactly those of raster_model.shade_pixels():
// the same set of (x, y), none twice, none outside the clamped bounding box, and for a shaded
// triangle every pixel carries the model's z and intensity. The check is a count plus two hashes
// of the (x, y, z, i) tuples (a sum and an xor, see raster_model.shade_pixel_hash), so a single
// wrong z or i anywhere in a triangle fails it. A few flat triangles (i_shaded = 0) are mixed in:
// they must give the Chapter 4.6 pixels and a setup time that does not grow.
//
// Run from build/sim/:   vvp -n tb_tri_shade.vvp          (about 2 minutes)
// Prints one PASS or FAIL line at the end (plus the first few problems before it).
`timescale 1ns/1ps
`default_nettype none

module tb_tri_shade;
    localparam N_SMALL = 3000, N_BIG = 100;          // lines in the two files (ref_interp.py prints them)

    reg         i_clk = 0;
    reg         start_a = 0, start_b = 0;
    reg  [15:0] i_x0 = 0, i_y0 = 0, i_x1 = 0, i_y1 = 0, i_x2 = 0, i_y2 = 0;
    reg  [15:0] i_z0 = 0, i_z1 = 0, i_z2 = 0;
    reg   [7:0] i_i0 = 0, i_i1 = 0, i_i2 = 0;
    reg         i_shaded = 0;
    wire        busy_a, valid_a, done_a, busy_b, valid_b, done_b;
    wire [15:0] x_a, y_a, z_a, x_b, y_b, z_b;
    wire  [4:0] i_a, i_b;

    tri_raster #(.SCREEN_W(64), .SCREEN_H(64), .SHADE(1)) dut_a (
        .i_clk(i_clk), .i_start(start_a),
        .i_x0(i_x0), .i_y0(i_y0), .i_x1(i_x1), .i_y1(i_y1), .i_x2(i_x2), .i_y2(i_y2),
        .i_shaded(i_shaded), .i_z0(i_z0), .i_z1(i_z1), .i_z2(i_z2), .i_i0(i_i0), .i_i1(i_i1), .i_i2(i_i2),
        .o_busy(busy_a), .o_valid(valid_a), .o_x(x_a), .o_y(y_a), .o_z(z_a), .o_i(i_a), .o_done(done_a));
    tri_raster #(.SCREEN_W(320), .SCREEN_H(240), .SHADE(1)) dut_b (
        .i_clk(i_clk), .i_start(start_b),
        .i_x0(i_x0), .i_y0(i_y0), .i_x1(i_x1), .i_y1(i_y1), .i_x2(i_x2), .i_y2(i_y2),
        .i_shaded(i_shaded), .i_z0(i_z0), .i_z1(i_z1), .i_z2(i_z2), .i_i0(i_i0), .i_i1(i_i1), .i_i2(i_i2),
        .o_busy(busy_b), .o_valid(valid_b), .o_x(x_b), .o_y(y_b), .o_z(z_b), .o_i(i_b), .o_done(done_b));

    reg sel = 0;                                     // 0: the 64x64 DUT, 1: the 320x240 one
    wire        o_busy  = sel ? busy_b  : busy_a;
    wire        o_valid = sel ? valid_b : valid_a;
    wire        o_done  = sel ? done_b  : done_a;
    wire [15:0] o_x     = sel ? x_b : x_a, o_y = sel ? y_b : y_a, o_z = sel ? z_b : z_a;
    wire  [4:0] o_i     = sel ? i_b : i_a;

    always #10 i_clk = ~i_clk;                       // 50 MHz; everything is driven and sampled on the falling edge

    reg [263:0] vec_s [0:N_SMALL-1];
    reg [263:0] vec_b [0:N_BIG-1];
    integer     stamp [0:320*240-1];                 // which triangle last wrote each pixel

    integer max_over [0:1];
    integer n_err, n_bad_tri, total_pix, total_clocks, max_clocks, n_shaded, n_flat;
    integer i, tri_id, line_no, W, H;
    reg [1023:0] dir;

    integer cx0, cy0, cx1, cy1, cx2, cy2, cz0, cz1, cz2;
    integer bx0, by0, bx1, by1;
    integer exp_cnt, got_cnt, cells, limit, cyc, first_px, flat;
    reg [31:0] exp_hash, exp_xhash, got_hash, got_xhash;
    reg        tri_bad, fin;
    integer    nshow;
    reg [15:0] sx [0:7], sy [0:7], sz [0:7];
    reg  [4:0] si [0:7];

    function integer imin(input integer a, input integer b); imin = (a < b) ? a : b; endfunction
    function integer imax(input integer a, input integer b); imax = (a > b) ? a : b; endfunction

    // the same two hashes as raster_model.shade_pixel_hash
    reg [31:0] ha, hb, hc, hm, hn;
    task phash(input integer x, input integer y, input integer z, input integer i, input integer w);
        begin
            ha = (y * w + x + 1) * 32'h9E3779B1;
            hb = (z + 1) * 32'h85EBCA6B;
            hc = (i + 1) * 32'hC2B2AE35;
            hm = (ha ^ hb) + hc;
            hm = hm ^ (hm >> 15);
            hm = hm * 32'h2C1B3C6D;
            hm = hm ^ (hm >> 12);
            hn = (ha + hb) ^ hc;
            hn = hn * 32'h297A2D39;
            hn = hn ^ (hn >> 13);
        end
    endtask

    task complain(input [8*44-1:0] what);
        begin
            tri_bad = 1;
            n_err = n_err + 1;
            if (n_err <= 12)
                $display("  %0s %0d (%0d,%0d,z%0d) (%0d,%0d,z%0d) (%0d,%0d,z%0d) %0s: %0s", sel ? "interp_big.hex line" : "interp_small.hex line", line_no,
                         cx0, cy0, cz0, cx1, cy1, cz1, cx2, cy2, cz2, flat ? "flat" : "shaded", what);
        end
    endtask

    task run_tri(input [263:0] word);
        integer x, y, idx, k;
        reg [15:0] zz;
        reg  [4:0] ii;
        begin
            if (n_bad_tri >= 20) begin               // enough evidence: stop early
                $display("FAIL: tb_tri_shade (%0d problems in %0d triangles so far, stopped at triangle %0d)", n_err, n_bad_tri, line_no);
                $finish;
            end
            cx0 = $signed(word[263:248]); cy0 = $signed(word[247:232]);
            cx1 = $signed(word[231:216]); cy1 = $signed(word[215:200]);
            cx2 = $signed(word[199:184]); cy2 = $signed(word[183:168]);
            cz0 = word[167:152]; cz1 = word[151:136]; cz2 = word[135:120];
            flat = (word[95:88] == 8'd0);
            exp_cnt   = word[87:64];
            exp_hash  = word[63:32];
            exp_xhash = word[31:0];
            bx0 = imax(imin(cx0, imin(cx1, cx2)), 0);  bx1 = imin(imax(cx0, imax(cx1, cx2)), W - 1);
            by0 = imax(imin(cy0, imin(cy1, cy2)), 0);  by1 = imin(imax(cy0, imax(cy1, cy2)), H - 1);
            cells = (bx0 > bx1 || by0 > by1) ? 0 : (bx1 - bx0 + 1) * (by1 - by0 + 1);
            limit = 2 * cells + 1000;                // one test per clock plus any sensible setup passes with room to spare
            tri_id = tri_id + 1;
            tri_bad = 0; got_cnt = 0; got_hash = 0; got_xhash = 0; fin = 0; cyc = 0; first_px = -1; nshow = 0;
            if (flat) n_flat = n_flat + 1; else n_shaded = n_shaded + 1;

            @(negedge i_clk);
            if (o_done !== 1'b0 || o_valid !== 1'b0) complain("o_done or o_valid in the clock after o_done");
            i_x0 = word[263:248]; i_y0 = word[247:232]; i_x1 = word[231:216];
            i_y1 = word[215:200]; i_x2 = word[199:184]; i_y2 = word[183:168];
            i_z0 = word[167:152]; i_z1 = word[151:136]; i_z2 = word[135:120];
            i_i0 = word[119:112]; i_i1 = word[111:104]; i_i2 = word[103:96];
            i_shaded = !flat;
            if (sel) start_b = 1; else start_a = 1;
            @(negedge i_clk);
            start_a = 0; start_b = 0;                // the DUT must have latched everything
            i_x0 = $random; i_y0 = $random; i_x1 = $random; i_y1 = $random; i_x2 = $random; i_y2 = $random;
            i_z0 = $random; i_z1 = $random; i_z2 = $random; i_i0 = $random; i_i1 = $random; i_i2 = $random;
            i_shaded = $random;

            while (!fin) begin
                if (cyc == 0 && o_busy !== 1'b1) complain("o_busy not high the clock after i_start");
                if (o_valid === 1'b1) begin
                    x = $signed(o_x); y = $signed(o_y);
                    if (first_px < 0) first_px = cyc;
                    if ((^o_z) === 1'bx || (^o_i) === 1'bx) complain("o_z or o_i is X on a valid pixel");
                    zz = flat ? 16'd0 : o_z;
                    ii = flat ? 5'd0 : o_i;
                    if (nshow < 8) begin sx[nshow] = o_x; sy[nshow] = o_y; sz[nshow] = zz; si[nshow] = ii; nshow = nshow + 1; end
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
                            got_cnt = got_cnt + 1;
                            phash(x, y, zz, ii, W);
                            got_hash = got_hash + hm;
                            got_xhash = got_xhash ^ hn;
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
            if (cells > 0 && cyc - cells > max_over[flat]) max_over[flat] = cyc - cells;
            if (flat && cyc > cells + 2 * (by1 - by0 + 1) + 60 && cells > 0) complain("a flat triangle took a shaded setup time");
            if (!tri_bad) begin
                if (got_cnt != exp_cnt) begin
                    complain("wrong pixel count");
                    if (n_err <= 12) $display("    got %0d pixels, expected %0d", got_cnt, exp_cnt);
                end else if (got_hash !== exp_hash || got_xhash !== exp_xhash) begin
                    complain("right count, wrong pixels or wrong z/i");
                end
            end
            total_pix = total_pix + got_cnt;
            if (tri_bad) begin
                n_bad_tri = n_bad_tri + 1;
                if (n_bad_tri == 1) begin
                    $display("  first pixels delivered (x y z i); compare `ref_interp.py --show %0d%0s`:", line_no, sel ? " --big" : "");
                    for (k = 0; k < nshow; k = k + 1) $display("    %0d %0d %0d %0d", sx[k], sy[k], sz[k], si[k]);
                end
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("dir=%s", dir)) dir = "../../sim/";
        for (i = 0; i < N_SMALL; i = i + 1) vec_s[i] = {264{1'bx}};
        for (i = 0; i < N_BIG; i = i + 1) vec_b[i] = {264{1'bx}};
        $readmemh({dir, "interp_small.hex"}, vec_s);
        $readmemh({dir, "interp_big.hex"}, vec_b);
        if ((^vec_s[N_SMALL-1]) === 1'bx || (^vec_b[N_BIG-1]) === 1'bx) begin
            $display("FAIL: tb_tri_shade cannot read its files (run `.venv/bin/python sim/py/ref_interp.py sim`, then run from build/sim)");
            $finish;
        end
        for (i = 0; i < 320 * 240; i = i + 1) stamp[i] = -1;
        n_err = 0; n_bad_tri = 0; total_pix = 0; total_clocks = 0; max_clocks = 0; max_over[0] = 0; max_over[1] = 0; n_shaded = 0; n_flat = 0; tri_id = 0;

        repeat (4) @(negedge i_clk);
        if (busy_a !== 1'b0 || valid_a !== 1'b0 || busy_b !== 1'b0 || valid_b !== 1'b0) begin
            $display("FAIL: tb_tri_shade o_busy and o_valid must be low while idle");
            $finish;
        end

        sel = 0; W = 64; H = 64;
        for (i = 0; i < N_SMALL; i = i + 1) begin line_no = i; run_tri(vec_s[i]); end
        sel = 1; W = 320; H = 240;
        for (i = 0; i < N_BIG; i = i + 1) begin line_no = i; run_tri(vec_b[i]); end

        if (n_err == 0)
            $display("PASS: tb_tri_shade (%0d shaded + %0d flat triangles, %0d pixels matched in x, y, z and intensity, clocks beyond one per box pixel: at most %0d shaded, %0d flat; worst triangle %0d clocks)",
                     n_shaded, n_flat, total_pix, max_over[0], max_over[1], max_clocks);
        else
            $display("FAIL: tb_tri_shade (%0d problems in %0d triangles)", n_err, n_bad_tri);
        $finish;
    end

    initial begin : watchdog
        #(2_000_000_000);
        $display("FAIL: tb_tri_shade watchdog timeout");
        $finish;
    end
endmodule
`default_nettype wire
