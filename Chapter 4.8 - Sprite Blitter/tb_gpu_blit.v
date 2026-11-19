// tb_gpu_blit.v -- self-checking testbench for UPLOAD_ASSET and BLIT in uart_gpu (Chapter 4.8).
//
// Same harness as Chapter 4.7's tb_gpu_tri: the testbench is the host and the
// memory. It sends sim/blit_stream.hex into i_rx as real 8N1 serial at
// 2.5 Mbaud (20 clocks per bit at 50 MHz), decodes every reply byte on o_tx,
// and keeps two framebuffer RAMs and a palette RAM on the DUT's draw ports.
// o_fb_buf picks the buffer a write goes to (writes on a rising edge, reads one
// clock later, write-first), and i_flip_ack follows o_flip_req four clocks
// late, like a scanout that swaps at once. The buffer on the screen is the one
// i_flip_ack names. At the end it compares
//
//   * every reply byte with sim/blit_replies.hex (the FB_CRC results),
//   * all 76,800 bytes of both framebuffers with sim/blit_fb0.hex and blit_fb1.hex,
//   * all 256 palette entries with sim/blit_pal.hex.
//
// The asset RAM and the asset table are inside the DUT, so the testbench can
// see them only through what BLIT draws: every stream entry that uploads,
// replaces, refuses or resets an asset is checked by a later FB_CRC.
//
// COUNTING BUFFER (as in Chapter 4.7). Next to every framebuffer byte the
// testbench counts the writes to it. 2004 zeroes the counts; 2005 / 2006 check
// "every pixel of the back buffer exactly once" / "no pixel twice" and that
// the front buffer was not written. The stream uses 2006 after single BLITs:
// a sprite pixel is written at most once. Any write to the buffer on the
// screen is also reported. The order and timing of writes don't matter.
//
// The stream (from `.venv/bin/python sim/py/ref_blit.py sim`, which feeds it to
// host/gpu_model.py) is described in that script. A wrong FB_CRC reply is
// reported with its line from sim/blit_labels.txt, which says what it covers.
//
// Stream entries (4 hex digits): 00NN = send byte NN; 1NNN = fence, wait
// until NNN (hex) reply bytes have arrived in all; 2000 = send without
// looking at o_ready; 2001 = wait for o_ready before each byte; 2002 / 2003 =
// start / end a throughput window; 2004 / 2005 / 2006 = zero / check
// (exactly once) / check (at most once) the write counts; 3NNN = the open
// window may take at most 4096 * HHH + NNN clocks, with 4HHH sent first;
// FFFF = end.
//
// Throughput: inside each window the only command that writes is one BLIT.
// The testbench counts the clocks from its first framebuffer write to its last
// and requires at most w*h + 2*h + 16 for a w x h sprite: one sprite pixel per
// clock (the off-screen ones too, if the design steps through them), with a
// clock or two to spare per row. Setup before the first write is not counted,
// so a slow setup passes; two clocks per sprite pixel fails. A design that
// skips off-screen pixels faster passes too.
//
// Also checked: o_overflow never rises; no write outside the 76,800 bytes; no
// X on the write enables; no reply bytes beyond the expected ones.
//
// Run from build/sim (make test / make sim-tb_gpu_blit); files are read from
// ../../sim/ and ../../rtl/common/.
`timescale 1ns/1ps
`default_nettype none

