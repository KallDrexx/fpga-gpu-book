// tb_frame_sprites.v -- frame-dump testbench for sprite_engine (Chapter 2.4).
//
// A 640x480 vga_timing drives one sprite_engine (your tile_engine from
// Chapter 2.3 with four 16x16 sprites on top). The tile map holds every tile
// (cell i holds i mod 64), written once at power-up. The testbench then dumps
// five frames, changing the sprite inputs during vertical blanking (line 490)
// before each one:
//   build/frame_sprites_corner.ppm     sprites at (0,0), the bottom-right corner,
//                                      and partly off the left and right edges
//   build/frame_sprites_offscreen.ppm  partly off the top and bottom; two
//                                      sprites that show one pixel each
//   build/frame_sprites_overlap.ppm    all four overlapping (priority and
//                                      transparency), four on the same lines
//   build/frame_sprites_hidden.ppm     a disabled sprite, sprites just off the
//                                      left and right edges
//   build/frame_sprites_abut.ppm       just off the top and bottom, two
//                                      sprites side by side
// ref_sprites.py draws the expected frames from the same tiles.hex and
// sprites.hex, and compare.py checks them pixel for pixel.
//
// While the frames run, this file also checks that
//   * o_hsync, o_vsync and o_de are i_hsync, i_vsync and i_de delayed by the
//     same number of clocks (the pipeline latency, 1 to 15), and
//   * the color is black whenever o_de is low.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_sprites.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_sprites.py build
//      python3 sim/py/compare.py --set build sprites
`timescale 1ns/1ps
`default_nettype none

