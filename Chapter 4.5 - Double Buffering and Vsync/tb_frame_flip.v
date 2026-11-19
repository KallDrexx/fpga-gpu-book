// tb_frame_flip.v -- frame-dump testbench for FLIP (Chapter 4.5).
//
// uart_gpu + fb_scanout + a real vga_timing, as on the board, with the
// engine on clk_sys (19.936 ns period, unrelated to clk_pix's 40 ns). The
// testbench is the host: it sends commands as real serial at 2.5 Mbaud, and
// uses FB_CRC as a fence, because an FB_CRC queued behind a FLIP is answered
// only after the swap.
//
//   1. draw picture A into the back buffer, FLIP, draw picture B into the
//      other buffer (which still holds the test image), FB_CRC, wait for it
//   2. dump a frame -> build/frame_flip_drawn.ppm: must be A, nothing of B
//   3. FLIP, FB_CRC, wait; dump a frame -> frame_flip_flipped.ppm: B
//   4. FLIP, FB_CRC, wait; dump a frame -> frame_flip_again.ppm: A again
//
// compare.py checks the three frames against ref_flip.py, pixel for pixel.
// The testbench also checks that the three replies arrive and o_overflow
// stays low. Each dumper starts at the first vsync after its arming, so
// every dumped frame lies entirely after the swap that put its picture up
// and entirely before the next FLIP request.
//
// Run from the repo root:  make frame-flip
`timescale 1ns/1ps
`default_nettype none

