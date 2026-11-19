// tb_frame_motion.v -- frame-dump testbench for bouncing_square (Chapter 1.3).
//
// Full-size part (640x480): two bouncing_square instances, each dumped on two
// consecutive frames with frame_dump SKIP(0) and SKIP(1):
//   build/frame_motion_free1.ppm,   build/frame_motion_free2.ppm
//       square in open space; frame 2 is frame 1 moved by exactly (DX, DY)
//   build/frame_motion_bounce1.ppm, build/frame_motion_bounce2.ppm
//       square near the bottom-right corner; in frame 2 it touches both edges
// ref_motion.py draws the expected frames and compare.py checks them.
//
// Small-screen part: two more instances on a tiny 48x36 screen (its own
// vga_timing with small parameters) run for about 550 frames and about 150
// bounces, while the big frames are captured.
//
// For every instance, on every clock, a model of the bounce rule in this file
// checks:
//   * o_sq_x/o_sq_y equal the model whenever the beam is in the visible lines
//     (y < V_VISIBLE). The model advances once per frame, at the start of
//     vertical blanking, so a position that changes mid-frame, moves by the
//     wrong step or bounces one step late or early fails here.
//   * the color is black whenever de is low;
//   * (small screens only) every pixel is white inside the square, black outside.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_motion.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_motion.py build
//      python3 sim/py/compare.py --set build motion
`timescale 1ns/1ps
`default_nettype none

