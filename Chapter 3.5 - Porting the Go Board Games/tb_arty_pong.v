// tb_arty_pong -- self-checking testbench for Step 3.5: Pong on the Arty Z7
// over HDMI.
//
// Design under test: projects/3.5-pong/top_artyz7.v (see "Testbench
// requirements" in Chapter 3.5) with your clk_gen, pong_game, pong_render,
// synchronizer, vga_timing, tmds_encoder and hdmi_tx below it. The Xilinx
// primitives come from the book's models in sim/ (Chapters 3.2 and 3.4),
// and the video is received by sim/hdmi_rx_model.v (this chapter).
//
// The testbench presses buttons and watches the TMDS pairs, nothing else.
// To make frames cheap it overrides the top's vertical timing to a 5-line
// frame (V_VISIBLE 2, V_FRONT 1, V_SYNC 1, V_BACK 1): the visible picture
// is logical row 0 of the 320x240 game, the top edge of the court. Lines
// keep their 640x480 timing. The game must still tick once per frame, at
// the start of vertical blanking, so it plays 60 ticks in under 10 ms.
//
//   A. From power-up it holds btn[0] (left paddle up) and btn[2] (right
//      paddle up). After 40 frames both paddles have reached y = 0 (the
//      centred paddles need 35 ticks): row 0 must show both, white, plus
//      the net's top dash in grey (3'b011 -> 8'h6D), with the ball still
//      waiting in the centre.
//   B. It releases btn[0] and presses btn[1] (left down): after 3 frames
//      only the right paddle is in row 0.
//   C. It releases btn[2] and presses btn[3] (right down): after 3 frames
//      neither is.
// Each check compares one whole frame, clock for clock (de, syncs, CTL
// bits and colors), lined up on vsync, so any pipeline latency passes.
// Finally led must be 0000 (both scores 0) with sw[0] at 0 and at 1.
//
// Runs in about two minutes. Ends with one PASS or FAIL line. +vcd writes
// tb_arty_pong.vcd (large).

`timescale 1ns/1ps
`default_nettype none

