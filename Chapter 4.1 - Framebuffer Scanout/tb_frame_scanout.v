// tb_frame_scanout.v -- frame-dump testbench for fb_scanout (Chapter 4.1).
//
// A 640x480 vga_timing on a 25 MHz clk_pix drives fb_scanout, whose
// framebuffer and palette are preloaded from rtl/common/fb_test.hex and
// pal_test.hex (written by `.venv/bin/python sim/py/ref_scanout.py --hex rtl/common`).
// The draw port runs on its own 50 MHz clk_sys.
//
//   frame 0   scanned but not dumped (so a reader that forgets to rewind its
//             address at the start of a frame shows up in frame 1)
//   frame 1   dumped to build/frame_scanout_image.ppm: the preloaded image
//   blanking  the testbench writes 3 palette entries and 444 pixels through
//             the clk_sys port (the same list as ref_scanout.py), then reads
//             back 74 addresses through o_fb_rdata (1 clk_sys of latency)
//   frame 2   dumped to build/frame_scanout_edit.ppm: the image after the writes
//
// It also measures the pipeline latency L (from i_de to o_de, any value from
// 1 to 24 clocks) and checks that o_hsync, o_vsync and o_de are all exactly
// i_hsync, i_vsync and i_de delayed by L. compare.py checks the pixels
// against ref_scanout.py. Color outside o_de is not checked.
//
// Run from the repo root (the hex paths are relative to it):
//   make frame-scanout
// or by hand:
//   iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb_frame_scanout.vvp sim/tb_frame_scanout.v
//   vvp -n build/tb_frame_scanout.vvp      (+vcd for waveforms; very large)
`timescale 1ns/1ps
`default_nettype none

