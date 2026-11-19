// tb_arty_scroll -- self-checking testbench for Step 3.5: Step 2.5's
// uart_ppu on the Arty Z7, UART on Pmod JA, video over HDMI.
//
// Design under test: projects/3.5-scroll/top_artyz7.v (see "Testbench
// requirements" in Chapter 3.5) with your clk_gen, uart_ppu, vga_timing,
// tmds_encoder and hdmi_tx below it. The Xilinx primitives come from the
// book's models in sim/ (Chapters 3.2 and 3.4), and the video is received
// by sim/hdmi_rx_model.v (this chapter).
//
// The testbench drives only the pins a person would touch:
//   1. clk, a 125 MHz board clock. It waits for the HDMI receiver to lock.
//   2. uart_rx, the serial line from the USB-serial adapter (Pmod JA pin
//      1). It sends Step 0.5 packets at BAUD = 781,250 (32 pixel clocks per
//      bit), passed to the top as a parameter, so a uart_ppu that does not
//      get CLK_HZ = 25,000,000 (the clock it really runs on) cannot follow:
//        W 0x00 0x53   LEDS: led[3:0] must show 0011 within 4 bit times;
//        R 0x00        uart_tx (Pmod JA pin 2) must answer 0x53 at BAUD;
//        palette entries 0-3, map row 0 (40 cells, tiles 1-31 from
//        rtl/common/tiles.hex), and the four sprites' ATTR bytes (0, off),
//        all through ADDR_LO, ADDR_HI and DATA.
//   3. The four TMDS pairs. Two whole frames must match, clock for clock,
//      the picture computed here: 640-pixel lines with the 640x480 line
//      timing, the syncs on channel 0, and in the visible lines the map's
//      tiles drawn 2x2 in the palette colors, each 3-bit channel expanded
//      to 8 bits by repeating its bits (3'b011 -> 8'h6D). Any pipeline
//      latency is accepted: the frames are lined up on vsync.
//
// To keep the run short the top's vertical timing is overridden to a
// 17-line frame (V_VISIBLE 8, V_FRONT 3, V_SYNC 2, V_BACK 4), which shows
// the top four logical rows of the map. The frame never reaches line 480,
// so the scroll and sprite registers are never committed and the test does
// not use them. Tile graphics are read from TILES (default
// ../../rtl/common/tiles.hex, right when vvp runs in build/sim); override
// with +tiles=<path>.
//
// Runs in about a minute. Ends with one PASS or FAIL line. +vcd writes
// tb_arty_scroll.vcd (large).

`timescale 1ns/1ps
`default_nettype none

