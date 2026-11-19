// tb_flip_sync.v -- self-checking testbench for FLIP and double buffering (Chapter 4.5).
//
// The whole chain, as on the board: this testbench is the host and sends
// sim/flipsync_stream.hex into uart_gpu as real 8N1 serial at 2.5 Mbaud;
// uart_gpu draws into fb_scanout; fb_scanout is scanned by a real vga_timing
// on clk_pix (25 MHz) while the engine runs on clk_sys. The two clocks are
// unrelated: clk_sys has a period of 19.936 ns, not 20, so the phase between
// them drifts through every value (+syspd_ps=20000 makes it exactly 2:1).
//
// The stream (from `.venv/bin/python sim/py/ref_flip_sync.py sim`, which uses
// host/gpu_model.py) draws a different picture into the back buffer, checks it
// with FB_CRC, and FLIPs, 10 times: 5 with the request at different places
// in the frame, and 5 whose request toggles 30, 3, 2, 1 and 0 pixel clocks
// before the start of vertical blanking. Right behind each FLIP it queues a PLOT, a
// FILL_RECT and an FB_CRC. Malformed FLIPs (length 1 and 2) are mixed in,
// and one FLIP gets an i_rst pulse while it waits for its swap.
// To time a request, the testbench measures how long your design takes from
// the start of a FLIP packet to the o_flip_req toggle, and starts the next
// timed FLIP that much earlier.
//
// The testbench watches the pixels leaving fb_scanout and gives every
// complete frame a signature (sum of color * (2p + 1) over the 307,200
// visible pixels, mod 2**32). Checked:
//
//   1. Every frame is exactly one picture: its signature equals the one for
//      "the picture on the screen after j FLIPs" in sim/flipsync_sigs.hex.
//      A swap in the visible area makes a frame of two pictures, which
//      matches none.
//   2. From one frame to the next j stays or rises by exactly 1 (one swap
//      per blanking interval, no swap-back).
//   3. A frame never shows a picture whose FLIP had not been requested before
//      the frame's first pixel (j <= requests), and never an older one than
//      FLIPs already acknowledged before that pixel (j >= acknowledges): the
//      acknowledge only comes after the swap.
//   4. Between a FLIP request and its acknowledge the engine is stalled: no
//      framebuffer or palette write, no byte on o_tx. The acknowledge
//      arrives within 40 ms (about two frames).
//   5. Every FB_CRC reply (the back buffer, so draws went to the right
//      buffer and FLIP behaved) equals the model's. Malformed FLIPs do nothing.
//   6. o_vblank is high while the beam is in vertical blanking (lines 480-524)
//      and low otherwise, give or take 4 pixel clocks at each edge.
//   7. All FLIPs are acknowledged and the last picture ends up on the screen.
//
// Any latency is accepted: the swap may happen anywhere in blanking, and a
// request that arrives during blanking may take effect now or a frame later.
//
// Run from build/sim (make test / make sim-tb_flip_sync); files are read from
// ../../sim/ and ../../rtl/common/.
`timescale 1ns/1ps
`default_nettype none

