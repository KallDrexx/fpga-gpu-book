// tb_frame_tiles.v -- frame-dump testbench for tile_engine (Chapter 2.3).
//
// A 640x480 vga_timing drives one tile_engine loaded with tiles.hex. The
// testbench fills the 40x30 tile map through the write port and dumps two
// frames:
//   build/frame_tiles_all.ppm    every tile 0-63 (cell i holds i mod 64)
//   build/frame_tiles_scene.ppm  a Snake screen: walls, a snake, food and a
//                                row of digit tiles, written during vertical
//                                blanking after the first frame
// ref_tiles.py draws the expected frames from the same tiles.hex and
// compare.py checks them pixel for pixel.
//
// While the frames run, this file also checks that
//   * o_hsync, o_vsync and o_de are i_hsync, i_vsync and i_de delayed by the
//     same number of clocks (the pipeline latency, at least 1), and
//   * the color is black whenever o_de is low.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_tiles.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_tiles.py build
//      python3 sim/py/compare.py --set build tiles
`timescale 1ns/1ps
`default_nettype none

module tb_frame_tiles;
    localparam COLS = 40, ROWS = 30, CELLS = COLS * ROWS;
    // Palette {r, g, b}, 3 bits each. ref_tiles.py uses the same values.
    localparam [8:0] PAL0 = 9'o013, PAL1 = 9'o521, PAL2 = 9'o264, PAL3 = 9'o776;

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    reg        wr_en = 1'b0;
    reg [10:0] wr_addr = 11'd0;
    reg [7:0]  wr_data = 8'd0;
    wire       t_hs, t_vs, t_de;
    wire [2:0] r, g, b;
    tile_engine #(.TILE_FILE("rtl/common/tiles.hex"),
                  .PAL0(PAL0), .PAL1(PAL1), .PAL2(PAL2), .PAL3(PAL3)) u_tiles (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .i_wr_en(wr_en), .i_wr_addr(wr_addr), .i_wr_data(wr_data),
        .o_hsync(t_hs), .o_vsync(t_vs), .o_de(t_de),
        .o_red(r), .o_grn(g), .o_blu(b));

    // Delay registers that start at 0 make o_vsync low for the first few
    // clocks, which frame_dump would count as a vsync. The dumps see vsync
    // only once map 1 is written, so they always capture frames 1 and 2.
    reg  armed = 1'b0;
    wire dump_vs = t_vs | ~armed;
    wire [1:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
                 .FILE("build/frame_tiles_all.ppm")) u_dump0 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1),
                 .FILE("build/frame_tiles_scene.ppm")) u_dump1 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[1]), .o_error(error[1]));

    // ---------------------------------------------------------------- screens
    // Must match ref_tiles.py: keep them in step.
    localparam EMPTY = 0, WALL = 1, BODY = 2, HEAD = 3, FOOD = 4;
    reg [7:0] screen [0:CELLS-1];
    integer i, c, rr;

    task put(input integer col, input integer row, input [7:0] t);
        screen[row * COLS + col] = t;
    endtask

    task make_all_screen;
        for (i = 0; i < CELLS; i = i + 1) screen[i] = i % 64;
    endtask

    task make_scene_screen;
        begin
            for (rr = 0; rr < ROWS; rr = rr + 1)
                for (c = 0; c < COLS; c = c + 1)
                    put(c, rr, (rr == 0 || rr == ROWS-1 || c == 0 || c == COLS-1) ? WALL : EMPTY);
            for (i = 0; i < 16; i = i + 1) put(2 + i, 2, 8'h30 + i);   // "0123456789:;<=>?"
            for (c = 5; c <= 20; c = c + 1) put(c, 14, BODY);           // a snake with two turns
            for (rr = 15; rr <= 20; rr = rr + 1) put(20, rr, BODY);
            for (c = 21; c <= 30; c = c + 1) put(c, 20, BODY);
            put(31, 20, HEAD);
            put(35, 20, FOOD);
            put(1, 1, FOOD); put(38, 28, FOOD);                          // corners inside the wall
        end
    endtask

    // Write the whole screen through the write port, one cell per clock.
    // Inputs change on the falling edge, away from the sampling edge.
    task write_screen;
        begin
            for (i = 0; i < CELLS; i = i + 1) begin
                @(negedge clk);
                wr_en = 1'b1; wr_addr = i; wr_data = screen[i];
            end
            @(negedge clk);
            wr_en = 1'b0;
        end
    endtask

    // ------------------------------------------------- sync alignment checks
    // Sampled on the falling edge. hist[k] holds {hs, vs, de} from k samples ago.
    // The latency is measured from the first rising edge of de (the start of
    // line 1) to the next rising edge of o_de; after that every sample is checked.
    localparam HMAX = 16;
    reg [2:0] hist [0:HMAX-1];
    integer   n_samp = 0, rise_in = -1, lat = -1;
    reg       prev_de = 1'b1, prev_t_de = 1'b1;
    reg       align_err = 1'b0, blank_err = 1'b0;
    integer   k;
    always @(negedge clk) begin
        for (k = HMAX-1; k > 0; k = k - 1) hist[k] = hist[k-1];
        hist[0] = {hs, vs, de};
        if (rise_in < 0 && de === 1'b1 && prev_de === 1'b0) rise_in = n_samp;
        if (rise_in >= 0 && lat < 0 && t_de === 1'b1 && prev_t_de === 1'b0) begin
            lat = n_samp - rise_in;
            if (lat < 1 || lat >= HMAX) begin
                $display("  tile_engine latency is %0d clocks; expected 1 to %0d (reads must be registered)",
                         lat, HMAX-1);
                align_err = 1'b1;
            end
        end
        if (lat >= 1 && lat < HMAX && !align_err && {t_hs, t_vs, t_de} !== hist[lat]) begin
            $display("  sync misaligned at x=%0d y=%0d: o_hsync/o_vsync/o_de = %b%b%b, inputs %0d clocks ago = %b",
                     x, y, t_hs, t_vs, t_de, lat, hist[lat]);
            align_err = 1'b1;
        end
        if (t_de === 1'b0 && {r, g, b} !== 9'd0 && !blank_err) begin
            $display("  color not black while o_de is low (x=%0d y=%0d)", x, y);
            blank_err = 1'b1;
        end
        prev_de = de; prev_t_de = t_de;
        n_samp = n_samp + 1;
    end

    // ------------------------------------------------------------------ main
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_tiles.vcd");
            $dumpvars(0, tb_frame_tiles);
        end
    end

    initial begin : watchdog
        #(40 * 4 * 420000);
        $display("FAIL: tb_frame_tiles watchdog timeout (no complete frame: is o_vsync toggling?)");
        $finish;
    end

    initial begin
        // Map 1 is written long before the first dumped frame starts.
        make_all_screen;
        write_screen;
        armed = 1'b1;
        // Map 2 is written after frame 1 ends, inside vertical blanking.
        wait (done[0]);
        make_scene_screen;
        write_screen;
        if (y < 480) begin
            $display("FAIL: tb_frame_tiles (testbench error: map 2 write ran past blanking)");
            $finish;
        end
        wait (done[1]);
        #1;
        if (|error)
            $display("FAIL: tb_frame_tiles (frame errors, see frame_dump lines above)");
        else if (align_err)
            $display("FAIL: tb_frame_tiles (hsync/vsync/de not delayed to match the pixels, see above)");
        else if (blank_err)
            $display("FAIL: tb_frame_tiles (color outside the visible area, see above)");
        else
            $display("PASS: tb_frame_tiles (2 frames written to build/frame_tiles_*.ppm, pipeline latency %0d)", lat);
        $finish;
    end
endmodule
`default_nettype wire