module tb_arty_scroll;
    localparam integer CLK_HZ = 25_000_000, BAUD = 781_250;
    localparam real    T_BIT  = 1.0e9 / BAUD;               // 1,280 ns
    localparam integer H_VIS = 640, H_FP = 16, H_SW = 96, H_TOT = 800;
    localparam integer V_VIS = 8, V_FP = 3, V_SW = 2, V_BP = 4;
    localparam integer V_TOT  = V_VIS + V_FP + V_SW + V_BP;  // 17 lines
    localparam integer NFRAME = H_TOT * V_TOT;               // 13,600
    localparam integer NCHK   = 2 * NFRAME;
    localparam integer NREC   = NCHK + NFRAME + 16;

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
            $display("FAIL: tb_arty_scroll, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
            $finish;
        end
    endtask

    // ===================================================== DUT
    reg        clk = 1'b0;
    always #4 clk = ~clk;                      // 125 MHz board clock
    reg        r_rx = 1'b1;                    // adapter TXD -> FPGA
    wire       uart_rx = r_rx;                 // a net, so a wrong port direction shows as X
    wire       uart_tx;
    wire [3:0] led;
    wire       tx_clk_p, tx_clk_n;
    wire [2:0] tx_d_p, tx_d_n;

    top_artyz7 #(
        .V_VISIBLE (V_VIS),
        .V_FRONT   (V_FP),
        .V_SYNC    (V_SW),
        .V_BACK    (V_BP),
        .BAUD      (BAUD)
    ) dut (
        .clk           (clk),
        .led           (led),
        .uart_rx       (uart_rx),
        .uart_tx       (uart_tx),
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
            $dumpfile("tb_arty_scroll.vcd");
            $dumpvars(0, tb_arty_scroll);
        end
    end

    // ===================================================== the scene
    // Palette entries 0-3, 9-bit {r, g, b}: every 3-bit level appears, and
    // no entry has two equal channels, so a swap or a bad expansion shows.
    function [8:0] pal(input integer i);
        case (i & 3)
            0: pal = {3'd1, 3'd2, 3'd3};
            1: pal = {3'd4, 3'd5, 3'd6};
            2: pal = {3'd7, 3'd0, 3'd5};
            default: pal = {3'd6, 3'd3, 3'd7};
        endcase
    endfunction
    function [7:0] tile_of(input integer col);   // map row 0, column col
        tile_of = 1 + col % 31;
    endfunction
    function [7:0] x8(input [2:0] c);            // the expansion under test
        x8 = {c, c, c[2:1]};
    endfunction
    function [23:0] rgb8(input [8:0] p);
        rgb8 = {x8(p[8:6]), x8(p[5:3]), x8(p[2:0])};
    endfunction

    reg [15:0] tiles [0:511];
    reg [8*256-1:0] tiles_path;
    integer j;
    initial begin
        if (!$value$plusargs("tiles=%s", tiles_path)) tiles_path = "../../rtl/common/tiles.hex";
        for (j = 0; j < 512; j = j + 1) tiles[j] = 16'hxxxx;
        $readmemh(tiles_path, tiles);
    end

    // ===================================================== expected video
    function exp_de(input integer p);
        exp_de = (p % H_TOT) < H_VIS && (p / H_TOT) < V_VIS;
    endfunction
    function exp_hs(input integer p);
        exp_hs = !((p % H_TOT) >= H_VIS + H_FP && (p % H_TOT) < H_VIS + H_FP + H_SW);
    endfunction
    function exp_vs(input integer p);
        exp_vs = !((p / H_TOT) >= V_VIS + V_FP && (p / H_TOT) < V_VIS + V_FP + V_SW);
    endfunction
    function [1:0] exp_idx(input integer p);
        integer lx, ly;
        reg [15:0] row;
        begin
            lx = (p % H_TOT) / 2; ly = (p / H_TOT) / 2;
            row = tiles[tile_of(lx / 8) * 8 + ly % 8];
            exp_idx = row[15 - 2 * (lx % 8) -: 2];
        end
    endfunction
    function [23:0] exp_rgb(input integer p);
        exp_rgb = rgb8(pal(exp_idx(p)));
    endfunction

    // ===================================================== UART
    task send_byte(input [7:0] d);
        integer b;
        begin
            r_rx = 1'b0; #(T_BIT);
            for (b = 0; b < 8; b = b + 1) begin r_rx = d[b]; #(T_BIT); end
            r_rx = 1'b1; #(T_BIT);
        end
    endtask
    task wr(input [7:0] a, input [7:0] d);
        begin send_byte(8'h57); send_byte(a); send_byte(d); end
    endtask

    // Receiver on uart_tx: frames bytes at BAUD; remembers the start-bit
    // length of the last byte, to tell a wrong bit rate from silence.
    reg [7:0] rx_byte;
    integer   rx_count = 0, rx_bad = 0;
    real      rx_start_len = 0.0;
    always @(negedge uart_tx) begin : uart_mon
        integer b;
        real t0;
        if (uart_tx === 1'b0) begin
            t0 = $realtime;
            @(uart_tx);
            rx_start_len = $realtime - t0;      // 0x53 starts with a 1 bit
            for (b = 0; b < 8; b = b + 1) begin
                if (t0 + (1.5 + b) * T_BIT > $realtime) #(t0 + (1.5 + b) * T_BIT - $realtime);
                rx_byte[b] = uart_tx;
            end
            if (t0 + 9.5 * T_BIT > $realtime) #(t0 + 9.5 * T_BIT - $realtime);
            if (uart_tx !== 1'b1) rx_bad = rx_bad + 1;
            rx_count = rx_count + 1;
        end
    end

    // ===================================================== recording
    reg        rk_ok  [0:NREC-1];
    reg        rk_de  [0:NREC-1];
    reg  [1:0] rk_sy  [0:NREC-1];      // {vsync, hsync}
    reg  [3:0] rk_ctl [0:NREC-1];
    reg [23:0] rk_rgb [0:NREC-1];
    integer    n_rec = -1;             // -1: not recording
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
        integer n, last_vs, found;
        begin
            find_vsync_k = -1; last_vs = -1; found = 0;
            for (n = 0; n < nwords && !found; n = n + 1)
                if (rk_ok[n] && !rk_de[n]) begin
                    if (last_vs == 1 && rk_sy[n][1] == 1'b0) begin
                        find_vsync_k = n;
                        found = 1;
                    end
                    last_vs = rk_sy[n][1];
                end
        end
    endfunction

    // Compare NCHK words from n0, which must be the first word of vsync.
    integer n_mis_ok, n_mis_de, n_mis_sync, n_mis_ctl, n_mis_rgb;
    task compare(input integer n0, input integer verbose, output integer mis);
        integer n, p, x, y;
        reg [23:0] er, gr, e3;
        reg [8:0]  pe;
        begin
            mis = 0; n_mis_ok = 0; n_mis_de = 0; n_mis_sync = 0; n_mis_ctl = 0; n_mis_rgb = 0;
            for (n = 0; n < NCHK; n = n + 1) begin
                p = (n + (V_VIS + V_FP) * H_TOT) % NFRAME;
                x = p % H_TOT; y = p / H_TOT;
                if (!rk_ok[n0 + n]) begin
                    mis = mis + 1; n_mis_ok = n_mis_ok + 1;
                    if (verbose && n_mis_ok <= 2)
                        err($sformatf("video: x=%0d y=%0d: the receiver could not decode this pixel clock (X bits or a damaged TMDS symbol)", x, y));
                end else if (rk_de[n0 + n] !== exp_de(p)) begin
                    mis = mis + 1; n_mis_de = n_mis_de + 1;
                    if (verbose && n_mis_de <= 3)
                        err($sformatf("video: x=%0d y=%0d: de=%b, expected %b (640x480 line timing, V_VISIBLE=%0d lines)",
                                      x, y, rk_de[n0 + n], exp_de(p), V_VIS));
                end else if (rk_sy[n0 + n] !== {exp_vs(p), exp_hs(p)}) begin
                    mis = mis + 1; n_mis_sync = n_mis_sync + 1;
                    if (verbose && n_mis_sync <= 3)
                        err($sformatf("video: x=%0d y=%0d: {vsync,hsync} = %b, expected %b", x, y, rk_sy[n0 + n], {exp_vs(p), exp_hs(p)}));
                end else if (!rk_de[n0 + n] && rk_ctl[n0 + n] !== 4'd0) begin
                    mis = mis + 1; n_mis_ctl = n_mis_ctl + 1;
                    if (verbose && n_mis_ctl <= 2)
                        err($sformatf("video: x=%0d y=%0d: CTL bits on channels 2/1 are %b, expected 0000", x, y, rk_ctl[n0 + n]));
                end else if (rk_de[n0 + n]) begin
                    er = exp_rgb(p); gr = rk_rgb[n0 + n];
                    if (gr !== er) begin
                        mis = mis + 1; n_mis_rgb = n_mis_rgb + 1;
                        if (verbose && n_mis_rgb <= 3) begin
                            pe = pal(exp_idx(p));
                            e3 = {pe[8:6], 5'b0, pe[5:3], 5'b0, pe[2:0], 5'b0};
                            err($sformatf("video: pixel x=%0d y=%0d (tile %0d, color index %0d = 3-bit %0d,%0d,%0d): RGB %02h %02h %02h, expected %02h %02h %02h%0s",
                                          x, y, tile_of(x / 16), exp_idx(p), pe[8:6], pe[5:3], pe[2:0],
                                          gr[23:16], gr[15:8], gr[7:0], er[23:16], er[15:8], er[7:0],
                                          (gr == e3) ? " (the 3-bit colors were shifted left; repeat the bits instead: {c, c, c[2:1]})" :
                                          (gr == {er[7:0], er[15:8], er[23:16]}) ? " (red and blue swapped: red goes to hdmi_tx i_red, which is channel 2)" :
                                          (gr[23:16] == er[15:8] && gr[15:8] == er[23:16]) ? " (red and green swapped)" : ""));
                        end
                    end
                end
            end
        end
    endtask

    // Colors a constant number of clocks off from de?
    task diagnose_shift(input integer n0);
        integer s, n, p, q, mis, best_s, best_mis;
        begin
            best_s = 0; best_mis = -1;
            for (s = -4; s <= 4; s = s + 1) begin
                mis = 0;
                for (n = 8; n < NFRAME - 8; n = n + 1) begin
                    p = (n + (V_VIS + V_FP) * H_TOT) % NFRAME;
                    q = (p - s + NFRAME) % NFRAME;
                    if (exp_de(p) && exp_de(q) && rk_ok[n0 + n] && rk_rgb[n0 + n] !== exp_rgb(q)) mis = mis + 1;
                end
                if (best_mis < 0 || mis < best_mis) begin best_mis = mis; best_s = s; end
            end
            if (best_s != 0 && best_mis == 0)
                err($sformatf("video: the colors arrive %0d pixel clock%0s %0s than de and the syncs. Give the color, de, hsync and vsync from uart_ppu the same register stages in the top",
                              best_s > 0 ? best_s : -best_s, (best_s == 1 || best_s == -1) ? "" : "s",
                              best_s > 0 ? "later" : "earlier"));
        end
    endtask

    // ===================================================== main
    integer c, n, k, mis, uart_errors;
    real    t_end;
    initial begin
        #1;
        if (tiles[8] === 16'hxxxx || tiles[255] === 16'hxxxx)
            finish_fail($sformatf("could not read the tile file %0s. Run vvp in build/sim, or pass +tiles=<path to rtl/common/tiles.hex>", tiles_path));

        // ---- start-up
        $display("tb_arty_scroll: waiting for the HDMI receiver to lock");
        t_end = $realtime + 200000.0;
        while (!p_locked && $realtime < t_end) #100;
        if (!p_locked)
            finish_fail("no DVI video on the HDMI pairs after 200 us: run tb_hdmi (Chapter 3.4) first; this test reuses the same clk_gen and hdmi_tx");
        $display("tb_arty_scroll: HDMI locked at %0.1f us", $realtime / 1000.0);
        if (uart_tx !== 1'b1)
            err($sformatf("uart_tx is %b while idle; a UART line idles high (is uart_tx an output driven by uart_ppu's o_tx?)", uart_tx));
        if (uart_rx !== 1'b1)
            err($sformatf("uart_rx reads %b while the testbench drives 1: the top drives its own uart_rx pin. uart_rx is the FPGA's input (adapter TXD -> Pmod JA pin 1), uart_tx its output",
                          uart_rx));

        // ---- registers over the Pmod UART
        wr(8'h00, 8'h53);
        #(4 * T_BIT);
        if (led !== 4'b0011) begin
            if (led === 4'b1100)
                err("after W 00 53: led = 1100, expected 0011: led[0] is register 0 bit 0 (LD0)");
            else
                err($sformatf("after W 00 53 at %0d baud: led = %b, expected 0011. uart_ppu did not take the packet. Check that it gets CLK_HZ = 25_000_000 (it runs on clk_pix, not the 125 MHz clk), that the top passes BAUD down, and that uart_rx reaches uart_ppu's i_rx",
                              BAUD, led));
        end
        n = rx_count;
        send_byte(8'h52); send_byte(8'h00);
        t_end = $realtime + 200.0 * T_BIT;
        while (rx_count == n && $realtime < t_end) #100;
        if (rx_count == n)
            err("R 00 got no reply on uart_tx within 200 bit times (uart_ppu's o_tx must drive uart_tx, Pmod JA pin 2)");
        else begin
            if (rx_start_len > 1.25 * T_BIT || rx_start_len < 0.75 * T_BIT)
                err($sformatf("the reply's start bit lasts %0.0f ns; at %0d baud a bit is %0.0f ns. Is uart_ppu's CLK_HZ the 25 MHz pixel clock it runs on?",
                              rx_start_len, BAUD, T_BIT));
            else if (rx_byte !== 8'h53 || rx_bad != 0)
                err($sformatf("R 00 replied 0x%02h, expected 0x53%0s", rx_byte, rx_bad != 0 ? " (bad stop bit)" : ""));
        end
        if (rx_count > n + 1) err($sformatf("R 00 got %0d reply bytes, expected 1", rx_count - n));

        uart_errors = errors;

        // ---- the scene: palette 0-3, map row 0, sprites off
        wr(8'h05, 8'h00); wr(8'h06, 8'h0C);
        for (c = 0; c < 4; c = c + 1) begin
            wr(8'h07, {7'd0, pal(c) >> 8});
            wr(8'h07, pal(c) & 8'hFF);
        end
        wr(8'h05, 8'h00); wr(8'h06, 8'h00);
        for (c = 0; c < 40; c = c + 1) wr(8'h07, tile_of(c));
        for (c = 0; c < 4; c = c + 1) begin
            wr(8'h05, 8'h14 + 8 * c); wr(8'h06, 8'h0C); wr(8'h07, 8'h00);
        end
        #(4 * T_BIT);
        $display("tb_arty_scroll: setup sent at %0.1f us; recording %0d pixel clocks", $realtime / 1000.0, NREC);

        // ---- video
        u_rx.clear_counts();
        n_rec = 0;
        wait (n_rec >= NREC);
        if (u_rx.n_clk_bad > 0)
            err($sformatf("%0d words on the clock pair were not 1111100000", u_rx.n_clk_bad));
        if (u_rx.n_pn > 0)
            err($sformatf("%0d bit samples where a pair's N pin was not the inverse of its P pin", u_rx.n_pn));
        if (u_rx.n_dis > 0)
            err($sformatf("%0d pixel clocks where the three channels disagreed on data or control", u_rx.n_dis));
        k = find_vsync_k(NREC - NCHK);
        if (k < 0)
            finish_fail($sformatf("video: no vsync falling edge on channel 0. The test sets the top's V_VISIBLE=%0d, V_FRONT=%0d, V_SYNC=%0d, V_BACK=%0d: pass them on to vga_timing",
                                  V_VIS, V_FP, V_SW, V_BP));
        compare(k, 1, mis);
        if (n_mis_rgb > 0) diagnose_shift(k);
        if (mis > 0 && uart_errors > 0)
            err("video: the palette and the map are written over the UART, so fix the UART errors first");
        else if (n_mis_ok > 0)
            err("video: X bits in the picture although the UART works: a memory was not loaded. Did $readmemh find the tile and sprite files? The top passes TILE_FILE(\"../../rtl/common/tiles.hex\"), right both for yosys in projects/3.5-scroll and for vvp in build/sim");
        if (mis > 0)
            err($sformatf("video: %0d of %0d pixel clocks wrong (undecodable %0d, de %0d, syncs %0d, CTL %0d, color %0d)",
                          mis, NCHK, n_mis_ok, n_mis_de, n_mis_sync, n_mis_ctl, n_mis_rgb));
        else
            $display("tb_arty_scroll: two frames OK: map row 0 in palette colors, 8-bit expansion, syncs and blanking as expected");

        if (errors == 0) $display("PASS: tb_arty_scroll (UART on the Pmod pins at %0d baud, LEDs, read-back and two HDMI frames of the tile map)", BAUD);
        else             $display("FAIL: tb_arty_scroll, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
        $finish;
    end

    // Watchdog: the run needs about 4.5 ms of simulated time.
    initial begin
        #12_000_000;
        $display("FAIL: tb_arty_scroll, watchdog timeout at %0t", $time);
        $finish;
    end
endmodule

`default_nettype wire
