// tb_gpu_spi.v -- self-checking testbench for the SPI link of gpu_link (Chapter 5.1).
//
// The testbench is an SPI master (mode 0) and the memory behind the draw ports.
// It replays the Chapter 4.8 stream (sim/blit_stream.hex, which exercises every
// command of Phase 4) into gpu_link over SPI instead of the UART, and compares
// the same results the UART testbench does:
//
//   * every FB_CRC reply byte, read back over MISO, with sim/blit_replies.hex,
//   * all 76,800 bytes of both framebuffers with sim/blit_fb0.hex and blit_fb1.hex,
//   * all 256 palette entries with sim/blit_pal.hex,
//   * the counting checks (2004 / 2005 / 2006) and the throughput windows,
//     which still describe the command engine and so still hold.
//
// The master:
//   * cuts the stream into CS frames of random length, always at a packet
//     boundary (opcode, length, payload), so one frame holds whole packets;
//   * runs each frame at a random SCK rate from 0.5 MHz to 6.25 MHz (half
//     period 1000 ns down to 80 ns), with random jitter on every edge and a
//     random phase against the 50 MHz clock; `+half=NS` forces one rate;
//   * leaves a random gap between frames (at least 100 ns);
//   * always honors READY (the o_ready pin) in command frames: in each frame
//     it looks before every byte, before every 16th or before every 257th
//     (READY high means room for at least 257 bytes), and waits with SCK low
//     and CS low while READY is low. Entries 2000 / 2001 are ignored: the
//     stream's "without regard to o_ready" section sends 181 bytes behind the
//     biggest BLIT and counts on a UART slow enough to keep the FIFO nearly
//     empty. An SPI master that has just used its 257-byte allowance may
//     leave the FIFO nearly full, and 181 more bytes would overflow it;
//   * reads replies with read frames (first byte 0xFF, then 1 to 17 random
//     bytes that must not reach the parser), which return status, count,
//     then reply bytes. It polls this way at every fence (1NNN) until NNN
//     reply bytes have arrived, without looking at READY.
//
// It also checks, in every frame: the status byte has bits 7:6 = 01 (so a
// stuck MISO line is noticed), OVERFLOW and the reserved bits 5 and 1 are 0,
// the count byte is at most 16, VBLANK matches the i_vblank input and READY
// matches the o_ready pin when they have not changed from 300 ns before CS
// fell to 150 ns after; a command frame returns 0x00 after the count byte,
// and a read frame returns 0x00 after its `count` reply bytes; MISO never
// changes while SCK is high; o_overflow never rises; no reply bytes beyond the
// expected ones; no write outside the 76,800 bytes; no X on the write enables.
//
// Stream entries (4 hex digits): 00NN = send byte NN; 1NNN = fence, wait
// until NNN (hex) reply bytes have arrived in all; 2000 = send without
// looking at o_ready (ignored here); 2001 = wait for o_ready (ignored here); 2002 / 2003 = start / end a
// throughput window; 2004 / 2005 / 2006 = zero / check (exactly once) / check
// (at most once) the write counts; 3NNN = the open window may take at most
// 4096 * HHH + NNN clocks, with 4HHH sent first; FFFF = end.
//
// Run from build/sim (make test / make sim-tb_gpu_spi); files are read from
// ../../sim/ and ../../rtl/common/. It takes about as long as tb_gpu_blit.
`timescale 1ns/1ps
`default_nettype none