module tb_frame_scanout;
    localparam FB_FILE  = "rtl/common/fb_test.hex";
    localparam PAL_FILE = "rtl/common/pal_test.hex";

    // ---------------- clocks: 25 MHz pixel, 50 MHz draw ----------------
    reg clk_pix = 1'b0, clk_sys = 1'b0;
    always #20 clk_pix = ~clk_pix;
    initial begin
        #3;                              // not edge-aligned with clk_pix
        forever #10 clk_sys = ~clk_sys;
    end

    // ---------------- beam ----------------
    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (
        .i_clk(clk_pix), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y)
    );

    // ---------------- DUT ----------------
    reg         fb_we = 1'b0, pal_we = 1'b0;
    reg  [16:0] fb_addr = 17'd0;
    reg  [7:0]  fb_wdata = 8'd0, pal_addr = 8'd0;
    reg  [23:0] pal_wdata = 24'd0;
    wire [7:0]  fb_rdata;
    wire        o_hs, o_vs, o_de;
    wire [7:0]  o_r, o_g, o_b;

    fb_scanout #(.FB_FILE(FB_FILE), .PAL_FILE(PAL_FILE)) dut (
        .i_clk_sys(clk_sys),
        .i_fb_we(fb_we), .i_fb_addr(fb_addr), .i_fb_wdata(fb_wdata), .o_fb_rdata(fb_rdata),
        .i_pal_we(pal_we), .i_pal_addr(pal_addr), .i_pal_wdata(pal_wdata),
        .i_clk_pix(clk_pix), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .o_hsync(o_hs), .o_vsync(o_vs), .o_de(o_de),
        .o_red(o_r), .o_grn(o_g), .o_blu(o_b)
    );

    // ---------------- dumpers ----------------
    // armed keeps the dumpers from mistaking a power-up low on o_vsync for a
    // frame start. Armed during frame 0, so the first fall they see ends
    // frame 0: "image" dumps frame 1, "edit" (SKIP=1) frame 2.
    reg  armed = 1'b0;
    wire dump_vs = o_vs | ~armed;
    wire done_img, err_img, done_edit, err_edit;

    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .SKIP(0), .FILE("build/frame_scanout_image.ppm")) u_dump_img (
        .i_clk(clk_pix), .i_vsync(dump_vs), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
        .o_done(done_img), .o_error(err_img));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .SKIP(1), .FILE("build/frame_scanout_edit.ppm")) u_dump_edit (
        .i_clk(clk_pix), .i_vsync(dump_vs), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
        .o_done(done_edit), .o_error(err_edit));

    // ---------------- latency and sync alignment ----------------
    // h_*[k] holds the input k+1 clk_pix edges ago, so a DUT with latency L
    // must show h_*[L-1] at every edge.
    reg [31:0] h_hs = {32{1'b1}}, h_vs = {32{1'b1}}, h_de = 32'd0;
    integer    lat = 0;             // measured latency, 0 = not yet
    integer    since_rise = -1;     // clocks since the measured i_de rise
    reg        sync_err = 1'b0;
    always @(posedge clk_pix) begin
        // Measure on line 4 of frame 0: from i_de rising to o_de rising.
        if (lat == 0) begin
            if (since_rise >= 0) since_rise = since_rise + 1;
            if (since_rise < 0 && de && !h_de[0] && y == 10'd4) since_rise = 0;
            if (since_rise > 0 && o_de === 1'b1 && since_rise <= 24) lat = since_rise;
            if (since_rise > 24) begin
                if (!sync_err) $display("tb_frame_scanout: o_de did not rise within 24 clocks of i_de");
                sync_err = 1'b1;
                since_rise = -1;
            end
        end else if (o_hs !== h_hs[lat-1] || o_vs !== h_vs[lat-1] || o_de !== h_de[lat-1]) begin
            if (!sync_err)
                $display("tb_frame_scanout: syncs/de not all delayed by %0d clocks (beam x=%0d y=%0d: o_hsync=%b want %b, o_vsync=%b want %b, o_de=%b want %b)",
                         lat, x, y, o_hs, h_hs[lat-1], o_vs, h_vs[lat-1], o_de, h_de[lat-1]);
            sync_err = 1'b1;
        end
        h_hs <= {h_hs[30:0], hs};
        h_vs <= {h_vs[30:0], vs};
        h_de <= {h_de[30:0], de};
    end

    // ---------------- draw-port writes (same list as ref_scanout.py) ----------------
    reg [7:0] shadow [0:76799];     // what the framebuffer should hold
    integer   i, a, n_wr = 0, rb_err = 0;
    reg       edit_late = 1'b0;

    task fb_write(input [16:0] addr, input [7:0] data);
        begin
            @(negedge clk_sys);
            fb_we = 1'b1; fb_addr = addr; fb_wdata = data;
            @(negedge clk_sys);
            fb_we = 1'b0;
            shadow[addr] = data;
            n_wr = n_wr + 1;
        end
    endtask

    task pal_write(input [7:0] addr, input [23:0] data);
        begin
            @(negedge clk_sys);
            pal_we = 1'b1; pal_addr = addr; pal_wdata = data;
            @(negedge clk_sys);
            pal_we = 1'b0;
        end
    endtask

    // Present addr, let one clk_sys edge pass, then o_fb_rdata must hold it.
    task fb_check(input [16:0] addr);
        begin
            @(negedge clk_sys);
            fb_addr = addr;
            @(negedge clk_sys);
            if (fb_rdata !== shadow[addr]) begin
                if (rb_err < 3)
                    $display("tb_frame_scanout: o_fb_rdata at address %0d is %h, expected %h (1 clk_sys after the address)",
                             addr, fb_rdata, shadow[addr]);
                rb_err = rb_err + 1;
            end
        end
    endtask

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("build/tb_frame_scanout.vcd");
            $dumpvars(0, tb_frame_scanout);
        end
        $readmemh(FB_FILE, shadow);
        if (shadow[0] === 8'bx || shadow[76799] === 8'bx) begin
            $display("FAIL: tb_frame_scanout cannot read %0s (run `.venv/bin/python sim/py/ref_scanout.py --hex rtl/common`, and run from the repo root)", FB_FILE);
            $finish;
        end

        // Arm on line 10 of frame 0, well after any power-up delay.
        wait (y == 10'd10);
        armed = 1'b1;

        // The "image" frame ends at the next vsync: we are in blanking now.
        wait (done_img);
        pal_write(8'd1,   24'hFF8000);
        pal_write(8'd254, 24'h0080FF);
        pal_write(8'd255, 24'h80FF00);
        fb_write(17'd0,               8'd254);
        fb_write(17'd319,             8'd254);
        fb_write(17'd239 * 320,       8'd254);
        fb_write(17'd239 * 320 + 319, 8'd254);
        for (i = 40; i < 280; i = i + 1) fb_write(120 * 320 + i, 8'd255);
        for (i = 20; i < 220; i = i + 1) fb_write(i * 320 + 161, 8'd255);

        // Read back: the corners, the lines' ends, and preloaded bytes.
        fb_check(17'd0); fb_check(17'd319); fb_check(17'd76480); fb_check(17'd76799);
        fb_check(120 * 320 + 40); fb_check(120 * 320 + 279); fb_check(20 * 320 + 161);
        fb_check(17'd1); fb_check(17'd320); fb_check(17'd76798);
        for (a = 7; a < 76800; a = a + 1201) fb_check(a);

        if (y < 10'd480) edit_late = 1'b1;   // must all happen in blanking
    end

    // ---------------- watchdog and verdict ----------------
    initial begin : watchdog
        #(40 * 800 * 525 * 5);
        $display("FAIL: tb_frame_scanout watchdog timeout (frames not complete: is o_vsync toggling? is o_de 640x480?)");
        $finish;
    end

    initial begin
        wait (done_img && done_edit);
        #1;
        if (err_img || err_edit)
            $display("FAIL: tb_frame_scanout (frame errors, see frame_dump lines above)");
        else if (sync_err)
            $display("FAIL: tb_frame_scanout (o_hsync/o_vsync/o_de not aligned, see above)");
        else if (rb_err != 0)
            $display("FAIL: tb_frame_scanout (%0d draw-port read-back mismatches)", rb_err);
        else if (edit_late)
            $display("FAIL: tb_frame_scanout (testbench writes ran past vertical blanking)");
        else
            $display("PASS: tb_frame_scanout (latency %0d clocks, %0d draw-port writes; build/frame_scanout_{image,edit}.ppm written)", lat, n_wr);
        $finish;
    end
endmodule
`default_nettype wire