module tb_arty_pong;
    localparam integer H_VIS = 640, H_FP = 16, H_SW = 96, H_TOT = 800;
    localparam integer V_VIS = 2, V_FP = 1, V_SW = 1, V_BP = 1;
    localparam integer V_TOT  = V_VIS + V_FP + V_SW + V_BP;   // 5 lines
    localparam integer NFRAME = H_TOT * V_TOT;                // 4,000
    localparam integer NREC   = 2 * NFRAME + 16;

    integer errors = 0;
    task err(input string msg);
        begin
            if (errors < 15) $display("ERROR: %0s", msg);
            errors = errors + 1;
        end
    endtask
    task finish_fail(input string msg);
        begin
            err(msg);
            $display("FAIL: tb_arty_pong, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
            $finish;
        end
    endtask

    // ===================================================== DUT
    reg        clk = 1'b0;
    always #4 clk = ~clk;                      // 125 MHz board clock
    reg  [3:0] btn = 4'b0101;                  // phase A from power-up
    reg  [1:0] sw  = 2'b00;
    wire [3:0] led;
    wire       tx_clk_p, tx_clk_n;
    wire [2:0] tx_d_p, tx_d_n;

    top_artyz7 #(
        .V_VISIBLE (V_VIS),
        .V_FRONT   (V_FP),
        .V_SYNC    (V_SW),
        .V_BACK    (V_BP)
    ) dut (
        .clk           (clk),
        .btn           (btn),
        .sw            (sw),
        .led           (led),
        .hdmi_tx_clk_p (tx_clk_p),
        .hdmi_tx_clk_n (tx_clk_n),
        .hdmi_tx_d_p   (tx_d_p),
        .hdmi_tx_d_n   (tx_d_n)
    );

    wire       p_clk, p_locked, p_ok, p_de, p_hs, p_vs;
    wire [3:0] p_ctl;
    wire [7:0] p_r, p_g, p_b;
    hdmi_rx_model u_rx (
        .i_clk_p(tx_clk_p), .i_clk_n(tx_clk_n), .i_d_p(tx_d_p), .i_d_n(tx_d_n),
        .o_clk(p_clk), .o_locked(p_locked), .o_ok(p_ok),
        .o_de(p_de), .o_hsync(p_hs), .o_vsync(p_vs), .o_ctl(p_ctl),
        .o_red(p_r), .o_grn(p_g), .o_blu(p_b));

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_arty_pong.vcd");
            $dumpvars(0, tb_arty_pong);
        end
    end

    // Frames seen by the receiver: count vsync falling edges.
    integer n_frames = 0;
    reg     last_vs = 1'b1;
    always @(posedge p_clk) begin
        if (p_ok && !p_de) begin
            if (last_vs && !p_vs) n_frames = n_frames + 1;
            last_vs = p_vs;
        end
    end

    // ===================================================== expected video
    // Logical row 0 of pong_render: net dash at x 159-160 (grey 3,3,3),
    // paddles at x 8-11 and 308-311 (white) when they are at y = 0.
    function exp_de(input integer p);
        exp_de = (p % H_TOT) < H_VIS && (p / H_TOT) < V_VIS;
    endfunction
    function exp_hs(input integer p);
        exp_hs = !((p % H_TOT) >= H_VIS + H_FP && (p % H_TOT) < H_VIS + H_FP + H_SW);
    endfunction
    function exp_vs(input integer p);
        exp_vs = !((p / H_TOT) >= V_VIS + V_FP && (p / H_TOT) < V_VIS + V_FP + V_SW);
    endfunction
    function [23:0] exp_rgb(input integer p, input lt, input rt);
        integer lx;
        begin
            lx = (p % H_TOT) / 2;
            if (lt && lx >= 8 && lx < 12)        exp_rgb = 24'hFFFFFF;
            else if (rt && lx >= 308 && lx < 312) exp_rgb = 24'hFFFFFF;
            else if (lx == 159 || lx == 160)     exp_rgb = 24'h6D6D6D;
            else                                 exp_rgb = 24'h000000;
        end
    endfunction

    // ===================================================== recording
    reg        rk_ok  [0:NREC-1];
    reg        rk_de  [0:NREC-1];
    reg  [1:0] rk_sy  [0:NREC-1];
    reg  [3:0] rk_ctl [0:NREC-1];
    reg [23:0] rk_rgb [0:NREC-1];
    integer    n_rec = -1;
    always @(posedge p_clk) begin
        if (n_rec >= 0 && n_rec < NREC) begin
            rk_ok[n_rec]  = p_ok && p_locked;
            rk_de[n_rec]  = p_de;
            rk_sy[n_rec]  = {p_vs, p_hs};
            rk_ctl[n_rec] = p_ctl;
            rk_rgb[n_rec] = {p_r, p_g, p_b};
            n_rec = n_rec + 1;
        end
    end

    function integer find_vsync_k(input integer nwords);
        integer n, lv, found;
        begin
            find_vsync_k = -1; lv = -1; found = 0;
            for (n = 0; n < nwords && !found; n = n + 1)
                if (rk_ok[n] && !rk_de[n]) begin
                    if (lv == 1 && rk_sy[n][1] == 1'b0) begin find_vsync_k = n; found = 1; end
                    lv = rk_sy[n][1];
                end
        end
    endfunction

    // Record a frame and compare it. lt/rt: left/right paddle expected in row 0.
    task check_frame(input string phase, input lt, input rt);
        integer n, n0, p, x, mis, nrgb, got_l, got_r, net_bad;
        reg [23:0] gr, er;
        begin
            u_rx.clear_counts();
            n_rec = 0;
            wait (n_rec >= NREC);
            n0 = find_vsync_k(NREC - NFRAME);
            if (n0 < 0)
                finish_fail($sformatf("%0s: no vsync falling edge on channel 0. The test sets the top's V_VISIBLE=%0d, V_FRONT=%0d, V_SYNC=%0d, V_BACK=%0d: pass them on to vga_timing",
                                      phase, V_VIS, V_FP, V_SW, V_BP));
            mis = 0; nrgb = 0; net_bad = 0;
            got_l = 1; got_r = 1;
            for (n = 0; n < NFRAME; n = n + 1) begin
                p = (n + (V_VIS + V_FP) * H_TOT) % NFRAME;
                x = p % H_TOT;
                if (!rk_ok[n0 + n]) begin
                    mis = mis + 1;
                    if (mis <= 2) err($sformatf("%0s: x=%0d y=%0d: the receiver could not decode this pixel clock", phase, x, p / H_TOT));
                end else if (rk_de[n0 + n] !== exp_de(p) || rk_sy[n0 + n] !== {exp_vs(p), exp_hs(p)} ||
                             (!rk_de[n0 + n] && rk_ctl[n0 + n] !== 4'd0)) begin
                    mis = mis + 1;
                    if (mis <= 2)
                        err($sformatf("%0s: x=%0d y=%0d: de=%b {vsync,hsync}=%b CTL=%b, expected de=%b %b 0000",
                                      phase, x, p / H_TOT, rk_de[n0 + n], rk_sy[n0 + n], rk_ctl[n0 + n],
                                      exp_de(p), {exp_vs(p), exp_hs(p)}));
                end else if (rk_de[n0 + n]) begin
                    gr = rk_rgb[n0 + n];
                    er = exp_rgb(p, lt, rt);
                    if (x / 2 >= 8 && x / 2 < 12 && gr === 24'h000000) got_l = 0;
                    if (x / 2 >= 308 && x / 2 < 312 && gr === 24'h000000) got_r = 0;
                    if (gr !== er) begin
                        mis = mis + 1; nrgb = nrgb + 1;
                        if (x / 2 == 159 || x / 2 == 160) net_bad = net_bad + 1;
                        if (nrgb <= 3)
                            err($sformatf("%0s: pixel x=%0d y=%0d: RGB %02h %02h %02h, expected %02h %02h %02h%0s",
                                          phase, x, p / H_TOT, gr[23:16], gr[15:8], gr[7:0], er[23:16], er[15:8], er[7:0],
                                          (gr == 24'hE0E0E0 || gr == 24'h606060) ? " (expand 3 to 8 bits by repeating the bits: {c, c, c[2:1]})" : ""));
                    end
                end
            end
            if (nrgb > 0 && (got_l != lt || got_r != rt)) begin
                err($sformatf("%0s: buttons btn[3:0] = %b; expected the %0s in row 0, but it shows the %0s",
                              phase, btn,
                              lt && rt ? "left and right paddles" : lt ? "left paddle only" : rt ? "right paddle only" : "paddles not",
                              got_l && got_r ? "left and right paddles" : got_l ? "left paddle only" : got_r ? "right paddle only" : "paddles not"));
                if (!got_l && !got_r && lt && rt)
                    err("neither paddle moved up. Check: btn[0] = left up, btn[1] = left down, btn[2] = right up, btn[3] = right down, all active high (pressed = 1) through a synchronizer and no debounce (a frame here is 160 us); and the game tick comes once per frame, at x == 0 and y == V_VISIBLE (the top's parameter, not the number 480), once clk_pix is good");
            end
            if (mis > 0)
                err($sformatf("%0s: %0d of %0d pixel clocks wrong (colors %0d)", phase, mis, NFRAME, nrgb));
            else
                $display("tb_arty_pong: %0s OK after %0d frames: %0s", phase, n_frames,
                         lt && rt ? "both paddles at the top" : lt ? "left paddle at the top" : rt ? "right paddle at the top" : "no paddle at the top");
        end
    endtask

    task wait_frames(input integer nf);
        integer target;
        real t_end;
        begin
            target = n_frames + nf;
            t_end = $realtime + (nf + 2) * NFRAME * 40.0;
            while (n_frames < target && $realtime < t_end) #1000;
            if (n_frames < target)
                finish_fail($sformatf("only %0d of %0d frames in %0.0f us: the receiver lost the video", n_frames - target + nf, nf, (nf + 2) * NFRAME * 0.04));
        end
    endtask

    // ===================================================== main
    real t_end;
    initial begin
        $display("tb_arty_pong: waiting for the HDMI receiver to lock (holding btn[0] and btn[2])");
        t_end = $realtime + 200000.0;
        while (!p_locked && $realtime < t_end) #100;
        if (!p_locked)
            finish_fail("no DVI video on the HDMI pairs after 200 us: run tb_hdmi (Chapter 3.4) first; this test reuses the same clk_gen and hdmi_tx");
        $display("tb_arty_pong: HDMI locked at %0.1f us", $realtime / 1000.0);

        wait_frames(40);
        check_frame("A (btn[0] left up, btn[2] right up)", 1'b1, 1'b1);

        btn = 4'b0110;
        wait_frames(3);
        check_frame("B (btn[1] left down, btn[2] right up)", 1'b0, 1'b1);

        btn = 4'b1010;
        wait_frames(3);
        check_frame("C (btn[1] left down, btn[3] right down)", 1'b0, 1'b0);

        if (u_rx.n_clk_bad > 0 || u_rx.n_pn > 0)
            err($sformatf("HDMI pairs: %0d wrong clock words, %0d P/N samples not complementary", u_rx.n_clk_bad, u_rx.n_pn));
        sw = 2'b00; #200;
        if (led !== 4'b0000) err($sformatf("led = %b with sw[0] = 0; expected 0000 (left score 0)", led));
        sw = 2'b01; #200;
        if (led !== 4'b0000) err($sformatf("led = %b with sw[0] = 1; expected 0000 (right score 0)", led));

        if (errors == 0) $display("PASS: tb_arty_pong (buttons move the right paddles; HDMI frames match the court's top row)");
        else             $display("FAIL: tb_arty_pong, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
        $finish;
    end

    // Watchdog: the run needs about 8 ms of simulated time.
    initial begin
        #20_000_000;
        $display("FAIL: tb_arty_pong, watchdog timeout at %0t", $time);
        $finish;
    end
endmodule

`default_nettype wire
