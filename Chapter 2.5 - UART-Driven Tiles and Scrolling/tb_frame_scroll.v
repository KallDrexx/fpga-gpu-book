// tb_frame_scroll.v -- frame-dump testbench for scroll_tiles (Chapter 2.5).
//
// A 640x480 vga_timing drives one scroll_tiles: a 64x32 map of 8x8 tiles
// (512x256 logical pixels) seen through the 320x240 screen at offset
// (scroll X, scroll Y), wrapping at the map edges. scroll_tiles outputs a
// 2-bit color index per pixel; this testbench turns it into a color with a
// fixed 4-entry palette before dumping. At power-up the testbench
// writes the whole map through the write port (cell row*64+col holds tile
// (col + 9*row) mod 64) and redraws tile 7 through the tile-graphics half of
// the same port. It then dumps five frames, changing the scroll inputs during
// vertical blanking (line 490) before each one:
//   build/frame_scroll_origin.ppm  scroll (0, 0)
//   build/frame_scroll_fine.ppm    scroll (3, 5): not a multiple of 8
//   build/frame_scroll_wrapx.ppm   scroll (300, 17): wraps from column 63 to 0
//   build/frame_scroll_wrapy.ppm   scroll (101, 200): wraps from row 31 to 0
//   build/frame_scroll_corner.ppm  scroll (511, 255): wraps both ways, one
//                                  logical pixel from the top-left corner
// ref_scroll.py draws the expected frames, compare.py checks them pixel for
// pixel.
//
// While the frames run, this file also checks that o_hsync, o_vsync and o_de
// are i_hsync, i_vsync and i_de delayed by the same number of clocks (the
// pipeline latency, 1 to 15), lined up with o_pix.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_scroll.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_scroll.py build
//      python3 sim/py/compare.py --set build scroll
`timescale 1ns/1ps
`default_nettype none

