// tb_frame_ppu.v -- system test for uart_ppu (Chapter 2.5).
//
// Everything goes through the serial line, using the Step 0.5 packets
// 'W' addr data and 'R' addr, with the register map of Chapter 2.5:
//   0x00 LEDS  0x01 SEG  0x02 SCROLL_X  0x03 SCROLL_Y  0x04 SCROLL_XH
//   0x05 ADDR_LO  0x06 ADDR_HI  0x07 DATA (writes VRAM[ADDR], then ADDR += 1)
//   VRAM: 0x000-0x7FF map, 0x800-0xBFF tile rows (big-endian),
//         0xC00-0xC0F palette (8 entries, big-endian 9-bit {r,g,b}),
//         0xC10-0xC2F sprites (8 bytes each: X hi, X lo, Y hi, Y lo, ATTR, 3 unused)
//
// The testbench
//   1. writes LEDS and SEG and checks o_reg0 and o_reg1,
//   2. loads the palette, redraws tile 5, writes all 2,048 map bytes, places
//      three sprites and sets the scroll to (300, 200), then reads registers
//      0x00-0x04 (and the unused 0x20) back over the serial line,
//   3. dumps three consecutive frames:
//        build/frame_ppu_a.ppm  the scene above
//        build/frame_ppu_b.ppm  during this frame's visible lines (from line
//                               100) it moves sprite 0 partly over sprite
//                               3, enables sprite 3 and sets the scroll to
//                               (37, 250): the frame must still be scene A
//        build/frame_ppu_c.ppm  the new sprite and scroll positions
// ref_ppu.py draws the expected frames, compare.py checks them pixel for pixel.
// It also checks that o_hsync, o_vsync and o_de share one delay and that the
// color is black whenever o_de is low.
//
// To keep the run short the link runs at CLK_HZ / 32 (781,250 baud), so your
// uart_rx and uart_tx must work at 32 clocks per bit. Every write group ends
// with SCROLL_Y, and the groups that frames a-c depend on finish inside the
// visible lines, so any commit point inside vertical blanking passes, with or
// without a commit flag.
//
// Run: iverilog -g2012 -Wall -Wno-timescale -y rtl/common -y sim -o build/tb.vvp sim/tb_frame_ppu.v
//      vvp -n build/tb.vvp          (add +vcd for waveforms; the dump is large)
//      python3 sim/py/ref_ppu.py build
//      python3 sim/py/compare.py --set build ppu
`timescale 1ns/1ps
`default_nettype none