module tb_gpu_spi;
    localparam integer FBN      = 76800;
    localparam         DIR      = "../../";

    reg clk = 1'b0;
    always #10 clk = ~clk;                                 // 50 MHz

    // ---------------- DUT ----------------
    reg         rst = 1'b1;
    reg         sck = 1'b0, mosi = 1'b0, cs_n = 1'b1;
    reg         vblank = 1'b0;
    wire        miso, tx, ready, overflow;
    wire        fb_we, pal_we, fb_buf, flip_req;
    reg  [3:0]  ack_pipe = 4'b0000;
    wire        flip_ack = ack_pipe[3];                    // the buffer on the screen
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    reg  [7:0]  fb_rdata = 8'd0;

    gpu_link #(.CLK_HZ(50_000_000), .BAUD(2_500_000)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(1'b1), .o_tx(tx),
        .i_sck(sck), .i_mosi(mosi), .i_cs_n(cs_n), .o_miso(miso), .i_vblank(vblank),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack)
    );
    always @(posedge clk) ack_pipe <= {ack_pipe[2:0], flip_req};

    // ---------------- the framebuffers and palette the DUT draws into ----------------
    reg [7:0]  fb0 [0:FBN-1];
    reg [7:0]  fb1 [0:FBN-1];
    integer    cnt0 [0:FBN-1];                             // writes since the last 2004
    integer    cnt1 [0:FBN-1];
    reg [23:0] pal [0:255];
    integer    n_fb_wr = 0, n_pal_wr = 0, bad_wr = 0, x_we = 0, front_wr = 0;
    integer    clocks = 0;
    reg        in_win = 1'b0;
    integer    win_first = -1, win_last = -1, win_wr = 0;
    always @(posedge clk) begin
        clocks = clocks + 1;
        if (!rst && (fb_we === 1'bx || pal_we === 1'bx)) begin
            if (x_we == 0) $display("tb_gpu_spi: o_fb_we or o_pal_we is X at %0t", $time);
            x_we = x_we + 1;
        end
        if (fb_we === 1'b1) begin
            if (fb_addr >= FBN) begin
                if (bad_wr < 3) $display("tb_gpu_spi: framebuffer write to address %0d (past 76,799) at %0t", fb_addr, $time);
                bad_wr = bad_wr + 1;
            end else begin
                if (fb_buf === flip_ack) begin
                    if (front_wr == 0)
                        $display("tb_gpu_spi: write to the buffer on the screen (buffer %0d) at address %0d (x=%0d y=%0d) at %0t",
                                 flip_ack, fb_addr, fb_addr % 320, fb_addr / 320, $time);
                    front_wr = front_wr + 1;
                end
                if (fb_buf) begin fb1[fb_addr] <= fb_wdata; cnt1[fb_addr] = cnt1[fb_addr] + 1; end
                else        begin fb0[fb_addr] <= fb_wdata; cnt0[fb_addr] = cnt0[fb_addr] + 1; end
            end
            fb_rdata <= fb_wdata;                          // write-first, like framebuffer.v
            n_fb_wr = n_fb_wr + 1;
            if (in_win) begin
                if (win_first < 0) win_first = clocks;
                win_last = clocks;
                win_wr = win_wr + 1;
            end
        end else
            fb_rdata <= (fb_addr >= FBN) ? 8'hxx : fb_buf ? fb1[fb_addr] : fb0[fb_addr];
        if (pal_we === 1'b1) begin
            pal[pal_addr] <= pal_wdata;
            n_pal_wr = n_pal_wr + 1;
        end
    end

    // ---------------- expected data ----------------
    reg [15:0]      stream  [0:262143];
    reg [7:0]       replies [0:4095];
    reg [7:0]       exp_fb0 [0:FBN-1];
    reg [7:0]       exp_fb1 [0:FBN-1];
    reg [23:0]      exp_pal [0:255];
    reg [8*128-1:0] labels  [0:255];                       // one per FB_CRC reply
    integer         n_exp = 0;

    // ---------------- the SPI master ----------------
    reg [7:0] got [0:4095];                                // reply bytes read over MISO
    integer   n_got = 0;
    integer   spi_err = 0;                                 // protocol errors seen in status bytes
    integer   n_frames = 0, n_polls = 0, n_rd_waits = 0;
    real      half = 80.0;
    integer   forced_half = 0;
    reg       honor = 1'b1;
    reg       timeout = 1'b0;
    integer   n_sent = 0;

    localparam real MOSI_DLY = 5.0, MISO_SETUP = 10.0;
    function real jit(input integer maxns);
        jit = $urandom_range(0, maxns * 10) / 10.0;
    endfunction

    // pin histories for the status checks
    time t_vb = 0, t_rdy = 0;
    always @(vblank) t_vb = $time;
    always @(ready)  t_rdy = $time;
    integer miso_bad = 0;
    always @(miso)
        if (sck === 1'b1) begin
            if (miso_bad == 0) $display("tb_gpu_spi: MISO changed while SCK was high at %0t", $time);
            miso_bad = miso_bad + 1;
        end
    // vertical blanking comes and goes, on the clock, every 20 to 300 us
    integer vb_wait = 0;
    always @(negedge clk) begin
        vb_wait = vb_wait - 1;
        if (vb_wait <= 0) begin vblank <= ~vblank; vb_wait = $urandom_range(1000, 15000); end
    end

    // one byte, MSB first; returns what MISO said
    reg [7:0] m_rx;
    task xfer(input [7:0] tx_b);
        integer i;
        reg s;
        begin
            for (i = 7; i >= 0; i = i - 1) begin
                #(MOSI_DLY) mosi = tx_b[i];
                #(half - MOSI_DLY - MISO_SETUP + jit(20)) s = miso;
                m_rx[i] = s;
                #(MISO_SETUP) sck = 1'b1;
                #(half + jit(20)) sck = 1'b0;
            end
        end
    endtask

    // ---- a frame in progress ----
    reg        in_frame = 1'b0;
    integer    fr_n = 0;                                   // bytes clocked in this frame
    integer    fr_target = 0;                              // close at the next packet boundary after this many
    integer    fr_look = 1;                                // look at READY every fr_look bytes
    reg [7:0]  fr_miso [0:511];
    time       fr_t0 = 0;
    reg        fr_vb = 1'b0, fr_rdy = 1'b0, fr_read = 1'b0;
    integer    fr_vb_stable = 0, fr_rdy_stable = 0;

    task open_frame;
        begin
            half      = forced_half != 0 ? forced_half : pick_half(0);
            fr_target = $urandom_range(1, 400);
            case ($urandom_range(0, 3)) 0: fr_look = 1; 1: fr_look = 16; default: fr_look = 257; endcase
            fr_n      = 0;
            fr_read   = 1'b0;
            #(jit(20));                                    // random phase against clk
            cs_n = 1'b0;
            fr_t0 = $time;
            #(150 + jit(100));                             // CS to first SCK: at least 150 ns
            fr_vb  = vblank;  fr_vb_stable  = ($time - t_vb  > 450) ? 1 : 0;   // no change from 300 ns before CS to 150 ns after
            fr_rdy = ready;   fr_rdy_stable = ($time - t_rdy > 450) ? 1 : 0;
            in_frame = 1'b1;
            n_frames = n_frames + 1;
        end
    endtask

    function real pick_half(input integer dummy);
        integer r;
        begin
            r = $urandom_range(0, 99);
            if      (r < 30) pick_half = 80.0;
            else if (r < 50) pick_half = 100.0;
            else if (r < 65) pick_half = 125.0;
            else if (r < 75) pick_half = 167.0;
            else if (r < 85) pick_half = 250.0;
            else if (r < 95) pick_half = 500.0;
            else             pick_half = 1000.0;
        end
    endfunction

    // close the frame: raise CS, read the status, count and reply bytes it carried
    integer tails [0:3];
    integer cnt_b, k2, nrep;
    task close_frame;
        begin
            if (in_frame) begin
                #(tails[$urandom_range(0, 3)]) cs_n = 1'b1;
                in_frame = 1'b0;
                // status
                if (fr_miso[0][7:6] !== 2'b01) begin
                    if (spi_err == 0) $display("tb_gpu_spi: status byte %h does not start with 01 (MISO stuck?)", fr_miso[0]);
                    spi_err = spi_err + 1;
                end
                if (fr_miso[0][5] !== 1'b0 || fr_miso[0][4] !== 1'b0 || fr_miso[0][1] !== 1'b0) begin
                    if (spi_err == 0) $display("tb_gpu_spi: status byte %h has OVERFLOW or a reserved bit set", fr_miso[0]);
                    spi_err = spi_err + 1;
                end
                if (fr_vb_stable && fr_miso[0][3] !== fr_vb) begin
                    if (spi_err == 0) $display("tb_gpu_spi: status VBLANK bit is %b but the i_vblank input was %b", fr_miso[0][3], fr_vb);
                    spi_err = spi_err + 1;
                end
                if (fr_rdy_stable && fr_miso[0][0] !== fr_rdy) begin
                    if (spi_err == 0) $display("tb_gpu_spi: status READY bit is %b but the o_ready pin was %b", fr_miso[0][0], fr_rdy);
                    spi_err = spi_err + 1;
                end
                if (fr_n >= 2) begin
                    cnt_b = fr_miso[1];
                    if (cnt_b > 16) begin
                        if (spi_err == 0) $display("tb_gpu_spi: reply count byte is %0d (the reply queue holds 16)", cnt_b);
                        spi_err = spi_err + 1;
                    end
                    nrep = fr_read ? fr_n - 2 : 0;          // only read frames carry replies
                    if (cnt_b < nrep) nrep = cnt_b;
                    for (k2 = 0; k2 < nrep; k2 = k2 + 1) begin
                        if (n_got < 4096) got[n_got] = fr_miso[2 + k2];
                        n_got = n_got + 1;
                    end
                    for (k2 = 2 + nrep; k2 < fr_n && k2 < 512; k2 = k2 + 1)
                        if (fr_miso[k2] !== 8'h00) begin
                            if (spi_err == 0) $display("tb_gpu_spi: %0s frame byte %0d is %h, expected 00 (count byte %0d)",
                                                       fr_read ? "read" : "command", k2, fr_miso[k2], cnt_b);
                            spi_err = spi_err + 1;
                        end
                end
                #(100 + (($urandom_range(0, 9) == 0) ? $urandom_range(0, 30000) : $urandom_range(0, 1500)));
            end
        end
    endtask

    // clock one byte inside the open frame, honoring READY if asked
    task frame_byte(input [7:0] d, input integer look_ok);
        time tw;
        begin
            if (look_ok && honor && (fr_n % fr_look) == 0) begin
                tw = $time;
                while (ready !== 1'b1 && !timeout) begin
                    n_rd_waits = n_rd_waits + 1;
                    @(negedge clk);
                    if ($time - tw > 50_000_000) begin
                        $display("tb_gpu_spi: READY stayed low for 50 ms after %0d bytes: the engine is stuck in a command, or the reply queue is full and unread. %0d reply bytes so far; the next covers: %0s",
                                 n_sent, n_got, labels[n_got / 4]);
                        timeout = 1'b1;
                    end
                end
            end
            if (!timeout) begin
                xfer(d);
                if (fr_n < 512) fr_miso[fr_n] = m_rx;
                fr_n = fr_n + 1;
            end
        end
    endtask

    // ---- the packet cutter: frames end only between packets ----
    integer pk_state = 0, pk_left = 0;
    task send_stream_byte(input [7:0] d);
        begin
            if (!in_frame) open_frame;
            frame_byte(d, 1);
            n_sent = n_sent + 1;
            case (pk_state)
                0: pk_state = 1;                           // opcode
                1: begin pk_left = d; pk_state = (d == 0) ? 0 : 2; end
                default: begin pk_left = pk_left - 1; if (pk_left == 0) pk_state = 0; end
            endcase
            if (pk_state == 0 && fr_n >= fr_target) close_frame;
        end
    endtask

    // a read frame: 0xFF, then 1 to 17 random bytes; status, count and up to 16 reply bytes come back
    task poll_replies;
        integer j, n;
        begin
            open_frame;
            fr_read = 1'b1;
            n = $urandom_range(2, 18);
            frame_byte(8'hFF, 0);
            for (j = 1; j < n; j = j + 1) frame_byte($urandom, 0);
            n_polls = n_polls + 1;
            close_frame;
        end
    endtask

    // ---------------- CRC of the testbench's own framebuffer (diagnosis only) ----------------
    function [31:0] fb_crc32(input integer dummy);
        integer a, j;
        reg [31:0] c;
        begin
            c = 32'hFFFFFFFF;
            for (a = 0; a < FBN; a = a + 1) begin
                c = c ^ {24'd0, (flip_ack ? fb0[a] : fb1[a])};     // the back buffer
                for (j = 0; j < 8; j = j + 1)
                    c = c[0] ? (c >> 1) ^ 32'hEDB88320 : (c >> 1);
            end
            fb_crc32 = c ^ 32'hFFFFFFFF;
        end
    endfunction

    // Compares reply bytes [n_checked, upto) with the expected ones, 4 at a
    // time, at a fence. The first wrong reply is reported with its label.
    integer reply_err = 0, n_fence = 0, n_checked = 0;
    task check_replies(input integer upto);
        integer r;
        reg [31:0] want, have, mine;
        begin
            for (r = n_checked; r + 3 < upto; r = r + 4) begin
                want = {replies[r+3], replies[r+2], replies[r+1], replies[r]};
                have = {got[r+3], got[r+2], got[r+1], got[r]};
                if (have !== want) begin
                    if (reply_err == 0) begin
                        $display("tb_gpu_spi: FB_CRC reply %0d is %h, expected %h. It covers: %0s",
                                 r / 4 + 1, have, want, labels[r / 4]);
                        if (r + 4 >= upto) begin
                            mine = fb_crc32(0);
                            if (mine === want)
                                $display("tb_gpu_spi:   the framebuffer is right at this point (its CRC is %h): check the CRC unit", mine);
                            else
                                $display("tb_gpu_spi:   the framebuffer is already wrong at this point (its CRC is %h): check the commands before this FB_CRC", mine);
                        end
                    end
                    reply_err = reply_err + 1;
                end
            end
            n_checked = upto;
        end
    endtask

    // The counting-buffer check (stream entries 2005 and 2006). The back
    // buffer is the one the screen is not showing.
    integer n_count = 0, count_err = 0;
    task check_counts(input integer once);
        integer a, gaps, dbl, first_gap, first_dbl, front_hits, c, back;
        begin
            n_count = n_count + 1;
            back = flip_ack ? 0 : 1;
            gaps = 0; dbl = 0; first_gap = -1; first_dbl = -1; front_hits = 0;
            for (a = 0; a < FBN; a = a + 1) begin
                c = back ? cnt1[a] : cnt0[a];
                if (c == 0 && once != 0) begin gaps = gaps + 1; if (first_gap < 0) first_gap = a; end
                if (c > 1) begin dbl = dbl + 1; if (first_dbl < 0) first_dbl = a; end
                if ((back ? cnt0[a] : cnt1[a]) != 0) front_hits = front_hits + 1;
            end
            if (gaps != 0 || dbl != 0 || front_hits != 0) begin
                if (count_err == 0) begin
                    $display("tb_gpu_spi: counting check %0d fails. It covers: %0s", n_count, labels[n_got / 4 - 1]);
                    if (gaps != 0)
                        $display("tb_gpu_spi:   %0d pixels were never written (gaps); the first is x=%0d y=%0d",
                                 gaps, first_gap % 320, first_gap / 320);
                    if (dbl != 0)
                        $display("tb_gpu_spi:   %0d pixels were written more than once (double hits); the first is x=%0d y=%0d, written %0d times",
                                 dbl, first_dbl % 320, first_dbl / 320, back ? cnt1[first_dbl] : cnt0[first_dbl]);
                    if (front_hits != 0)
                        $display("tb_gpu_spi:   %0d pixels of the buffer on the screen were written", front_hits);
                end
                count_err = count_err + 1;
            end
        end
    endtask

    task zero_counts;
        integer a;
        begin
            for (a = 0; a < FBN; a = a + 1) begin cnt0[a] = 0; cnt1[a] = 0; end
        end
    endtask

    // Reads a hex list of unknown length (after its comment line) into
    // stream[] (which = 0) or replies[] (which = 1).
    task read_list(input [8*64-1:0] name, input integer which);
        integer fd, n, rc;
        reg [15:0] v;
        reg [8*200-1:0] line;
        begin
            fd = $fopen(name, "r");
            if (fd != 0) begin
                rc = $fgets(line, fd);           // the // comment line
                n = 0;
                while ($fscanf(fd, "%h\n", v) == 1) begin
                    if (which == 0) stream[n] = v; else replies[n] = v[7:0];
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
                    if (line[7:0] == 8'h0A) line = line >> 8;   // drop the newline
                    labels[n] = line;
                    n = n + 1;
                    line = 0;
                    rc = $fgets(line, fd);
                end
                $fclose(fd);
            end
        end
    endtask


    reg overflow_seen = 1'b0;
    always @(posedge clk)
        if (!rst && overflow === 1'b1 && !overflow_seen) begin
            $display("tb_gpu_spi: o_overflow rose after %0d bytes sent", n_sent);
            overflow_seen = 1'b1;
        end

    // ---------------- main sequence ----------------
    integer i, p, n_fb_err = 0, n_pal_err = 0, first_bad = -1, first_buf = 0, t0, win_span = -1;
    integer n_win = 0, win_limit = 0, win_limit_hi = 0, win_bad = 0, win_bad_span = 0, win_bad_limit = 0, n_lim = 0;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_gpu_spi.vcd");
            $dumpvars(0, tb_gpu_spi);
        end
        if (!$value$plusargs("half=%d", forced_half)) forced_half = 0;
        tails[0] = 0; tails[1] = 2; tails[2] = 20; tails[3] = 100;
        for (i = 0; i < 262144; i = i + 1) stream[i] = 16'bx;
        for (i = 0; i < 4096; i = i + 1) replies[i] = 8'bx;
        for (i = 0; i < 256; i = i + 1) labels[i] = "(no label)";
        $readmemh({DIR, "rtl/common/fb_test.hex"}, fb0);
        $readmemh({DIR, "rtl/common/fb_test.hex"}, fb1);
        zero_counts;
        $readmemh({DIR, "rtl/common/pal_test.hex"}, pal);
        read_list({DIR, "sim/blit_stream.hex"}, 0);
        read_list({DIR, "sim/blit_replies.hex"}, 1);
        read_labels({DIR, "sim/blit_labels.txt"});
        $readmemh({DIR, "sim/blit_fb0.hex"}, exp_fb0);
        $readmemh({DIR, "sim/blit_fb1.hex"}, exp_fb1);
        $readmemh({DIR, "sim/blit_pal.hex"}, exp_pal);
        if (fb0[FBN-1] === 8'bx || fb1[FBN-1] === 8'bx || pal[255] === 24'bx || stream[0] === 16'bx ||
            exp_fb0[FBN-1] === 8'bx || exp_fb1[FBN-1] === 8'bx || exp_pal[255] === 24'bx) begin
            $display("FAIL: tb_gpu_spi cannot read its files (run `.venv/bin/python sim/py/ref_blit.py sim` and Chapter 4.1's `ref_scanout.py --hex rtl/common`; run from build/sim)");
            $finish;
        end

        repeat (10) @(negedge clk);
        rst = 1'b0;
        repeat (100) @(negedge clk);

        p = 0;
        while (stream[p] !== 16'hFFFF && !timeout) begin
            if (stream[p] === 16'bx) begin
                $display("FAIL: tb_gpu_spi sim/blit_stream.hex has no end marker");
                $finish;
            end
            if (stream[p][15:12] == 4'h0) send_stream_byte(stream[p][7:0]);
            else begin
                close_frame;                                // directives sit between packets
                case (stream[p][15:12])
                    4'h1: begin                             // fence: poll until NNN reply bytes are in
                        n_fence = n_fence + 1;
                        t0 = $time;
                        while (n_got < stream[p][11:0] && !timeout) begin
                            poll_replies;
                            #($urandom_range(100, 3000));
                            if ($time - t0 > 50_000_000) begin
                                $display("tb_gpu_spi: fence %0d waited 50 ms: %0d reply bytes so far, expected %0d. Waiting for the reply covering: %0s",
                                         n_fence, n_got, stream[p][11:0], labels[n_got / 4]);
                                timeout = 1'b1;
                            end
                        end
                        if (!timeout) check_replies(stream[p][11:0]);
                    end
                    4'h2: case (stream[p][2:0])
                        3'd0, 3'd1: ;                           // READY is honored throughout
                        3'd2: begin in_win = 1'b1; win_first = -1; win_last = -1; win_wr = 0; end
                        3'd4: zero_counts;
                        3'd5: check_counts(1);
                        3'd6: check_counts(0);
                        3'd3: begin
                            in_win = 1'b0;
                            n_win = n_win + 1;
                            win_span = (win_first < 0) ? -1 : win_last - win_first + 1;
                            if ((win_span < 0 || win_span > win_limit) && win_bad == 0) begin
                                win_bad = n_win; win_bad_span = win_span; win_bad_limit = win_limit;
                            end
                        end
                    endcase
                    4'h3: begin win_limit = win_limit_hi * 4096 + stream[p][11:0]; n_lim = n_lim + 1; end
                    4'h4: win_limit_hi = stream[p][11:0];
                    default: ;
                endcase
            end
            p = p + 1;
        end
        close_frame;

        // the engine finishes, the replies are all read, and nothing is left over
        #200_000;
        honor = 1'b1;
        poll_replies; poll_replies;
        n_exp = 0;
        while (replies[n_exp] !== 8'bx) n_exp = n_exp + 1;
        check_replies(n_exp);
        if (n_got != n_exp)
            $display("tb_gpu_spi: %0d reply bytes read, %0d expected", n_got, n_exp);
        if (fr_miso[0][2] !== 1'b0 || fr_miso[1] !== 8'd0)
            $display("tb_gpu_spi: the last poll still shows a reply waiting (status %h, count %0d)", fr_miso[0], fr_miso[1]);

        for (i = 0; i < FBN; i = i + 1) begin
            if (fb0[i] !== exp_fb0[i]) begin
                if (first_bad < 0) begin first_bad = i; first_buf = 0; end
                n_fb_err = n_fb_err + 1;
            end
            if (fb1[i] !== exp_fb1[i]) begin
                if (first_bad < 0) begin first_bad = i; first_buf = 1; end
                n_fb_err = n_fb_err + 1;
            end
        end
        for (i = 0; i < 256; i = i + 1)
            if (pal[i] !== exp_pal[i]) begin
                if (n_pal_err == 0)
                    $display("tb_gpu_spi: palette entry %0d is %h, expected %h", i, pal[i], exp_pal[i]);
                n_pal_err = n_pal_err + 1;
            end
        if (first_bad >= 0)
            $display("tb_gpu_spi: %0d framebuffer bytes wrong at the end; first in buffer %0d at address %0d (x=%0d y=%0d): %h, expected %h",
                     n_fb_err, first_buf, first_bad, first_bad % 320, first_bad / 320,
                     first_buf ? fb1[first_bad] : fb0[first_bad], first_buf ? exp_fb1[first_bad] : exp_fb0[first_bad]);

        if (overflow_seen)
            $display("FAIL: tb_gpu_spi (o_overflow rose: the FIFO dropped bytes)");
        else if (timeout)
            $display("FAIL: tb_gpu_spi (a reply never came or the engine got stuck, see above)");
        else if (x_we != 0 || bad_wr != 0)
            $display("FAIL: tb_gpu_spi (bad framebuffer/palette writes, see above)");
        else if (front_wr != 0)
            $display("FAIL: tb_gpu_spi (%0d writes went to the buffer on the screen, not the back buffer)", front_wr);
        else if (count_err != 0)
            $display("FAIL: tb_gpu_spi (%0d of %0d counting checks failed: a pixel written twice, or a write to the front buffer)",
                     count_err, n_count);
        else if (spi_err != 0 || miso_bad != 0)
            $display("FAIL: tb_gpu_spi (%0d bad status or count bytes, %0d MISO changes while SCK was high, see above)", spi_err, miso_bad);
        else if (n_got != n_exp || reply_err != 0)
            $display("FAIL: tb_gpu_spi (replies: got %0d bytes, expected %0d, %0d of %0d FB_CRC replies wrong)",
                     n_got, n_exp, reply_err, n_exp / 4);
        else if (fr_miso[0][2] !== 1'b0 || fr_miso[1] !== 8'd0)
            $display("FAIL: tb_gpu_spi (a reply was still waiting after the last poll)");
        else if (n_fb_err != 0 || n_pal_err != 0)
            $display("FAIL: tb_gpu_spi (%0d framebuffer bytes and %0d palette entries differ from blit_fb0.hex, blit_fb1.hex and blit_pal.hex)",
                     n_fb_err, n_pal_err);
        else if (n_win == 0 || n_win != n_lim || win_bad != 0)
            $display("FAIL: tb_gpu_spi (throughput window %0d: the BLIT took %0d clocks from first to last write, more than %0d)",
                     win_bad, win_bad_span, win_bad_limit);
        else
            $display("PASS: tb_gpu_spi (%0d bytes in %0d frames and %0d polls, %0d clocks spent waiting for READY, %0d FB_CRC replies match, %0d counting checks, %0d throughput windows)",
                     n_sent, n_frames, n_polls, n_rd_waits, n_exp / 4, n_count, n_win);
        $finish;
    end

    initial begin : watchdog
        #2_000_000_000;                                   // 2 s simulated
        $display("FAIL: tb_gpu_spi watchdog timeout after 2 s (sent %0d bytes)", n_sent);
        $finish;
    end
endmodule
`default_nettype wire