module tb_frame_flip;
    localparam integer BAUD   = 2_500_000;
    localparam integer BIT_NS = 1_000_000_000 / BAUD;

    reg clk_pix = 1'b0, clk_sys = 1'b0;
    always #20 clk_pix = ~clk_pix;
    initial begin
        #3;
        forever #9.968 clk_sys = ~clk_sys;
    end

    wire       hs, vs, de;
    wire [9:0] x, y;
    vga_timing u_vga (.i_clk(clk_pix), .o_hsync(hs), .o_vsync(vs), .o_de(de), .o_x(x), .o_y(y));

    reg         rst = 1'b1, rx = 1'b1;
    wire        tx, ready, overflow, fb_we, pal_we, fb_buf, flip_req, flip_ack, vblank;
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, fb_rdata, pal_addr;
    wire [23:0] pal_wdata;
    wire        o_hs, o_vs, o_de;
    wire [7:0]  o_r, o_g, o_b;

    uart_gpu #(.CLK_HZ(50_000_000), .BAUD(BAUD)) u_gpu (
        .i_clk(clk_sys), .i_rst(rst), .i_rx(rx), .o_tx(tx),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack));

    fb_scanout #(.FB_FILE("rtl/common/fb_test.hex"), .PAL_FILE("rtl/common/pal_test.hex")) u_scan (
        .i_clk_sys(clk_sys), .i_fb_buf(fb_buf),
        .i_fb_we(fb_we), .i_fb_addr(fb_addr), .i_fb_wdata(fb_wdata), .o_fb_rdata(fb_rdata),
        .i_pal_we(pal_we), .i_pal_addr(pal_addr), .i_pal_wdata(pal_wdata),
        .i_flip_req(flip_req), .o_flip_ack(flip_ack),
        .i_clk_pix(clk_pix), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .o_hsync(o_hs), .o_vsync(o_vs), .o_de(o_de), .o_red(o_r), .o_grn(o_g), .o_blu(o_b),
        .o_vblank(vblank));

    // ---------------- dumpers, one per picture ----------------
    reg  arm_drawn = 1'b0, arm_flipped = 1'b0, arm_again = 1'b0;
    wire done_drawn, err_drawn, done_flipped, err_flipped, done_again, err_again;
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .FILE("build/frame_flip_drawn.ppm")) u_d1 (
        .i_clk(clk_pix), .i_vsync(o_vs | ~arm_drawn), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
        .o_done(done_drawn), .o_error(err_drawn));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .FILE("build/frame_flip_flipped.ppm")) u_d2 (
        .i_clk(clk_pix), .i_vsync(o_vs | ~arm_flipped), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
        .o_done(done_flipped), .o_error(err_flipped));
    frame_dump #(.WIDTH(640), .HEIGHT(480), .BITS(8), .FILE("build/frame_flip_again.ppm")) u_d3 (
        .i_clk(clk_pix), .i_vsync(o_vs | ~arm_again), .i_de(o_de), .i_red(o_r), .i_grn(o_g), .i_blu(o_b),
        .o_done(done_again), .o_error(err_again));

    // ---------------- serial host ----------------
    integer n_sent = 0, n_got = 0, k;
    reg [7:0] b;
    always begin                                        // reply receiver
        @(negedge tx);
        #(BIT_NS / 2);
        for (k = 0; k < 8; k = k + 1) begin #(BIT_NS); b[k] = tx; end
        #(BIT_NS);
        n_got = n_got + 1;
        #(BIT_NS / 4);
    end

    task send_byte(input [7:0] d);
        integer j;
        begin
            @(negedge clk_sys);
            while (ready !== 1'b1) @(negedge clk_sys);
            rx = 1'b0; #(BIT_NS);
            for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
            rx = 1'b1; #(BIT_NS);
            n_sent = n_sent + 1;
        end
    endtask
    task pkt0(input [7:0] op);
        begin send_byte(op); send_byte(8'd0); end
    endtask
    task pkt1(input [7:0] op, input [7:0] a);
        begin send_byte(op); send_byte(8'd1); send_byte(a); end
    endtask
    // Signed 16-bit coordinates, low byte first; c is the color.
    task pkt_xy(input [7:0] op, input integer x0, input integer y0, input integer x1, input integer y1, input [7:0] c);
        begin
            send_byte(op); send_byte(8'd9);
            send_byte(x0[7:0]); send_byte(x0[15:8]); send_byte(y0[7:0]); send_byte(y0[15:8]);
            send_byte(x1[7:0]); send_byte(x1[15:8]); send_byte(y1[7:0]); send_byte(y1[15:8]);
            send_byte(c);
        end
    endtask
    task plot(input integer px, input integer py, input [7:0] c);
        begin
            send_byte(8'h03); send_byte(8'd5);
            send_byte(px[7:0]); send_byte(px[15:8]); send_byte(py[7:0]); send_byte(py[15:8]); send_byte(c);
        end
    endtask
    task wait_replies(input integer n);
        integer t0;
        begin
            t0 = $time;
            while (n_got < n && ($time - t0) < 100_000_000) @(negedge clk_sys);
        end
    endtask

    // The same lists as ref_flip.py's PICTURE_A and PICTURE_B.
    task picture_a;
        begin
            pkt1(8'h02, 8'h30);                                  // CLEAR
            pkt_xy(8'h05, 40, 30, 279, 209, 8'h80);              // FILL_RECT
            pkt_xy(8'h06, 0, 0, 319, 239, 8'h01);                // DRAW_LINE
            pkt_xy(8'h06, 319, 0, 0, 239, 8'h01);
        end
    endtask
    task picture_b;
        begin
            pkt_xy(8'h05, 100, 80, 219, 159, 8'hC8);
            pkt_xy(8'h06, 0, 120, 319, 120, 8'hF0);
            plot(160, 120, 8'h02);
        end
    endtask

    initial begin
        repeat (10) @(negedge clk_sys);
        rst = 1'b0;
        wait (y == 10'd10);
        repeat (100) @(negedge clk_sys);

        picture_a; pkt0(8'h07);                 // FLIP: A goes on the screen at the next blanking
        picture_b; pkt0(8'h04);                 // drawn into the other buffer; FB_CRC answers after the swap
        wait_replies(4);
        arm_drawn = 1'b1;
        wait (done_drawn);

        pkt0(8'h07); pkt0(8'h04);               // second FLIP
        wait_replies(8);
        arm_flipped = 1'b1;
        wait (done_flipped);

        pkt0(8'h07); pkt0(8'h04);               // third FLIP
        wait_replies(12);
        arm_again = 1'b1;
        wait (done_again);
        #1;
        if (err_drawn || err_flipped || err_again)
            $display("FAIL: tb_frame_flip (frame errors, see frame_dump lines above)");
        else if (n_got != 12)
            $display("FAIL: tb_frame_flip (%0d reply bytes, expected 12: an FB_CRC after a FLIP never answered?)", n_got);
        else if (overflow === 1'b1)
            $display("FAIL: tb_frame_flip (o_overflow rose)");
        else
            $display("PASS: tb_frame_flip (%0d bytes sent, 3 replies; build/frame_flip_{drawn,flipped,again}.ppm written)", n_sent);
        $finish;
    end

    initial begin : watchdog
        #1_500_000_000;
        $display("FAIL: tb_frame_flip watchdog timeout (sent %0d bytes, %0d reply bytes; stuck in a FLIP?)", n_sent, n_got);
        $finish;
    end
endmodule
`default_nettype wire
