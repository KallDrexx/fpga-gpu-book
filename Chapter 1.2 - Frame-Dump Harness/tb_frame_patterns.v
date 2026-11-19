// tb_frame_patterns.v -- frame-dump testbench for test_pattern (Chapter 1.2).
//
// One vga_timing drives four test_pattern instances (checkerboard, gradient,
// border, color bars). A frame_dump on each writes the first complete frame
// to build/frame_patterns_<label>.ppm.
//
// This testbench checks the *shape* of each frame (640 de pixels per line,
// 480 lines, no X), checks that every output is black whenever de is low,
// and prints PASS once all four files are written. Whether
// the *pixels* are right is decided by compare.py against the Python
// reference images; `make frame` runs both steps.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_patterns.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_patterns.py build
//      python3 sim/py/compare.py --set build patterns
`timescale 1ns/1ps
`default_nettype none

module tb_frame_patterns;
    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;

    vga_timing u_vga (
        .i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y)
    );

    // ---------------- the four patterns, each with its own dumper ----------------
    // To dump a different design, replace these instances with your DUT and
    // point one frame_dump at each video output you want to see.
    localparam N = 4;
    wire [N-1:0] done, error;
    wire [2:0]   r0, g0, b0, r1, g1, b1, r2, g2, b2, r3, g3, b3;

    test_pattern u_checker  (.i_x(x), .i_y(y), .i_de(de), .i_pattern(2'd0), .o_red(r0), .o_grn(g0), .o_blu(b0));
    test_pattern u_gradient (.i_x(x), .i_y(y), .i_de(de), .i_pattern(2'd1), .o_red(r1), .o_grn(g1), .o_blu(b1));
    test_pattern u_border   (.i_x(x), .i_y(y), .i_de(de), .i_pattern(2'd2), .o_red(r2), .o_grn(g2), .o_blu(b2));
    test_pattern u_bars     (.i_x(x), .i_y(y), .i_de(de), .i_pattern(2'd3), .o_red(r3), .o_grn(g3), .o_blu(b3));

    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .FILE("build/frame_patterns_checker.ppm")) u_dump_checker (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r0), .i_grn(g0), .i_blu(b0), .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .FILE("build/frame_patterns_gradient.ppm")) u_dump_gradient (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r1), .i_grn(g1), .i_blu(b1), .o_done(done[1]), .o_error(error[1]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .FILE("build/frame_patterns_border.ppm")) u_dump_border (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r2), .i_grn(g2), .i_blu(b2), .o_done(done[2]), .o_error(error[2]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .FILE("build/frame_patterns_bars.ppm")) u_dump_bars (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r3), .i_grn(g3), .i_blu(b3), .o_done(done[3]), .o_error(error[3]));

    // ---------------- blanking: color must be black whenever de is low ----------------
    // frame_dump only looks at de pixels, so it cannot see color in the porches.
    reg blank_err = 1'b0;
    always @(posedge clk)
        if (de === 1'b0 && (|{r0, g0, b0, r1, g1, b1, r2, g2, b2, r3, g3, b3}) !== 1'b0) begin
            if (!blank_err)
                $display("tb_frame_patterns: color not black while de is low (x=%0d, y=%0d)", x, y);
            blank_err = 1'b1;
        end

    // ---------------- run, watchdog, verdict ----------------
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_patterns.vcd");
            $dumpvars(0, tb_frame_patterns);
        end
    end

    // Up to one frame to reach the first vsync, one frame to capture, plus margin.
    initial begin : watchdog
        #(40 * 3 * 420000);
        $display("FAIL: tb_frame_patterns watchdog timeout (no complete frame: is vsync toggling?)");
        $finish;
    end

    initial begin
        wait (&done);
        #1;
        if (|error)
            $display("FAIL: tb_frame_patterns (frame errors, see frame_dump lines above)");
        else if (blank_err)
            $display("FAIL: tb_frame_patterns (color not black outside de)");
        else
            $display("PASS: tb_frame_patterns (4 frames of 640x480 written to build/frame_patterns_*.ppm)");
        $finish;
    end
endmodule
`default_nettype wire