module tb_flip_sync;
    localparam integer BAUD    = 2_500_000;
    localparam integer BIT_NS  = 1_000_000_000 / BAUD;         // 400 ns
    localparam integer FBN     = 76800;
    localparam integer PIXELS  = 640 * 480;
    localparam integer LINE480 = 480 * 800;
    localparam integer FRAME   = 800 * 525;
    localparam         DIR     = "../../";

    // ---------------- clocks ----------------
    reg clk_pix = 1'b0, clk_sys = 1'b0;
    integer sys_half_ps = 9968;
    integer syspd;
    always #20 clk_pix = ~clk_pix;                              // 25 MHz
    initial begin
        if ($value$plusargs("syspd_ps=%d", syspd)) sys_half_ps = syspd / 2;
        #3;
        forever #(sys_half_ps / 1000.0) clk_sys = ~clk_sys;
    end

    // ---------------- beam and DUT ----------------
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

    fb_scanout #(.FB_FILE({DIR, "rtl/common/fb_test.hex"}), .PAL_FILE({DIR, "rtl/common/pal_test.hex"})) u_scan (
        .i_clk_sys(clk_sys), .i_fb_buf(fb_buf),
        .i_fb_we(fb_we), .i_fb_addr(fb_addr), .i_fb_wdata(fb_wdata), .o_fb_rdata(fb_rdata),
        .i_pal_we(pal_we), .i_pal_addr(pal_addr), .i_pal_wdata(pal_wdata),
        .i_flip_req(flip_req), .o_flip_ack(flip_ack),
        .i_clk_pix(clk_pix), .i_x(x), .i_y(y), .i_de(de), .i_hsync(hs), .i_vsync(vs),
        .o_hsync(o_hs), .o_vsync(o_vs), .o_de(o_de), .o_red(o_r), .o_grn(o_g), .o_blu(o_b),
        .o_vblank(vblank));

    // ---------------- expected data ----------------
    reg [15:0]      stream  [0:8191];
    reg [7:0]       replies [0:1023];
    reg [31:0]      sigs    [0:255];
    reg [8*128-1:0] labels  [0:255];
    integer         n_sigs = 0, n_exp = 0;

    // ---------------- reply receiver (8N1 on o_tx) ----------------
    reg [7:0] got [0:1023];
    integer   n_got = 0, rx_err = 0, k;
    reg [7:0] b;
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 1'b0) begin
            if (rx_err < 3) $display("tb_flip_sync: o_tx start bit too short at %0t", $time);
            rx_err = rx_err + 1;
        end else begin
            for (k = 0; k < 8; k = k + 1) begin
                #(BIT_NS);
                b[k] = tx;
            end
            #(BIT_NS);
            if (tx !== 1'b1) begin
                if (rx_err < 3) $display("tb_flip_sync: o_tx stop bit missing at %0t", $time);
                rx_err = rx_err + 1;
            end
            if (n_got < 1024) got[n_got] = b;
            n_got = n_got + 1;
            #(BIT_NS / 4);
        end
    end

    // ---------------- sender ----------------
    integer n_sent = 0;
    reg     timeout = 1'b0;
    task send_byte(input [7:0] d);
        integer j, tw;
        begin
            @(negedge clk_sys);
            tw = $time;
            while (ready !== 1'b1 && !timeout) begin
                @(negedge clk_sys);
                if ($time - tw > 50_000_000) begin
                    $display("tb_flip_sync: o_ready stayed low for 50 ms after %0d bytes (a FLIP that never completes?). %0d reply bytes so far; the next covers: %0s",
                             n_sent, n_got, labels[n_got / 4]);
                    timeout = 1'b1;
                end
            end
            if (!timeout) begin
                rx = 1'b0; #(BIT_NS);
                for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
                rx = 1'b1; #(BIT_NS);
                n_sent = n_sent + 1;
            end
        end
    endtask

    // ---------------- reply check ----------------
    integer reply_err = 0, n_fence = 0, n_checked = 0;
    task check_replies(input integer upto);
        integer r;
        reg [31:0] want, have;
        begin
            for (r = n_checked; r + 3 < upto; r = r + 4) begin
                want = {replies[r+3], replies[r+2], replies[r+1], replies[r]};
                have = {got[r+3], got[r+2], got[r+1], got[r]};
                if (have !== want) begin
                    if (reply_err < 3)
                        $display("tb_flip_sync: FB_CRC reply %0d is %h, expected %h. It covers: %0s",
                                 r / 4 + 1, have, want, labels[r / 4]);
                    reply_err = reply_err + 1;
                end
            end
            n_checked = upto;
        end
    endtask

    // Reads a hex list of unknown length (after its comment line) into
    // stream[] (which = 0), replies[] (1) or sigs[] (2).
    task read_list(input [8*64-1:0] name, input integer which);
        integer fd, n, rc;
        reg [31:0] v;
        reg [8*200-1:0] line;
        begin
            fd = $fopen(name, "r");
            if (fd != 0) begin
                rc = $fgets(line, fd);           // the // comment line
                n = 0;
                while ($fscanf(fd, "%h\n", v) == 1) begin
                    if (which == 0) stream[n] = v[15:0];
                    else if (which == 1) replies[n] = v[7:0];
                    else sigs[n] = v;
                    n = n + 1;
                end
                $fclose(fd);
            end
        end
    endtask

    task read_labels(input [8*64-1:0] name);
        integer fd, n, rc;
        reg [8*128-1:0] line;
        begin
            fd = $fopen(name, "r");
            n = 0;
            if (fd != 0) begin
                line = 0;
                rc = $fgets(line, fd);
                while (rc > 0 && n < 256) begin
                    if (line[7:0] == 8'h0A) line = line >> 8;
                    labels[n] = line;
                    n = n + 1;
                    line = 0;
                    rc = $fgets(line, fd);
                end
                $fclose(fd);
            end
        end
    endtask

    // ---------------- FLIP handshake monitor ----------------
    // Event driven, to keep the run short: nothing here runs on every clock.
    integer n_req = 0, n_ack = 0, stall_err = 0, live_err = 0, hs_err = 0;
    integer max_lat_ns = 0, min_lat_ns = 1000000000, t_req = 0;
    reg     pend = 1'b0, started = 1'b0;    // started: after the power-up reset
    // Timed FLIPs: lead = pixel clocks from the end of a beam wait to the
    // request toggle, measured at every timed FLIP and used for the next.
    integer lead = 205, t_mark = 0, race_off = 0, n_race = 0;
    integer race_got [0:63];
    reg     marked = 1'b0, is_race = 1'b0;
    always @(flip_req) if (started) begin
        if (marked) begin
            if (is_race && n_race < 64) begin
                race_got[n_race] = race_off - lead + 1 + ($time - t_mark) / 40;
                n_race = n_race + 1;
            end
            lead = 1 + ($time - t_mark) / 40;
            marked = 1'b0;
        end
        n_req = n_req + 1;
        if (pend) begin
            if (hs_err == 0) $display("tb_flip_sync: a second FLIP request while the first was unacknowledged at %0t", $time);
            hs_err = hs_err + 1;
        end
        pend = 1'b1;
        t_req = $time;
    end
    always @(flip_ack) if (started) begin
        n_ack = n_ack + 1;
        if (!pend) begin
            if (hs_err == 0) $display("tb_flip_sync: o_flip_ack changed with no FLIP pending at %0t", $time);
            hs_err = hs_err + 1;
        end else begin
            if ($time - t_req > max_lat_ns) max_lat_ns = $time - t_req;
            if ($time - t_req < min_lat_ns) min_lat_ns = $time - t_req;
        end
        pend = 1'b0;
    end
    // Between a request and its acknowledge: no framebuffer write, no palette write, no reply byte.
    always @(posedge fb_we or posedge pal_we or negedge tx) if (pend && started) begin
        if (stall_err == 0)
            $display("tb_flip_sync: the engine is not stalled after FLIP %0d: a %0s at %0t, %0d ns after the request and before the acknowledge",
                     n_req, (tx === 1'b0) ? "reply byte starts" : "write happens", $time, $time - t_req);
        stall_err = stall_err + 1;
    end

    // ---------------- frame signatures (clk_pix) ----------------
    // A frame is the 307,200 o_de pixels after a falling edge of o_vsync.
    // Frame 0 (before the first fall) is not checked: it starts mid-picture.
    reg        armed = 1'b0, collecting = 1'b0;
    reg [31:0] sig = 32'd0;
    integer    pcount = 0, nframes = 0, j_prev = -1, j_now, jj, frame_req = 0, frame_ack = 0;
    integer    frame_err = 0, step_err = 0, bound_err = 0, shown_max = 0;
    always @(negedge o_vs) if (armed) begin
        collecting = 1'b1; pcount = 0; sig = 32'd0;
    end
    always @(posedge clk_pix) if (collecting && o_de === 1'b1) begin
        if (pcount == 0) begin frame_req = n_req; frame_ack = n_ack; end
        sig = sig + ({8'd0, o_r, o_g, o_b} * {pcount[30:0], 1'b1});
        pcount = pcount + 1;
        if (pcount == PIXELS) begin
            collecting = 1'b0;
            nframes = nframes + 1;
            j_now = -1;
            for (jj = 0; jj < n_sigs; jj = jj + 1) if (sigs[jj] === sig && j_now < 0) j_now = jj;
            if (pend && $time - t_req > 40_000_000) begin
                if (live_err == 0) $display("tb_flip_sync: FLIP %0d was not acknowledged within 40 ms", n_req);
                live_err = live_err + 1;
                pend = 1'b0;
            end
            if (j_now < 0) begin
                if (frame_err == 0)
                    $display("tb_flip_sync: frame %0d (signature %h) is not any one picture: it mixes two buffers (a swap in the visible area?). %0d FLIPs had been requested and %0d acknowledged when it began; the previous checked frame showed picture %0d (-1: none yet)",
                             nframes, sig, frame_req, frame_ack, j_prev);
                frame_err = frame_err + 1;
            end else begin
                if (j_prev >= 0 && j_now != j_prev && j_now != j_prev + 1) begin
                    if (step_err == 0)
                        $display("tb_flip_sync: frame %0d shows picture %0d after picture %0d: more than one swap per frame, or a swap back",
                                 nframes, j_now, j_prev);
                    step_err = step_err + 1;
                end
                if (j_now > frame_req || j_now < frame_ack) begin
                    if (bound_err == 0)
                        $display("tb_flip_sync: frame %0d shows picture %0d, but %0d FLIPs had been requested and %0d acknowledged before its first pixel",
                                 nframes, j_now, frame_req, frame_ack);
                    bound_err = bound_err + 1;
                end
                j_prev = j_now;
                if (j_now > shown_max) shown_max = j_now;
            end
        end
    end

    // ---------------- o_vblank ----------------
    // Checked at each edge, and once per line at the beam's hsync.
    integer lp, vb_err = 0, vb_rises = 0;
    always @(vblank) if (armed) begin
        lp = y * 800 + x;
        if (vblank === 1'b1) vb_rises = vb_rises + 1;
        // rising: at the start of line 480; falling: at the start of line 0 (within 4 clocks)
        if (vblank === 1'b1 ? (lp < LINE480 - 2 || lp > LINE480 + 4) : (lp > 4 && lp < FRAME - 4)) begin
            if (vb_err == 0) $display("tb_flip_sync: o_vblank %0s at x=%0d y=%0d (it must change at the start of line %0d)",
                                      vblank ? "rose" : "fell", x, y, vblank ? 480 : 0);
            vb_err = vb_err + 1;
        end
    end
    always @(negedge hs) if (armed && vblank !== (y >= 10'd480)) begin
        if (vb_err == 0) $display("tb_flip_sync: o_vblank is %b with the beam at x=%0d y=%0d (high only for lines 480-524)", vblank, x, y);
        vb_err = vb_err + 1;
    end

    // ---------------- main sequence ----------------
    integer i, p, t0, tgt, n_stream = 0;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_flip_sync.vcd");
            $dumpvars(0, tb_flip_sync);
        end
        for (i = 0; i < 8192; i = i + 1) stream[i] = 16'bx;
        for (i = 0; i < 1024; i = i + 1) replies[i] = 8'bx;
        for (i = 0; i < 256; i = i + 1) begin sigs[i] = 32'bx; labels[i] = "(no label)"; end
        read_list({DIR, "sim/flipsync_stream.hex"}, 0);
        read_list({DIR, "sim/flipsync_replies.hex"}, 1);
        read_list({DIR, "sim/flipsync_sigs.hex"}, 2);
        read_labels({DIR, "sim/flipsync_labels.txt"});
        if (stream[0] === 16'bx || replies[0] === 8'bx || sigs[0] === 32'bx) begin
            $display("FAIL: tb_flip_sync cannot read its files (run `.venv/bin/python sim/py/ref_flip_sync.py sim` and Chapter 4.1's `ref_scanout.py --hex rtl/common`; run from build/sim)");
            $finish;
        end
        while (sigs[n_sigs] !== 32'bx) n_sigs = n_sigs + 1;
        while (replies[n_exp] !== 8'bx) n_exp = n_exp + 1;

        repeat (10) @(negedge clk_sys);
        rst = 1'b0;
        started = 1'b1;
        wait (y == 10'd10);                 // well after power-up: start looking at the frames
        armed = 1'b1;
        repeat (100) @(negedge clk_sys);

        p = 0;
        while (stream[p] !== 16'hFFFF && !timeout) begin
            if (stream[p] === 16'bx) begin
                $display("FAIL: tb_flip_sync sim/flipsync_stream.hex has no end marker");
                $finish;
            end
            case (stream[p][15:12])
                4'h0: send_byte(stream[p][7:0]);
                4'h1: begin                                // fence
                    n_fence = n_fence + 1;
                    t0 = $time;
                    while (n_got < stream[p][11:0] && !timeout) begin
                        @(negedge clk_sys);
                        if ($time - t0 > 50_000_000) begin
                            $display("tb_flip_sync: fence %0d waited 50 ms: %0d reply bytes so far, expected %0d. Waiting for the reply covering: %0s",
                                     n_fence, n_got, stream[p][11:0], labels[n_got / 4]);
                            timeout = 1'b1;
                        end
                    end
                    if (!timeout) check_replies(stream[p][11:0]);
                end
                4'h2: begin                                // reset while a FLIP waits
                    t0 = $time;
                    while (!pend && $time - t0 < 1_000_000) @(negedge clk_sys);
                    rst = 1'b1;
                    repeat (stream[p][11:0]) @(negedge clk_sys);
                    rst = 1'b0;
                end
                4'h5, 4'h6: begin                          // time the next FLIP against the beam
                    is_race  = (stream[p][15:12] == 4'h6);
                    race_off = stream[p][11:0] - 1024;
                    tgt = is_race ? LINE480 + race_off : stream[p][11:0] * 800;
                    tgt = (tgt - lead + 2 * FRAME) % FRAME;
                    @(posedge clk_pix);
                    while ((y * 800 + x) != tgt) @(posedge clk_pix);
                    t_mark = $time;
                    marked = 1'b1;
                end
                default: ;
            endcase
            p = p + 1;
        end

        // Let the last picture show for two more frames.
        t0 = nframes;
        while (nframes < t0 + 2 && !timeout) @(posedge clk_pix);
        check_replies(n_exp);
        if (n_race > 0) begin
            $write("tb_flip_sync: timed FLIP requests toggled at");
            for (i = 0; i < n_race; i = i + 1) $write(" %0d", race_got[i]);
            $display(" pixel clocks from the start of line 480");
        end

        if (overflow === 1'b1)
            $display("FAIL: tb_flip_sync (o_overflow rose: the FIFO dropped bytes)");
        else if (timeout)
            $display("FAIL: tb_flip_sync (a reply never came or the engine got stuck, see above)");
        else if (stall_err != 0)
            $display("FAIL: tb_flip_sync (the engine ran commands between a FLIP and its swap, see above)");
        else if (live_err != 0 || hs_err != 0)
            $display("FAIL: tb_flip_sync (FLIP request/acknowledge handshake, see above)");
        else if (frame_err != 0)
            $display("FAIL: tb_flip_sync (%0d frames were not one picture: the buffers swapped while the beam was drawing)", frame_err);
        else if (step_err != 0 || bound_err != 0)
            $display("FAIL: tb_flip_sync (the pictures on the screen came in the wrong order or at the wrong time, see above)");
        else if (rx_err != 0)
            $display("FAIL: tb_flip_sync (%0d malformed bytes on o_tx)", rx_err);
        else if (n_got != n_exp || reply_err != 0)
            $display("FAIL: tb_flip_sync (replies: got %0d bytes, expected %0d, %0d of %0d FB_CRC replies wrong)",
                     n_got, n_exp, reply_err, n_exp / 4);
        else if (vb_err != 0)
            $display("FAIL: tb_flip_sync (o_vblank does not follow vertical blanking, see above)");
        else if (n_req != n_sigs - 1 || n_ack != n_req)
            $display("FAIL: tb_flip_sync (%0d FLIP requests and %0d acknowledges, expected %0d of each)", n_req, n_ack, n_sigs - 1);
        else if (j_prev != n_sigs - 1)
            $display("FAIL: tb_flip_sync (the last frame shows picture %0d, expected the last one, %0d)", j_prev, n_sigs - 1);
        else
            $display("PASS: tb_flip_sync (%0d FLIPs, %0d frames each one picture, FLIP took %0d-%0d us to acknowledge, %0d FB_CRC replies match, %0d vblank rises)",
                     n_req, nframes, min_lat_ns / 1000, max_lat_ns / 1000, n_exp / 4, vb_rises);
        $finish;
    end

    initial begin : watchdog
        #3_000_000_000;                                    // 3 s simulated
        $display("FAIL: tb_flip_sync watchdog timeout after 3 s simulated (sent %0d bytes, %0d FLIP requests, %0d acknowledges)", n_sent, n_req, n_ack);
        $finish;
    end
endmodule
`default_nettype wire