module tb_frame_ppu;
    localparam CLK_HZ = 25_000_000, BAUD = 781_250, CPB = CLK_HZ / BAUD;
    localparam NFRAMES = 3, MAP_COLS = 64, MAP_ROWS = 32;
    localparam [7:0] OP_W = 8'h57, OP_R = 8'h52;
    localparam [7:0] LEDS = 8'h00, SEG = 8'h01, SCROLL_X = 8'h02, SCROLL_Y = 8'h03,
                     SCROLL_XH = 8'h04, ADDR_LO = 8'h05, ADDR_HI = 8'h06, DATA = 8'h07;

    reg clk = 0;
    always #20 clk = ~clk;  // 25 MHz

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    reg        rx = 1'b1;   // into the FPGA
    wire       tx;          // out of the FPGA
    wire       t_hs, t_vs, t_de;
    wire [2:0] r, g, b;
    wire [7:0] reg0, reg1;
    uart_ppu #(.CLK_HZ(CLK_HZ), .BAUD(BAUD),
               .TILE_FILE("rtl/common/tiles.hex"), .SPRITE_FILE("rtl/common/sprites.hex")) u_dut (
        .i_clk(clk), .i_rx(rx), .o_tx(tx),
        .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .o_hsync(t_hs), .o_vsync(t_vs), .o_de(t_de),
        .o_red(r), .o_grn(g), .o_blu(b), .o_reg0(reg0), .o_reg1(reg1));

    // The dumps see vsync only once the scene is loaded.
    reg  armed = 1'b0;
    wire dump_vs = t_vs | ~armed;
    wire [NFRAMES-1:0] done, error;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(0),
                 .FILE("build/frame_ppu_a.ppm")) u_dump0 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[0]), .o_error(error[0]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(1),
                 .FILE("build/frame_ppu_b.ppm")) u_dump1 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[1]), .o_error(error[1]));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(3), .SKIP(2),
                 .FILE("build/frame_ppu_c.ppm")) u_dump2 (
        .i_clk(clk), .i_vsync(dump_vs), .i_de(t_de), .i_red(r), .i_grn(g), .i_blu(b),
        .o_done(done[2]), .o_error(error[2]));

    // ------------------------------------------------------------ serial out
    // One byte: start bit, 8 data bits LSB first, stop bit; CPB clocks each.
    task send_byte(input [7:0] v);
        integer i;
        begin
            @(negedge clk); rx = 1'b0;
            repeat (CPB) @(negedge clk);
            for (i = 0; i < 8; i = i + 1) begin
                rx = v[i];
                repeat (CPB) @(negedge clk);
            end
            rx = 1'b1;
            repeat (CPB) @(negedge clk);
        end
    endtask

    task wr(input [7:0] a, input [7:0] d);
        begin send_byte(OP_W); send_byte(a); send_byte(d); end
    endtask

    task set_addr(input [11:0] a);
        begin wr(ADDR_LO, a[7:0]); wr(ADDR_HI, {4'd0, a[11:8]}); end
    endtask

    task data(input [7:0] d);
        begin wr(DATA, d); end
    endtask

    // ------------------------------------------------------------- serial in
    // Decodes every byte the FPGA sends into a list, sampling mid-bit.
    reg [7:0] rxq [0:31];
    integer   nrx = 0, bi;
    reg [7:0] sh;
    reg       frame_err = 1'b0;
    initial forever begin
        @(negedge tx);
        repeat (CPB / 2) @(negedge clk);
        if (tx === 1'b0) begin
            for (bi = 0; bi < 8; bi = bi + 1) begin
                repeat (CPB) @(negedge clk);
                sh[bi] = tx;
            end
            repeat (CPB) @(negedge clk);
            if (tx !== 1'b1 && !frame_err) begin
                $display("  reply byte 0x%02h has no stop bit", sh);
                frame_err = 1'b1;
            end
            if (nrx < 32) rxq[nrx] = sh;
            nrx = nrx + 1;
        end
    end

    reg read_err = 1'b0;
    task check_read(input [7:0] a, input [7:0] want);
        integer t, n0;
        begin
            n0 = nrx;
            send_byte(OP_R); send_byte(a);
            t = 0;
            while (nrx == n0 && t < 20 * CPB * 10) begin @(negedge clk); t = t + 1; end
            if (nrx == n0) begin
                $display("  read of register 0x%02h: no reply", a);
                read_err = 1'b1;
            end else if (rxq[n0] !== want) begin
                $display("  read of register 0x%02h returned 0x%02h, expected 0x%02h", a, rxq[n0], want);
                read_err = 1'b1;
            end
        end
    endtask

    // ---------------------------------------------------------------- scenes
    // Must match ref_ppu.py: keep them in step.
    function [8:0] palette(input integer n);   // {r, g, b}
        case (n)
            0: palette = 9'o001; 1: palette = 9'o531; 2: palette = 9'o247; 3: palette = 9'o675;
            5: palette = 9'o777; 6: palette = 9'o704; 7: palette = 9'o770;
            default: palette = 9'o000;            // entry 4 is unused
        endcase
    endfunction

    function [15:0] tile5_row(input integer row);
        case (row)
            0: tile5_row = 16'hC003;  1: tile5_row = 16'h3FFC;
            2: tile5_row = 16'h2008;  3: tile5_row = 16'h2E08;
            4: tile5_row = 16'h2008;  5: tile5_row = 16'h2008;
            6: tile5_row = 16'h3FFC;  default: tile5_row = 16'h0001;
        endcase
    endfunction

    // One sprite record: X hi, X lo, Y hi, Y lo, ATTR (bit 7 enable, 3:0 image).
    task sprite_bytes(input integer sx, input integer sy, input integer img, input en);
        reg [15:0] xx, yy;
        begin
            xx = sx; yy = sy;
            data(xx[15:8]); data(xx[7:0]); data(yy[15:8]); data(yy[7:0]);
            data({en, 3'd0, img[3:0]});
        end
    endtask

    task scroll(input integer sx, input integer sy);
        reg [8:0] xx;
        begin
            xx = sx;
            wr(SCROLL_XH, {7'd0, xx[8]}); wr(SCROLL_X, xx[7:0]); wr(SCROLL_Y, sy);
        end
    endtask

    integer    i;
    reg [8:0]  pv;
    reg [15:0] tv;
    task load_scene_a;
        begin
            set_addr(12'hC00);                // palette: high byte (bit 8 = red MSB) first
            for (i = 0; i < 8; i = i + 1) begin
                pv = palette(i);
                data({7'd0, pv[8]}); data(pv[7:0]);
            end
            set_addr(12'h800 + 5 * 16);       // tile 5: bits 15:8 of each row first
            for (i = 0; i < 8; i = i + 1) begin
                tv = tile5_row(i);
                data(tv[15:8]); data(tv[7:0]);
            end
            set_addr(12'h000);
            for (i = 0; i < MAP_COLS * MAP_ROWS; i = i + 1)
                data((3 * (i % MAP_COLS) + 5 * (i / MAP_COLS) + 1) % 64);
            set_addr(12'hC10);
            sprite_bytes( -6, 100, 0, 1);     // sprite 0: ship, off the left edge
            data(0); data(0); data(0);
            sprite_bytes(150, 230, 1, 1);     // sprite 1: enemy, off the bottom
            data(0); data(0); data(0);
            sprite_bytes(300,  10, 3, 1);     // sprite 2: bullet
            data(0); data(0); data(0);
            sprite_bytes( 60,  60, 4, 0);     // sprite 3: disabled
            data(0); data(0); data(0);
            scroll(300, 200);
        end
    endtask

    task update_scene_c;
        begin
            set_addr(12'hC10);
            sprite_bytes(52, 56, 0, 1);       // move sprite 0, partly over sprite 3
            set_addr(12'hC10 + 3 * 8 + 4);
            data({1'b1, 3'd0, 4'd4});         // enable sprite 3
            scroll(37, 250);
        end
    endtask

    // ------------------------------------------------- sync alignment checks
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
                $display("  uart_ppu latency is %0d clocks; expected 1 to %0d", lat, HMAX-1);
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
            $dumpfile("build/tb_frame_ppu.vcd");
            $dumpvars(0, tb_frame_ppu);
        end
    end

    initial begin : watchdog
        #(40 * 14 * 420000);
        $display("FAIL: tb_frame_ppu watchdog timeout (stuck on the serial link, or no complete frame)");
        $finish;
    end

    reg reg_err = 1'b0;
    initial begin
        repeat (100) @(negedge clk);
        wr(LEDS, 8'h0A); wr(SEG, 8'h5C);
        repeat (4 * CPB) @(negedge clk);
        if (reg0 !== 8'h0A || reg1 !== 8'h5C) begin
            $display("  after writing LEDS=0x0A and SEG=0x5C: o_reg0=0x%02h o_reg1=0x%02h", reg0, reg1);
            reg_err = 1'b1;
        end
        load_scene_a;
        check_read(LEDS, 8'h0A);
        check_read(SEG, 8'h5C);
        check_read(SCROLL_X, 8'd44);     // 300 = 0x12C
        check_read(SCROLL_Y, 8'd200);
        check_read(SCROLL_XH, 8'd1);
        check_read(8'h20, 8'h00);        // outside the register file
        // Start dumping at the next frame.
        wait (y == 100);
        @(negedge clk);
        armed = 1'b1;
        // Frame A is being dumped. Once it is done (line 490), wait for line
        // 100 of frame B and change the scene while the beam is drawing it.
        wait (done[0]);
        wait (y == 100);
        update_scene_c;
        if (y >= 480 || y < 100) begin
            $display("FAIL: tb_frame_ppu (testbench error: the update did not finish inside frame B)");
            $finish;
        end
        wait (done[NFRAMES-1]);
        #1;
        if (|error)
            $display("FAIL: tb_frame_ppu (frame errors, see frame_dump lines above)");
        else if (reg_err || read_err || frame_err)
            $display("FAIL: tb_frame_ppu (register writes or read-back over the serial line, see above)");
        else if (align_err)
            $display("FAIL: tb_frame_ppu (hsync/vsync/de not delayed to match the pixels, see above)");
        else if (blank_err)
            $display("FAIL: tb_frame_ppu (color outside the visible area, see above)");
        else
            $display("PASS: tb_frame_ppu (registers read back; %0d frames written to build/frame_ppu_*.ppm, pipeline latency %0d)",
                     NFRAMES, lat);
        $finish;
    end
endmodule
`default_nettype wire