module tb_frame_sprites;
    localparam COLS = 40, ROWS = 30, CELLS = COLS * ROWS, NFRAMES = 5;
    // Palettes {r, g, b}, 3 bits each. ref_sprites.py uses the same values.
    localparam [8:0] PAL0 = 9'o013, PAL1 = 9'o521, PAL2 = 9'o264, PAL3 = 9'o776;
    localparam [8:0] SPAL1 = 9'o777, SPAL2 = 9'o704, SPAL3 = 9'o770;

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    reg        wr_en = 1'b0;
    reg [10:0] wr_addr = 11'd0;
    reg [7:0]  wr_data = 8'd0;
    reg [39:0] spr_x = 40'd0, spr_y = 40'd0;
    reg [15:0] spr_img = 16'd0;
    reg [3:0]  spr_en = 4'd0;
    wire       t_hs, t_vs, t_de;
    wire [2:0] r, g, b;
    sprite_engine #(.TILE_FILE("rtl/common/tiles.hex"), .SPRITE_FILE("rtl/common/sprites.hex"),
                    .PAL0(PAL0), .PAL1(PAL1), .PAL2(PAL2), .PAL3(PAL3),
                    .SPAL1(SPAL1), .SPAL2(SPAL2), .SPAL3(SPAL3)) u_dut (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .i_wr_en(wr_en), .i_wr_addr(wr_addr), .i_wr_data(wr_data),
        .i_spr_x(spr_x), .i_spr_y(spr_y), .i_spr_img(spr_img), .i_spr_en(spr_en),
        .o_hsync(t_hs), .o_vsync(t_vs), .o_de(t_de),
        .o_red(r), .o_grn(g), .o_blu(b));

    // Delay registers that start at 0 make o_vsync low for the first few
    // clocks, which frame_dump would count as a vsync. The dumps see vsync
    // only once the map and the first sprites are set.
    reg  armed = 1'b0;
    wire dump_vs = t_vs | ~armed;
    wire [NFRAMES-1:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
                 .FILE("build/frame_sprites_corner.ppm")) u_dump0 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1),
                 .FILE("build/frame_sprites_offscreen.ppm")) u_dump1 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[1]), .o_error(error[1]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(2),
                 .FILE("build/frame_sprites_overlap.ppm")) u_dump2 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[2]), .o_error(error[2]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(3),
                 .FILE("build/frame_sprites_hidden.ppm")) u_dump3 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[3]), .o_error(error[3]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(4),
                 .FILE("build/frame_sprites_abut.ppm")) u_dump4 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[4]), .o_error(error[4]));

    // ---------------------------------------------------------------- scenes
    // Must match ref_sprites.py: keep them in step. Coordinates are logical
    // (320x240) pixels of the sprite's top-left corner, 10-bit two's complement.
    task put_sprite(input integer n, input integer sx, input integer sy,
                    input integer img, input en);
        begin
            spr_x[n*10 +: 10] = sx;
            spr_y[n*10 +: 10] = sy;
            spr_img[n*4 +: 4] = img;
            spr_en[n]         = en;
        end
    endtask

    task set_scene(input integer k);
        case (k)
            0: begin  // corner
                put_sprite(0,    0,   0,  5, 1);
                put_sprite(1,  304, 224,  6, 1);
                put_sprite(2,   -6, 100,  7, 1);
                put_sprite(3,  311,  50,  8, 1);
            end
            1: begin  // offscreen
                put_sprite(0,  150,  -9,  9, 1);
                put_sprite(1,   60, 233, 10, 1);
                put_sprite(2,  -15, -15, 11, 1);
                put_sprite(3,  319, 239, 12, 1);
            end
            2: begin  // overlap
                put_sprite(0,  100, 100, 13, 1);
                put_sprite(1,  106, 104, 14, 1);
                put_sprite(2,   96, 108, 15, 1);
                put_sprite(3,  110,  96,  0, 1);
            end
            3: begin  // hidden
                put_sprite(0,  160, 100,  3, 1);
                put_sprite(1,  150, 200,  0, 0);
                put_sprite(2,  -16,  50,  5, 1);
                put_sprite(3,  320,  60,  6, 1);
            end
            default: begin  // abut
                put_sprite(0,  200, 240,  7, 1);
                put_sprite(1,  100, -16,  8, 1);
                put_sprite(2,   40,  40,  1, 1);
                put_sprite(3,   56,  40,  4, 1);
            end
        endcase
    endtask

    // Write the tile map, one cell per clock, on the falling edge.
    integer i;
    task write_map;
        begin
            for (i = 0; i < CELLS; i = i + 1) begin
                @(negedge clk);
                wr_en = 1'b1; wr_addr = i; wr_data = i % 64;
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
                $display("  sprite_engine latency is %0d clocks; expected 1 to %0d", lat, HMAX-1);
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
            $dumpfile("build/tb_frame_sprites.vcd");
            $dumpvars(0, tb_frame_sprites);
        end
    end

    initial begin : watchdog
        #(40 * (NFRAMES + 3) * 420000);
        $display("FAIL: tb_frame_sprites watchdog timeout (no complete frame: is o_vsync toggling?)");
        $finish;
    end

    integer n;
    initial begin
        write_map;
        set_scene(0);
        armed = 1'b1;
        // Each later scene is set as soon as the previous frame has been
        // dumped: at the start of vertical sync (line 490), inside blanking.
        for (n = 1; n < NFRAMES; n = n + 1) begin
            wait (done[n-1]);
            @(negedge clk);
            set_scene(n);
            if (y < 480) begin
                $display("FAIL: tb_frame_sprites (testbench error: scene %0d set outside blanking)", n);
                $finish;
            end
        end
        wait (done[NFRAMES-1]);
        #1;
        if (|error)
            $display("FAIL: tb_frame_sprites (frame errors, see frame_dump lines above)");
        else if (align_err)
            $display("FAIL: tb_frame_sprites (hsync/vsync/de not delayed to match the pixels, see above)");
        else if (blank_err)
            $display("FAIL: tb_frame_sprites (color outside the visible area, see above)");
        else
            $display("PASS: tb_frame_sprites (%0d frames written to build/frame_sprites_*.ppm, pipeline latency %0d)",
                     NFRAMES, lat);
        $finish;
    end
endmodule
`default_nettype wire
