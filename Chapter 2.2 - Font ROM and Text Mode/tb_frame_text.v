// tb_frame_text.v -- frame-dump testbench for text_mode (Chapter 2.2).
//
// A 640x480 vga_timing drives one text_mode. The testbench fills the
// 40x30 character RAM through the write port and dumps two frames:
//   build/frame_text_string.ppm  a test screen: corners, the full printable
//                                ASCII set, a sentence, an inverse-video line
//   build/frame_text_codes.ppm   every code 0x00-0xFF in turn (cell i holds
//                                i mod 256), rewritten during vertical blanking
// ref_text.py draws the expected frames from the same font file and
// compare.py checks them pixel for pixel.
//
// While the frames run, this file also checks that
//   * o_hsync, o_vsync and o_de are i_hsync, i_vsync and i_de delayed by the
//     same number of clocks (the pipeline latency, at least 1), and
//   * the color is black whenever o_de is low.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_text.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_text.py build
//      python3 sim/py/compare.py --set build text
`timescale 1ns/1ps
`default_nettype none

module tb_frame_text;
    localparam COLS = 40, ROWS = 30, CELLS = COLS * ROWS;
    // Colors {r, g, b}, 3 bits each. ref_text.py uses the same values.
    localparam [8:0] FG = 9'o764;   // (7, 6, 4)
    localparam [8:0] BG = 9'o013;   // (0, 1, 3)

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
    text_mode #(.FONT_FILE("rtl/common/font8x8.hex"), .FG(FG), .BG(BG)) u_text (
        .i_clk(clk), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .i_wr_en(wr_en), .i_wr_addr(wr_addr), .i_wr_data(wr_data),
        .o_hsync(t_hs), .o_vsync(t_vs), .o_de(t_de),
        .o_red(r), .o_grn(g), .o_blu(b));

    wire [1:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
                 .FILE("build/frame_text_string.ppm")) u_dump0 (
        .i_clk(clk), .i_vsync(t_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1),
                 .FILE("build/frame_text_codes.ppm")) u_dump1 (
        .i_clk(clk), .i_vsync(t_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[1]), .o_error(error[1]));

    // ---------------------------------------------------------------- screens
    // Must match ref_text.py: keep them in step.
    reg [7:0] screen [0:CELLS-1];
    integer i;

    // Write the string s (right-justified in the 320-bit vector, as Verilog
    // stores string literals) at (row, col), OR-ing each code with attr.
    task put(input integer row, input integer col, input [8*40-1:0] s, input [7:0] attr);
        integer n, k;
        begin
            n = 40;
            while (n > 0 && s[8*n-1 -: 8] == 8'd0) n = n - 1;
            for (k = 0; k < n; k = k + 1)
                screen[row * COLS + col + k] = s[8*(n-1-k) +: 8] | attr;
        end
    endtask

    task make_string_screen;
        begin
            for (i = 0; i < CELLS; i = i + 1) screen[i] = 8'h20;
            put(0, 0, "#", 0);  put(0, 39, "#", 0);
            put(29, 0, "#", 0); put(29, 39, "#", 0);
            put(0, 2, "The quick brown fox jumps over", 0);
            for (i = 0; i < 95; i = i + 1) screen[2 * COLS + i] = 8'h20 + i;
            put(8, 11, "Hello, FPGA world!", 0);
            put(12, 11, " TEXT MODE 40x30 ", 8'h80);
            put(15, 0, "@", 0); put(15, 39, "W", 0);
            put(29, 2, "MW__WM {edges} |0123456789|", 0);
            put(28, 37, "end", 8'h80);
        end
    endtask

    task make_codes_screen;
        for (i = 0; i < CELLS; i = i + 1) screen[i] = i % 256;
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
                $display("  text_mode latency is %0d clocks; expected 1 to %0d (reads must be registered)",
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
            $dumpfile("build/tb_frame_text.vcd");
            $dumpvars(0, tb_frame_text);
        end
    end

    initial begin : watchdog
        #(40 * 4 * 420000);
        $display("FAIL: tb_frame_text watchdog timeout (no complete frame: is o_vsync toggling?)");
        $finish;
    end

    initial begin
        // Screen 1 is written long before the first frame starts.
        make_string_screen;
        write_screen;
        // Screen 2 is written after frame 1 ends, inside vertical blanking.
        wait (done[0]);
        make_codes_screen;
        write_screen;
        if (y < 480) begin
            $display("FAIL: tb_frame_text (testbench error: screen 2 write ran past blanking)");
            $finish;
        end
        wait (done[1]);
        #1;
        if (|error)
            $display("FAIL: tb_frame_text (frame errors, see frame_dump lines above)");
        else if (align_err)
            $display("FAIL: tb_frame_text (hsync/vsync/de not delayed to match the pixels, see above)");
        else if (blank_err)
            $display("FAIL: tb_frame_text (color outside the visible area, see above)");
        else
            $display("PASS: tb_frame_text (2 frames written to build/frame_text_*.ppm, pipeline latency %0d)", lat);
        $finish;
    end
endmodule
`default_nettype wire