module tb_frame_scroll;
    localparam MAP_COLS = 64, MAP_ROWS = 32, NFRAMES = 5;
    // The testbench's palette {r, g, b}, 3 bits each; entry n in bits [9n +: 9].
    // ref_scroll.py uses the same values.
    localparam [35:0] PAL = {9'o776, 9'o264, 9'o521, 9'o013};

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    reg        wr_en = 1'b0;
    reg [11:0] wr_addr = 12'd0;
    reg [7:0]  wr_data = 8'd0;
    reg [8:0]  scroll_x = 9'd0;
    reg [7:0]  scroll_y = 8'd0;
    wire       t_hs, t_vs, t_de;
    wire [1:0] t_pix;
    wire [2:0] r, g, b;
    assign {r, g, b} = t_de ? PAL[t_pix * 9 +: 9] : 9'd0;
    scroll_tiles #(.TILE_FILE("rtl/common/tiles.hex")) u_dut (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .i_wr_en(wr_en), .i_wr_addr(wr_addr), .i_wr_data(wr_data),
        .i_scroll_x(scroll_x), .i_scroll_y(scroll_y),
        .o_hsync(t_hs), .o_vsync(t_vs), .o_de(t_de), .o_pix(t_pix));

    // Delay registers that start at 0 make o_vsync low for the first few
    // clocks, which frame_dump would count as a vsync. The dumps see vsync
    // only once the map is written.
    reg  armed = 1'b0;
    wire dump_vs = t_vs | ~armed;
    wire [NFRAMES-1:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
                 .FILE("build/frame_scroll_origin.ppm")) u_dump0 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1),
                 .FILE("build/frame_scroll_fine.ppm")) u_dump1 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[1]), .o_error(error[1]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(2),
                 .FILE("build/frame_scroll_wrapx.ppm")) u_dump2 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[2]), .o_error(error[2]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(3),
                 .FILE("build/frame_scroll_wrapy.ppm")) u_dump3 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[3]), .o_error(error[3]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(4),
                 .FILE("build/frame_scroll_corner.ppm")) u_dump4 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[4]), .o_error(error[4]));

    // ---------------------------------------------------------------- scenes
    // Must match ref_scroll.py: keep them in step.
    task set_scene(input integer k);
        case (k)
            0: begin scroll_x =   0; scroll_y =   0; end  // origin
            1: begin scroll_x =   3; scroll_y =   5; end  // fine
            2: begin scroll_x = 300; scroll_y =  17; end  // wrapx
            3: begin scroll_x = 101; scroll_y = 200; end  // wrapy
            default: begin scroll_x = 511; scroll_y = 255; end  // corner
        endcase
    endtask

    // The new tile 7: eight 16-bit rows, leftmost pixel in bits 15:14.
    // High and low bytes differ in every row, so swapped bytes show up.
    function [15:0] tile7_row(input integer row);
        case (row)
            0: tile7_row = 16'hFF00;  1: tile7_row = 16'hE400;
            2: tile7_row = 16'h9000;  3: tile7_row = 16'h4000;
            4: tile7_row = 16'h001B;  5: tile7_row = 16'h0006;
            6: tile7_row = 16'h0001;  default: tile7_row = 16'h5A0F;
        endcase
    endfunction

    task write_byte(input [11:0] a, input [7:0] d);
        begin
            @(negedge clk);
            wr_en = 1'b1; wr_addr = a; wr_data = d;
        end
    endtask

    // Write the whole map and tile 7, one byte per clock, on the falling edge.
    integer i;
    task write_vram;
        begin
            for (i = 0; i < MAP_COLS * MAP_ROWS; i = i + 1)
                write_byte(i, ((i % MAP_COLS) + 9 * (i / MAP_COLS)) % 64);
            // Tile rows are big-endian: the even address holds bits 15:8.
            for (i = 0; i < 8; i = i + 1) begin
                write_byte(12'h800 + 7 * 16 + 2 * i,     tile7_row(i) >> 8);
                write_byte(12'h800 + 7 * 16 + 2 * i + 1, tile7_row(i) & 8'hFF);
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
    reg       align_err = 1'b0;
    integer   k;
    always @(negedge clk) begin
        for (k = HMAX-1; k > 0; k = k - 1) hist[k] = hist[k-1];
        hist[0] = {hs, vs, de};
        if (rise_in < 0 && de === 1'b1 && prev_de === 1'b0) rise_in = n_samp;
        if (rise_in >= 0 && lat < 0 && t_de === 1'b1 && prev_t_de === 1'b0) begin
            lat = n_samp - rise_in;
            if (lat < 1 || lat >= HMAX) begin
                $display("  scroll_tiles latency is %0d clocks; expected 1 to %0d", lat, HMAX-1);
                align_err = 1'b1;
            end
        end
        if (lat >= 1 && lat < HMAX && !align_err && {t_hs, t_vs, t_de} !== hist[lat]) begin
            $display("  sync misaligned at x=%0d y=%0d: o_hsync/o_vsync/o_de = %b%b%b, inputs %0d clocks ago = %b",
                     x, y, t_hs, t_vs, t_de, lat, hist[lat]);
            align_err = 1'b1;
        end
        prev_de = de; prev_t_de = t_de;
        n_samp = n_samp + 1;
    end

    // ------------------------------------------------------------------ main
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_scroll.vcd");
            $dumpvars(0, tb_frame_scroll);
        end
    end

    initial begin : watchdog
        #(40 * (NFRAMES + 3) * 420000);
        $display("FAIL: tb_frame_scroll watchdog timeout (no complete frame: is o_vsync toggling?)");
        $finish;
    end

    integer n;
    initial begin
        write_vram;
        set_scene(0);
        armed = 1'b1;
        // Each later scroll offset is set as soon as the previous frame has
        // been dumped: at the start of vertical sync (line 490), inside blanking.
        for (n = 1; n < NFRAMES; n = n + 1) begin
            wait (done[n-1]);
            @(negedge clk);
            set_scene(n);
            if (y < 480) begin
                $display("FAIL: tb_frame_scroll (testbench error: scene %0d set outside blanking)", n);
                $finish;
            end
        end
        wait (done[NFRAMES-1]);
        #1;
        if (|error)
            $display("FAIL: tb_frame_scroll (frame errors, see frame_dump lines above)");
        else if (align_err)
            $display("FAIL: tb_frame_scroll (hsync/vsync/de not delayed to match the pixels, see above)");
        else
            $display("PASS: tb_frame_scroll (%0d frames written to build/frame_scroll_*.ppm, pipeline latency %0d)",
                     NFRAMES, lat);
        $finish;
    end
endmodule
`default_nettype wire