module tb_gpu_blit;
    localparam integer CLK_HZ   = 50_000_000;
    localparam integer BAUD     = 2_500_000;
    localparam integer BIT_NS   = 1_000_000_000 / BAUD;     // 400 ns = 20 clocks
    localparam integer FBN      = 76800;
    localparam         DIR      = "../../";

    reg clk = 1'b0;
    always #10 clk = ~clk;                                 // 50 MHz

    // ---------------- DUT ----------------
    reg         rst = 1'b1;
    reg         rx  = 1'b1;
    wire        tx, ready, overflow;
    wire        fb_we, pal_we, fb_buf, flip_req;
    reg  [3:0]  ack_pipe = 4'b0000;
    wire        flip_ack = ack_pipe[3];                    // the buffer on the screen
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    reg  [7:0]  fb_rdata = 8'd0;

    uart_gpu #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx),
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
            if (x_we == 0) $display("tb_gpu_blit: o_fb_we or o_pal_we is X at %0t", $time);
            x_we = x_we + 1;
        end
        if (fb_we === 1'b1) begin
            if (fb_addr >= FBN) begin
                if (bad_wr < 3) $display("tb_gpu_blit: framebuffer write to address %0d (past 76,799) at %0t", fb_addr, $time);
                bad_wr = bad_wr + 1;
            end else begin
                if (fb_buf === flip_ack) begin
                    if (front_wr == 0)
                        $display("tb_gpu_blit: write to the buffer on the screen (buffer %0d) at address %0d (x=%0d y=%0d) at %0t",
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

    // ---------------- reply receiver (8N1 on o_tx) ----------------
    reg [7:0] got [0:4095];
    integer   n_got = 0, rx_err = 0, k;
    reg [7:0] b;
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 1'b0) begin
            if (rx_err < 3) $display("tb_gpu_blit: o_tx start bit too short at %0t", $time);
            rx_err = rx_err + 1;
        end else begin
            for (k = 0; k < 8; k = k + 1) begin
                #(BIT_NS);
                b[k] = tx;
            end
            #(BIT_NS);
            if (tx !== 1'b1) begin
                if (rx_err < 3) $display("tb_gpu_blit: o_tx stop bit missing at %0t", $time);
                rx_err = rx_err + 1;
            end
            if (n_got < 4096) got[n_got] = b;
            n_got = n_got + 1;
            #(BIT_NS / 4);
        end
    end

    // ---------------- sender ----------------
    integer n_sent = 0, ready_low = 0;
    reg     honor = 1'b1;
    always @(posedge clk) if (!ready) ready_low = ready_low + 1;

    reg overflow_seen = 1'b0;
    always @(posedge clk)
        if (!rst && overflow === 1'b1 && !overflow_seen) begin
            $display("tb_gpu_blit: o_overflow rose after %0d bytes sent (honoring o_ready: %0d)", n_sent, honor);
            overflow_seen = 1'b1;
        end

    // A stuck engine (for example a blit loop that never ends and
    // stops fetching) fills the FIFO, so o_ready stays low: give up after 50 ms.
    reg timeout = 1'b0;
    task send_byte(input [7:0] d);
        integer j, tw;
        begin
            if (honor) begin
                @(negedge clk);
                tw = $time;
                while (ready !== 1'b1 && !timeout) begin
                    @(negedge clk);
                    if ($time - tw > 50_000_000) begin
                        $display("tb_gpu_blit: o_ready stayed low for 50 ms after %0d bytes: the engine is stuck in a command (a BLIT that never finishes?). %0d reply bytes so far; the next covers: %0s",
                                 n_sent, n_got, labels[n_got / 4]);
                        timeout = 1'b1;
                    end
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
                        $display("tb_gpu_blit: FB_CRC reply %0d is %h, expected %h. It covers: %0s",
                                 r / 4 + 1, have, want, labels[r / 4]);
                        if (r + 4 >= upto) begin
                            mine = fb_crc32(0);
                            if (mine === want)
                                $display("tb_gpu_blit:   the framebuffer is right at this point (its CRC is %h): check the CRC unit", mine);
                            else
                                $display("tb_gpu_blit:   the framebuffer is already wrong at this point (its CRC is %h): check the commands before this FB_CRC", mine);
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
                    $display("tb_gpu_blit: counting check %0d fails. It covers: %0s", n_count, labels[n_got / 4 - 1]);
                    if (gaps != 0)
                        $display("tb_gpu_blit:   %0d pixels were never written (gaps); the first is x=%0d y=%0d",
                                 gaps, first_gap % 320, first_gap / 320);
                    if (dbl != 0)
                        $display("tb_gpu_blit:   %0d pixels were written more than once (double hits); the first is x=%0d y=%0d, written %0d times",
                                 dbl, first_dbl % 320, first_dbl / 320, back ? cnt1[first_dbl] : cnt0[first_dbl]);
                    if (front_hits != 0)
                        $display("tb_gpu_blit:   %0d pixels of the buffer on the screen were written", front_hits);
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

    // ---------------- main sequence ----------------
    integer i, p, n_fb_err = 0, n_pal_err = 0, first_bad = -1, first_buf = 0, t0, win_span = -1;
    integer n_win = 0, win_limit = 0, win_limit_hi = 0, win_bad = 0, win_bad_span = 0, win_bad_limit = 0, n_lim = 0;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_gpu_blit.vcd");
            $dumpvars(0, tb_gpu_blit);
        end
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
            $display("FAIL: tb_gpu_blit cannot read its files (run `.venv/bin/python sim/py/ref_blit.py sim` and Chapter 4.1's `ref_scanout.py --hex rtl/common`; run from build/sim)");
            $finish;
        end

        repeat (10) @(negedge clk);
        rst = 1'b0;
        repeat (100) @(negedge clk);

        p = 0;
        while (stream[p] !== 16'hFFFF && !timeout) begin
            if (stream[p] === 16'bx) begin
                $display("FAIL: tb_gpu_blit sim/blit_stream.hex has no end marker");
                $finish;
            end
            case (stream[p][15:12])
                4'h0: send_byte(stream[p][7:0]);
                4'h1: begin                                // fence
                    n_fence = n_fence + 1;
                    t0 = $time;
                    while (n_got < stream[p][11:0] && !timeout) begin
                        @(negedge clk);
                        if ($time - t0 > 50_000_000) begin  // 50 ms and no reply
                            $display("tb_gpu_blit: fence %0d waited 50 ms: %0d reply bytes so far, expected %0d. Waiting for the reply covering: %0s",
                                     n_fence, n_got, stream[p][11:0], labels[n_got / 4]);
                            timeout = 1'b1;
                        end
                    end
                    if (!timeout) check_replies(stream[p][11:0]);
                end
                4'h2: case (stream[p][2:0])
                    3'd0, 3'd1: honor = stream[p][0];
                    3'd2: begin in_win = 1'b1; win_first = -1; win_last = -1; win_wr = 0; end
                    3'd4: zero_counts;
                    3'd5: check_counts(1);
                    3'd6: check_counts(0);
                    3'd3: begin
                        in_win = 1'b0;
                        n_win = n_win + 1;
                        win_span = (win_first < 0) ? -1 : win_last - win_first + 1;
                        $display("tb_gpu_blit: throughput window %0d: %0d writes in %0d clocks from first to last (%0d allowed)",
                                 n_win, win_wr, win_span, win_limit);
                        if ((win_span < 0 || win_span > win_limit) && win_bad == 0) begin
                            win_bad = n_win; win_bad_span = win_span; win_bad_limit = win_limit;
                        end
                    end
                endcase
                4'h3: begin win_limit = win_limit_hi * 4096 + stream[p][11:0]; n_lim = n_lim + 1; end
                4'h4: win_limit_hi = stream[p][11:0];
                default: ;
            endcase
            p = p + 1;
        end

        #200_000;
        n_exp = 0;
        while (replies[n_exp] !== 8'bx) n_exp = n_exp + 1;
        check_replies(n_exp);

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
                    $display("tb_gpu_blit: palette entry %0d is %h, expected %h", i, pal[i], exp_pal[i]);
                n_pal_err = n_pal_err + 1;
            end
        if (first_bad >= 0)
            $display("tb_gpu_blit: %0d framebuffer bytes wrong at the end; first in buffer %0d at address %0d (x=%0d y=%0d): %h, expected %h",
                     n_fb_err, first_buf, first_bad, first_bad % 320, first_bad / 320,
                     first_buf ? fb1[first_bad] : fb0[first_bad], first_buf ? exp_fb1[first_bad] : exp_fb0[first_bad]);

        if (overflow_seen)
            $display("FAIL: tb_gpu_blit (o_overflow rose: the FIFO dropped bytes)");
        else if (timeout)
            $display("FAIL: tb_gpu_blit (a reply never came or the engine got stuck, see above)");
        else if (x_we != 0 || bad_wr != 0)
            $display("FAIL: tb_gpu_blit (bad framebuffer/palette writes, see above)");
        else if (front_wr != 0)
            $display("FAIL: tb_gpu_blit (%0d writes went to the buffer on the screen, not the back buffer)", front_wr);
        else if (count_err != 0)
            $display("FAIL: tb_gpu_blit (%0d of %0d counting checks failed: a pixel written twice, or a write to the front buffer)",
                     count_err, n_count);
        else if (rx_err != 0)
            $display("FAIL: tb_gpu_blit (%0d malformed bytes on o_tx)", rx_err);
        else if (n_got != n_exp || reply_err != 0)
            $display("FAIL: tb_gpu_blit (replies: got %0d bytes, expected %0d, %0d of %0d FB_CRC replies wrong)",
                     n_got, n_exp, reply_err, n_exp / 4);
        else if (n_fb_err != 0 || n_pal_err != 0)
            $display("FAIL: tb_gpu_blit (%0d framebuffer bytes and %0d palette entries differ from blit_fb0.hex, blit_fb1.hex and blit_pal.hex)",
                     n_fb_err, n_pal_err);
        else if (n_win == 0 || n_win != n_lim || win_bad != 0)
            $display("FAIL: tb_gpu_blit (throughput window %0d: the BLIT took %0d clocks from first to last write, more than %0d: aim for one sprite pixel per clock)",
                     win_bad, win_bad_span, win_bad_limit);
        else
            $display("PASS: tb_gpu_blit (%0d bytes sent, %0d FB_CRC replies match, %0d pixel writes, %0d counting checks, %0d throughput windows at one sprite pixel per clock)",
                     n_sent, n_exp / 4, n_fb_wr, n_count, n_win);
        $finish;
    end

    initial begin : watchdog
        #2_000_000_000;                                   // 2 s simulated
        $display("FAIL: tb_gpu_blit watchdog timeout after 2 s (sent %0d bytes; stuck waiting for o_ready?)", n_sent);
        $finish;
    end
endmodule
`default_nettype wire