// ---------------------------------------------------------------------------
// Checker: a behavioral model of one bouncing square, compared every clock.
// ---------------------------------------------------------------------------
module tb_frame_motion_check #(
    parameter NAME = "?",
    parameter H_VISIBLE = 640, parameter V_VISIBLE = 480,
    parameter SIZE = 32, parameter DX = 3, parameter DY = 2,
    parameter START_X = 100, parameter START_Y = 80,
    parameter CHECK_PIXELS = 0
) (
    input wire       i_clk,
    input wire [9:0] i_x, i_y,
    input wire       i_de,
    input wire [2:0] i_red, i_grn, i_blu,
    input wire [9:0] i_sq_x, i_sq_y
);
    integer mx = START_X, my = START_Y;  // model position
    integer right = 1, down = 1;         // model direction
    integer frames = 0, bounces = 0, errors = 0;
    integer fx = START_X, fy = START_Y;  // DUT position latched at the top of the frame
    integer cyc = 0;
    reg     in_sq, moved = 0;

    task fail(input [8*100-1:0] msg);
        begin
            if (errors < 3)
                $display("  %0s: %0s (frame %0d, beam x=%0d y=%0d; DUT square at (%0d,%0d), expected (%0d,%0d))",
                         NAME, msg, frames, i_x, i_y, i_sq_x, i_sq_y, mx, my);
            errors = errors + 1;
        end
    endtask

    // Sample just before each rising edge, when everything has settled.
    always @(negedge i_clk) begin
        cyc = cyc + 1;
        if (cyc > 2) begin
            if ((^{i_x, i_y, i_de, i_red, i_grn, i_blu, i_sq_x, i_sq_y}) === 1'bx) begin
                fail("X/Z on an output (give every register an initial value)");
            end else begin
                // Start of vertical blanking: advance the model by one frame.
                if (i_x == 0 && i_y == V_VISIBLE) begin
                    frames = frames + 1;
                    if (right) begin
                        if (mx + DX >= H_VISIBLE - SIZE) begin mx = H_VISIBLE - SIZE; right = 0; bounces = bounces + 1; end
                        else mx = mx + DX;
                    end else begin
                        if (mx <= DX) begin mx = 0; right = 1; bounces = bounces + 1; end
                        else mx = mx - DX;
                    end
                    if (down) begin
                        if (my + DY >= V_VISIBLE - SIZE) begin my = V_VISIBLE - SIZE; down = 0; bounces = bounces + 1; end
                        else my = my + DY;
                    end else begin
                        if (my <= DY) begin my = 0; down = 1; bounces = bounces + 1; end
                        else my = my - DY;
                    end
                end
                // Top-left pixel: the position this frame is drawn with must
                // match the model. Remember it for the rest of the frame.
                if (i_x == 0 && i_y == 0) begin
                    if (i_sq_x != mx || i_sq_y != my)
                        fail("wrong position at the start of a frame (step, direction or bounce wrong?)");
                    fx = i_sq_x; fy = i_sq_y; moved = 0;
                end
                // While the picture is being drawn the position must not move.
                if (i_y < V_VISIBLE && !moved && (i_sq_x != fx || i_sq_y != fy)) begin
                    fail("position changed while the picture was being drawn (update only in vertical blanking)");
                    moved = 1;  // report once per frame
                end
                if (!i_de && {i_red, i_grn, i_blu} != 9'd0)
                    fail("color not black while de is low");
                // Pixels are checked against the position the DUT itself
                // reported at the top of the frame, so a drawing bug is
                // reported as such even when the motion is also wrong.
                if (CHECK_PIXELS && i_de) begin
                    in_sq = (i_x >= fx) && (i_x < fx + SIZE) && (i_y >= fy) && (i_y < fy + SIZE);
                    if ({i_red, i_grn, i_blu} != (in_sq ? 9'h1FF : 9'd0))
                        fail(in_sq ? "pixel inside the square is not white" : "pixel outside the square is not black");
                end
            end
        end
    end
endmodule

// ---------------------------------------------------------------------------
// Top level
// ---------------------------------------------------------------------------
module tb_frame_motion;
    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    // Full-size squares. ref_motion.py uses the same numbers: keep them in step.
    localparam SIZE = 32, DX = 3, DY = 2;
    localparam FREE_X   = 100, FREE_Y   = 80;   // open space
    localparam BOUNCE_X = 602, BOUNCE_Y = 444;  // reaches (608, 448) after 2 steps

    // Small screen: 48x36 visible, 56x40 total (2,240 clocks per frame).
    localparam SH_VIS = 48, SH_FP = 2, SH_SW = 4, SH_BP = 2;
    localparam SV_VIS = 36, SV_FP = 1, SV_SW = 2, SV_BP = 1;

    // ---------------- full-size design ----------------
    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    wire [2:0] r0, g0, b0, r1, g1, b1;
    wire [9:0] sx0, sy0, sx1, sy1;

    bouncing_square #(.H_VISIBLE(640), .V_VISIBLE(480), .SIZE(SIZE), .DX(DX), .DY(DY),
                      .START_X(FREE_X), .START_Y(FREE_Y)) u_free (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de),
        .o_red(r0), .o_grn(g0), .o_blu(b0), .o_sq_x(sx0), .o_sq_y(sy0));
    bouncing_square #(.H_VISIBLE(640), .V_VISIBLE(480), .SIZE(SIZE), .DX(DX), .DY(DY),
                      .START_X(BOUNCE_X), .START_Y(BOUNCE_Y)) u_bounce (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de),
        .o_red(r1), .o_grn(g1), .o_blu(b1), .o_sq_x(sx1), .o_sq_y(sy1));

    // Two consecutive frames of each: SKIP(0) is frame 1, SKIP(1) is frame 2.
    wire [3:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0), .FILE("build/frame_motion_free1.ppm")) u_dump_free1 (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r0), .i_grn(g0), .i_blu(b0), .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1), .FILE("build/frame_motion_free2.ppm")) u_dump_free2 (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r0), .i_grn(g0), .i_blu(b0), .o_done(done[1]), .o_error(error[1]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0), .FILE("build/frame_motion_bounce1.ppm")) u_dump_bounce1 (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r1), .i_grn(g1), .i_blu(b1), .o_done(done[2]), .o_error(error[2]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1), .FILE("build/frame_motion_bounce2.ppm")) u_dump_bounce2 (
        .i_clk(clk), .i_vsync(vs), .i_de(de), .i_red(r1), .i_grn(g1), .i_blu(b1), .o_done(done[3]), .o_error(error[3]));

    tb_frame_motion_check #(.NAME("free"), .SIZE(SIZE), .DX(DX), .DY(DY),
                            .START_X(FREE_X), .START_Y(FREE_Y)) u_chk_free (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_red(r0), .i_grn(g0), .i_blu(b0), .i_sq_x(sx0), .i_sq_y(sy0));
    tb_frame_motion_check #(.NAME("bounce"), .SIZE(SIZE), .DX(DX), .DY(DY),
                            .START_X(BOUNCE_X), .START_Y(BOUNCE_Y)) u_chk_bounce (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_red(r1), .i_grn(g1), .i_blu(b1), .i_sq_x(sx1), .i_sq_y(sy1));

    // ---------------- small-screen design: many frames, many bounces ----------------
    wire       shs, svs, sde;
    wire [9:0] sxx, syy;
    vga_timing #(.H_VISIBLE(SH_VIS), .H_FRONT(SH_FP), .H_SYNC(SH_SW), .H_BACK(SH_BP),
                 .V_VISIBLE(SV_VIS), .V_FRONT(SV_FP), .V_SYNC(SV_SW), .V_BACK(SV_BP)) u_vga_small (
        .i_clk(clk), .o_hsync(shs), .o_vsync(svs), .o_de(sde), .o_x(sxx), .o_y(syy));

    wire [2:0] r2, g2, b2, r3, g3, b3;
    wire [9:0] sx2, sy2, sx3, sy3;

    // A: steps that do not land exactly on the edges, so each bounce clamps.
    bouncing_square #(.H_VISIBLE(SH_VIS), .V_VISIBLE(SV_VIS), .SIZE(8), .DX(3), .DY(2),
                      .START_X(5), .START_Y(7)) u_small_a (
        .i_clk(clk), .i_x(sxx), .i_y(syy), .i_de(sde),
        .o_red(r2), .o_grn(g2), .o_blu(b2), .o_sq_x(sx2), .o_sq_y(sy2));
    // B: steps that land exactly on the edges (MAX_X = 44, MAX_Y = 32 = 8 * DY).
    bouncing_square #(.H_VISIBLE(SH_VIS), .V_VISIBLE(SV_VIS), .SIZE(4), .DX(1), .DY(4),
                      .START_X(0), .START_Y(4)) u_small_b (
        .i_clk(clk), .i_x(sxx), .i_y(syy), .i_de(sde),
        .o_red(r3), .o_grn(g3), .o_blu(b3), .o_sq_x(sx3), .o_sq_y(sy3));

    tb_frame_motion_check #(.NAME("small A"), .H_VISIBLE(SH_VIS), .V_VISIBLE(SV_VIS), .SIZE(8), .DX(3), .DY(2),
                            .START_X(5), .START_Y(7), .CHECK_PIXELS(1)) u_chk_a (
        .i_clk(clk), .i_x(sxx), .i_y(syy), .i_de(sde), .i_red(r2), .i_grn(g2), .i_blu(b2), .i_sq_x(sx2), .i_sq_y(sy2));
    tb_frame_motion_check #(.NAME("small B"), .H_VISIBLE(SH_VIS), .V_VISIBLE(SV_VIS), .SIZE(4), .DX(1), .DY(4),
                            .START_X(0), .START_Y(4), .CHECK_PIXELS(1)) u_chk_b (
        .i_clk(clk), .i_x(sxx), .i_y(syy), .i_de(sde), .i_red(r3), .i_grn(g3), .i_blu(b3), .i_sq_x(sx3), .i_sq_y(sy3));

    // ---------------- frame numbering ----------------
    // ref_motion.py assumes frame k (k = 1, 2) shows the square after k updates.
    // That holds when vga_timing starts at (0, 0): one start of blanking comes
    // before the first vsync. Check it, so a different start gives a clear message.
    reg vs_q = 1'b1, numbering_err = 1'b0, numbered = 1'b0;
    always @(posedge clk) begin
        if (!numbered && vs_q === 1'b1 && vs === 1'b0) begin
            numbered = 1'b1;
            if (u_chk_free.frames != 1) begin
                $display("  frame numbering: %0d starts of blanking before the first vsync, expected 1 (does vga_timing start at x=0, y=0?)",
                         u_chk_free.frames);
                numbering_err = 1'b1;
            end
        end
        vs_q = vs;
    end

    // ---------------- run, watchdog, verdict ----------------
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_motion.vcd");
            $dumpvars(0, tb_frame_motion);
        end
    end

    // Up to one frame to reach the first vsync, two frames to capture, plus margin.
    initial begin : watchdog
        #(40 * 4 * 420000);
        $display("FAIL: tb_frame_motion watchdog timeout (no complete frames: is vsync toggling?)");
        $finish;
    end

    integer errs;
    initial begin
        wait (&done);
        #1;
        errs = u_chk_free.errors + u_chk_bounce.errors + u_chk_a.errors + u_chk_b.errors;
        if (|error)
            $display("FAIL: tb_frame_motion (frame errors, see frame_dump lines above)");
        else if (numbering_err)
            $display("FAIL: tb_frame_motion (frame numbering, see above)");
        else if (errs != 0)
            $display("FAIL: tb_frame_motion (%0d failed checks, first ones above)", errs);
        else if (u_chk_a.bounces < 50 || u_chk_b.bounces < 50)
            $display("FAIL: tb_frame_motion (small screens ran too few frames: %0d and %0d bounces)", u_chk_a.bounces, u_chk_b.bounces);
        else
            $display("PASS: tb_frame_motion (4 frames written to build/frame_motion_*.ppm; %0d small-screen frames, %0d bounces checked)",
                     u_chk_a.frames, u_chk_a.bounces + u_chk_b.bounces);
        $finish;
    end
endmodule
`default_nettype wire
