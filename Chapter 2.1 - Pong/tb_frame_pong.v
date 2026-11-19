// tb_frame_pong.v -- frame-dump testbench for pong_render (Chapter 2.1).
//
// One 640x480 vga_timing drives four pong_render instances, each with fixed
// ball and paddle positions (logical 320x240 coordinates), and one frame of
// each is written:
//   build/frame_pong_start.ppm    power-up positions: ball and paddles centred
//   build/frame_pong_edges.ppm    ball in the top-left corner, paddles at the
//                                 top and bottom limits
//   build/frame_pong_net.ppm      ball across the net, on the edge of a dash
//   build/frame_pong_overlap.ppm  ball overlapping the left paddle (ball wins)
// ref_pong.py draws the expected frames and compare.py checks them.
// While the frames run, this file also checks that the color is black
// whenever de is low.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_pong.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_pong.py build
//      python3 sim/py/compare.py --set build pong
`timescale 1ns/1ps
`default_nettype none

// One pong_render with fixed positions, its frame_dump and a blanking check.
module tb_frame_pong_one #(
    parameter NAME = "?", parameter FILE = "build/frame.ppm",
    parameter BX = 0, parameter BY = 0, parameter LP = 0, parameter RP = 0
) (
    input  wire       i_clk, i_vsync, i_de,
    input  wire [9:0] i_x, i_y,
    output wire       o_done, o_error,
    output reg        o_blank_err = 1'b0
);
    wire [2:0] r, g, b;
    pong_render u_render (
        .i_x(i_x), .i_y(i_y), .i_de(i_de),
        .i_ball_x(BX[9:0]), .i_ball_y(BY[9:0]), .i_lpad_y(LP[9:0]), .i_rpad_y(RP[9:0]),
        .o_red(r), .o_grn(g), .o_blu(b));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0), .FILE(FILE)) u_dump (
        .i_clk(i_clk), .i_vsync(i_vsync), .i_de(i_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(o_done), .o_error(o_error));
    always @(negedge i_clk)
        if (!i_de && {r, g, b} !== 9'd0 && !o_blank_err) begin
            $display("  %0s: color not black while de is low (x=%0d y=%0d)", NAME, i_x, i_y);
            o_blank_err = 1'b1;
        end
endmodule

module tb_frame_pong;
    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    // Positions in logical pixels. ref_pong.py uses the same numbers: keep them in step.
    wire [3:0] done, error, blank_err;
    tb_frame_pong_one #(.NAME("start"), .FILE("build/frame_pong_start.ppm"),
                        .BX(158), .BY(118), .LP(104), .RP(104)) u_start (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_x(x), .i_y(y),
        .o_done(done[0]), .o_error(error[0]), .o_blank_err(blank_err[0]));
    tb_frame_pong_one #(.NAME("edges"), .FILE("build/frame_pong_edges.ppm"),
                        .BX(0), .BY(0), .LP(0), .RP(208)) u_edges (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_x(x), .i_y(y),
        .o_done(done[1]), .o_error(error[1]), .o_blank_err(blank_err[1]));
    tb_frame_pong_one #(.NAME("net"), .FILE("build/frame_pong_net.ppm"),
                        .BX(157), .BY(5), .LP(207), .RP(1)) u_net (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_x(x), .i_y(y),
        .o_done(done[2]), .o_error(error[2]), .o_blank_err(blank_err[2]));
    tb_frame_pong_one #(.NAME("overlap"), .FILE("build/frame_pong_overlap.ppm"),
                        .BX(6), .BY(100), .LP(90), .RP(33)) u_overlap (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_x(x), .i_y(y),
        .o_done(done[3]), .o_error(error[3]), .o_blank_err(blank_err[3]));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_pong.vcd");
            $dumpvars(0, tb_frame_pong);
        end
    end

    initial begin : watchdog
        #(40 * 3 * 420000);
        $display("FAIL: tb_frame_pong watchdog timeout (no complete frame: is vsync toggling?)");
        $finish;
    end

    initial begin
        wait (&done);
        #1;
        if (|error)
            $display("FAIL: tb_frame_pong (frame errors, see frame_dump lines above)");
        else if (|blank_err)
            $display("FAIL: tb_frame_pong (color outside the visible area, see above)");
        else
            $display("PASS: tb_frame_pong (4 frames written to build/frame_pong_*.ppm)");
        $finish;
    end
endmodule
`default_nettype wire
