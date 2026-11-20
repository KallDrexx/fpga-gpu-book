// tb_gpu_fuzz.v -- fuzz testbench for the SPI link and command parser (Chapter 5.2).
//
// The testbench is an SPI master (mode 0) and the memory behind the draw ports.
// It plays sim/fuzz_stream.hex into gpu_link: rounds of garbage frames (random
// bytes, headers that promise payload that never comes, mutated packets,
// unknown opcodes, wrong lengths, frames cut inside a byte, empty frames),
// each closed by a CS rise, sometimes with UART bytes between frames, followed
// by a frame of valid commands ending in GET_INFO and often FB_CRC. The
// expected replies (FB_CRC and GET_INFO bytes, including the error flags),
// both framebuffers and the palette come from host/gpu_model.py, which treats
// a frame's first byte, and the first UART byte after SPI bytes, as "drop any
// partial packet" (Gpu.feed(data, sof=True)). The flip acknowledge follows
// o_flip_req only while i_vblank is high, so FLIPs wait up to 300 us.
//
// Hang detection (black box, nothing inside the DUT is looked at):
//   * a fence waits for the replies of its round with read frames; if no new
//     reply byte arrives for 40 ms simulated the parser or engine is stuck
//     (the longest command here takes about 1.6 ms) and the test FAILs;
//   * before every frame the master waits for READY; 50 ms of READY low
//     without a reply to read is a hang as well.
//
// After the stream, and after the framebuffers and palette are compared, the
// testbench drives the error flags: it overflows the FIFO on purpose and checks
// that OVERFLOW is set in GET_INFO and in the status byte, stays set, and is
// cleared only by CLEAR_ERRORS with bit 0 in the mask; it also checks the exact
// bytes of GET_INFO's reply (the same 9 bytes with every fence).
//
// Run from build/sim; files are read from ../../sim/ and ../../rtl/common/.
`timescale 1ns/1ps
`default_nettype none

module tb_gpu_fuzz;
    localparam integer FBN = 76800;
    localparam         DIR = "../../";

    reg clk = 1'b0;
    always #10 clk = ~clk;                                 // 50 MHz

    // ---------------- DUT ----------------
    reg         rst = 1'b1;
    reg         sck = 1'b0, mosi = 1'b0, cs_n = 1'b1;
    reg         vblank = 1'b0;
    reg         rx = 1'b1;                                 // the UART line into the GPU
    wire        miso, tx, ready, overflow;
    wire        fb_we, pal_we, fb_buf, flip_req;
    reg  [3:0]  ack_pipe = 4'b0000;
    wire        flip_ack = ack_pipe[3];
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    reg  [7:0]  fb_rdata = 8'd0;

    gpu_link #(.CLK_HZ(50_000_000), .BAUD(2_500_000)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx),
        .i_sck(sck), .i_mosi(mosi), .i_cs_n(cs_n), .o_miso(miso), .i_vblank(vblank),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata),
        .o_fb_buf(fb_buf), .o_flip_req(flip_req), .i_flip_ack(flip_ack),
        .i_ar_addr(15'd0), .i_as_id(4'd0)          // sprite side (Chapter 5.4): nothing reads the asset RAM here
    );
    always @(posedge clk) if (vblank) ack_pipe <= {ack_pipe[2:0], flip_req};   // swap only in vblank

    // ---------------- the framebuffers and palette ----------------
    reg [7:0]  fb0 [0:FBN-1];
    reg [7:0]  fb1 [0:FBN-1];
    reg [23:0] pal [0:255];
    integer    bad_wr = 0, x_we = 0, front_wr = 0;
    always @(posedge clk) begin
        if (!rst && (fb_we === 1'bx || pal_we === 1'bx)) begin
            if (x_we == 0) $display("tb_gpu_fuzz: o_fb_we or o_pal_we is X at %0t", $time);
            x_we = x_we + 1;
        end
        if (fb_we === 1'b1) begin
            if (fb_addr >= FBN) begin
                if (bad_wr < 3) $display("tb_gpu_fuzz: framebuffer write to address %0d (past 76,799) at %0t", fb_addr, $time);
                bad_wr = bad_wr + 1;
            end else begin
                if (fb_buf === flip_ack) front_wr = front_wr + 1;
                if (fb_buf) fb1[fb_addr] <= fb_wdata; else fb0[fb_addr] <= fb_wdata;
            end
            fb_rdata <= fb_wdata;
        end else
            fb_rdata <= (fb_addr >= FBN) ? 8'hxx : fb_buf ? fb1[fb_addr] : fb0[fb_addr];
        if (pal_we === 1'b1) pal[pal_addr] <= pal_wdata;
    end

    // ---------------- expected data ----------------
    reg [15:0]      stream  [0:262143];
    reg [7:0]       replies [0:16383];
    reg [7:0]       exp_fb0 [0:FBN-1];
    reg [7:0]       exp_fb1 [0:FBN-1];
    reg [23:0]      exp_pal [0:255];
    reg [8*128-1:0] labels  [0:1023];

    // ---------------- the SPI master ----------------
    reg [7:0] got [0:16383];                               // reply bytes read over MISO
    integer   n_got = 0, spi_err = 0, n_frames = 0, n_polls = 0, n_cuts = 0, n_empty = 0, n_rd_waits = 0;
    real      half = 80.0;
    integer   forced_half = 0;
    reg       timeout = 1'b0;
    reg       allow_ovf = 1'b0;                            // the overflow test expects OVERFLOW in the status
    integer   n_sent = 0;
    localparam real MOSI_DLY = 5.0, MISO_SETUP = 10.0;
    function real jit(input integer maxns);
        jit = $urandom_range(0, maxns * 10) / 10.0;
    endfunction
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

    integer miso_bad = 0;
    always @(miso)
        if (sck === 1'b1) begin
            if (miso_bad == 0) $display("tb_gpu_fuzz: MISO changed while SCK was high at %0t", $time);
            miso_bad = miso_bad + 1;
        end
    integer vb_wait = 0;
    always @(negedge clk) begin
        vb_wait = vb_wait - 1;
        if (vb_wait <= 0) begin vblank <= ~vblank; vb_wait = $urandom_range(1000, 15000); end
    end

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
    task xfer_bits(input integer k);                        // k clocks of random data, then stop with SCK low
        integer i;
        begin
            for (i = 0; i < k; i = i + 1) begin
                #(MOSI_DLY) mosi = $urandom_range(0, 1);
                #(half - MOSI_DLY + jit(20)) sck = 1'b1;
                #(half + jit(20)) sck = 1'b0;
            end
        end
    endtask

    // ---- the UART: 2.5 Mbaud = 20 clocks per bit; only between frames ----
    integer n_uart = 0, tx_low = 0;
    task uart_byte(input [7:0] b);
        integer i;
        begin
            @(negedge clk) rx = 1'b0;
            repeat (20) @(negedge clk);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; repeat (20) @(negedge clk); end
            rx = 1'b1;
            repeat (20 + $urandom_range(0, 40)) @(negedge clk);
            n_uart = n_uart + 1;
        end
    endtask
    always @(negedge tx)
        if (!rst) begin
            if (tx_low == 0) $display("tb_gpu_fuzz: a reply went out on the UART at %0t (no UART byte in the stream asks for one)", $time);
            tx_low = tx_low + 1;
        end

    // ---- a frame in progress ----
    reg        in_frame = 1'b0;
    integer    fr_n = 0;
    reg [7:0]  fr_miso [0:511];
    reg        fr_read = 1'b0;
    reg [7:0]  last_status = 8'h41;
    integer    tails [0:3];

    task open_frame_raw;
        begin
            half = forced_half != 0 ? forced_half : pick_half(0);
            fr_n = 0; fr_read = 1'b0;
            #(jit(20));
            cs_n = 1'b0;
            #(150 + jit(100));
            in_frame = 1'b1;
            n_frames = n_frames + 1;
        end
    endtask

    integer cnt_b, k2, nrep;
    task close_frame;
        begin
            if (in_frame) begin
                #(tails[$urandom_range(0, 3)]) cs_n = 1'b1;
                in_frame = 1'b0;
                if (fr_n >= 1) begin
                    last_status = fr_miso[0];
                    if (fr_miso[0][7:6] !== 2'b01) begin
                        if (spi_err == 0) $display("tb_gpu_fuzz: status byte %h does not start with 01 (MISO stuck?) in frame %0d (%0d bytes, %0s) at %0t", fr_miso[0], n_frames, fr_n, fr_read ? "read" : "command", $time);
                        spi_err = spi_err + 1;
                    end
                    if (fr_miso[0][5] !== 1'b0 || fr_miso[0][1] !== 1'b0 || (!allow_ovf && fr_miso[0][4] !== 1'b0)) begin
                        if (spi_err == 0) $display("tb_gpu_fuzz: status byte %h has OVERFLOW or a reserved bit set", fr_miso[0]);
                        spi_err = spi_err + 1;
                    end
                end
                if (fr_n >= 2) begin
                    cnt_b = fr_miso[1];
                    if (cnt_b > 16) begin
                        if (spi_err == 0) $display("tb_gpu_fuzz: reply count byte is %0d (the reply queue holds 16)", cnt_b);
                        spi_err = spi_err + 1;
                    end
                    nrep = fr_read ? fr_n - 2 : 0;
                    if (cnt_b < nrep) nrep = cnt_b;
                    for (k2 = 0; k2 < nrep; k2 = k2 + 1) begin
                        if (n_got < 16384) got[n_got] = fr_miso[2 + k2];
                        n_got = n_got + 1;
                    end
                    for (k2 = 2 + nrep; k2 < fr_n && k2 < 512; k2 = k2 + 1)
                        if (fr_miso[k2] !== 8'h00) begin
                            if (spi_err == 0) $display("tb_gpu_fuzz: %0s frame byte %0d is %h, expected 00 (count byte %0d)",
                                                       fr_read ? "read" : "command", k2, fr_miso[k2], cnt_b);
                            spi_err = spi_err + 1;
                        end
                end
                #(100 + (($urandom_range(0, 9) == 0) ? $urandom_range(0, 30000) : $urandom_range(0, 1500)));
            end
        end
    endtask

    task frame_byte(input [7:0] d);
        begin
            xfer(d);
            if (fr_n < 512) fr_miso[fr_n] = m_rx;
            fr_n = fr_n + 1;
        end
    endtask

    // a frame cut inside a byte: k more bits, then CS rises
    task close_cut(input integer k);
        begin
            if (in_frame) begin
                xfer_bits(k);
                n_cuts = n_cuts + 1;
                close_frame;
            end
        end
    endtask

    task poll_replies;
        integer j, n;
        begin
            open_frame_raw;
            fr_read = 1'b1;
            n = $urandom_range(2, 18);
            frame_byte(8'hFF);
            for (j = 1; j < n; j = j + 1) frame_byte($urandom);
            n_polls = n_polls + 1;
            close_frame;
        end
    endtask

    // wait for READY, reading replies while we wait (a full reply queue holds READY low)
    time t_rw;
    task open_frame;
        begin
            t_rw = $time;
            while (ready !== 1'b1 && !timeout) begin
                n_rd_waits = n_rd_waits + 1;
                poll_replies;
                #($urandom_range(100, 3000));
                if ($time - t_rw > 50_000_000) begin
                    $display("tb_gpu_fuzz: HANG: READY stayed low for 50 ms after %0d bytes (%0d reply bytes read)", n_sent, n_got);
                    timeout = 1'b1;
                end
            end
            if (!timeout) open_frame_raw;
        end
    endtask

    // ---------------- checks ----------------
    integer reply_err = 0, n_fence = 0, n_checked = 0, n_goal = 0;
    task check_replies(input integer upto);
        integer r;
        begin
            for (r = n_checked; r < upto; r = r + 1)
                if (got[r] !== replies[r]) begin
                    if (reply_err == 0)
                        $display("tb_gpu_fuzz: reply byte %0d is %h, expected %h (round: %0s)", r, got[r], replies[r], labels[n_fence - 1]);
                    reply_err = reply_err + 1;
                end
            n_checked = upto;
        end
    endtask

    reg overflow_seen = 1'b0;
    always @(posedge clk)
        if (!rst && overflow === 1'b1 && !allow_ovf && !overflow_seen) begin
            $display("tb_gpu_fuzz: o_overflow is high outside the overflow test (after %0d bytes sent)", n_sent);
            overflow_seen = 1'b1;
        end

    // ---------------- GET_INFO and the overflow test ----------------
    reg [7:0] info8;
    integer   info_err = 0;
    reg [7:0] want_info [0:7];
    task fence_hang(input integer goal, input [8*128-1:0] what);
        time t0;
        integer last;
        begin
            t0 = $time; last = n_got;
            while (n_got < goal && !timeout) begin
                poll_replies;
                #($urandom_range(100, 3000));
                if (n_got != last) begin last = n_got; t0 = $time; end
                if ($time - t0 > 40_000_000) begin
                    $display("tb_gpu_fuzz: HANG: %0s: no new reply byte for 40 ms (%0d of %0d bytes read after %0d bytes sent)",
                             what, n_got, goal, n_sent);
                    timeout = 1'b1;
                end
            end
        end
    endtask
    integer ii;
    reg [7:0] sb [0:1023];
    task send_frame(input integer n);
        integer q;
        begin
            open_frame;
            for (q = 0; q < n && !timeout; q = q + 1) begin frame_byte(sb[q]); n_sent = n_sent + 1; end
            close_frame;
        end
    endtask
    // GET_INFO in a frame of its own; checks bytes 0-7, returns the error byte
    integer g0;
    task get_info;
        begin
            sb[0] = 8'h0B; sb[1] = 8'h00;
            g0 = n_got;
            send_frame(2);
            fence_hang(g0 + 9, "GET_INFO (error flag test)");
            if (!timeout) begin
                for (ii = 0; ii < 8; ii = ii + 1)
                    if (got[g0 + ii] !== want_info[ii]) begin
                        if (info_err == 0) $display("tb_gpu_fuzz: GET_INFO byte %0d is %h, expected %h", ii, got[g0 + ii], want_info[ii]);
                        info_err = info_err + 1;
                    end
                info8 = got[g0 + 8];
                n_checked = n_got; n_goal = n_got;
            end
        end
    endtask
    task clear_errors(input [7:0] mask);
        begin
            sb[0] = 8'h0C; sb[1] = 8'h01; sb[2] = mask;
            send_frame(3);
        end
    endtask
    integer flag_err = 0;
    task expect_flags(input [8*40-1:0] what, input [3:0] mask, input [3:0] value, input st_ovf);
        begin
            get_info;
            poll_replies;
            if (!timeout && ((info8[3:0] & mask) !== value || info8[7:4] !== 4'h0 || last_status[4] !== st_ovf)) begin
                if (flag_err == 0)
                    $display("tb_gpu_fuzz: %0s: GET_INFO error byte %h (want bits %b under mask %b, upper bits 0), status OVERFLOW bit %b (want %b)",
                             what, info8, value, mask, last_status[4], st_ovf);
                flag_err = flag_err + 1;
            end
        end
    endtask

    // ---------------- main sequence ----------------
    integer i, p, n_fb_err = 0, n_pal_err = 0, first_bad = -1, first_buf = 0, n_exp, flip_n;
    integer fence_goal = 0;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_gpu_fuzz.vcd");
            $dumpvars(0, tb_gpu_fuzz);
        end
        if (!$value$plusargs("half=%d", forced_half)) forced_half = 0;
        tails[0] = 0; tails[1] = 2; tails[2] = 20; tails[3] = 100;
        want_info[0] = 8'h47; want_info[1] = 8'h01; want_info[2] = 8'h7F; want_info[3] = 8'h02;
        want_info[4] = 8'h40; want_info[5] = 8'h01; want_info[6] = 8'hF0; want_info[7] = 8'h00;
        for (i = 0; i < 262144; i = i + 1) stream[i] = 16'bx;
        for (i = 0; i < 16384; i = i + 1) replies[i] = 8'bx;
        for (i = 0; i < 1024; i = i + 1) labels[i] = "(no label)";
        $readmemh({DIR, "rtl/common/fb_test.hex"}, fb0);
        $readmemh({DIR, "rtl/common/fb_test.hex"}, fb1);
        $readmemh({DIR, "rtl/common/pal_test.hex"}, pal);
        read_list({DIR, "sim/fuzz_stream.hex"}, 0);
        read_list({DIR, "sim/fuzz_replies.hex"}, 1);
        read_labels({DIR, "sim/fuzz_labels.txt"});
        $readmemh({DIR, "sim/fuzz_fb0.hex"}, exp_fb0);
        $readmemh({DIR, "sim/fuzz_fb1.hex"}, exp_fb1);
        $readmemh({DIR, "sim/fuzz_pal.hex"}, exp_pal);
        if (fb0[FBN-1] === 8'bx || fb1[FBN-1] === 8'bx || pal[255] === 24'bx || stream[0] === 16'bx ||
            exp_fb0[FBN-1] === 8'bx || exp_fb1[FBN-1] === 8'bx || exp_pal[255] === 24'bx) begin
            $display("FAIL: tb_gpu_fuzz cannot read its files (run `.venv/bin/python sim/py/ref_fuzz.py sim` and Chapter 4.1's `ref_scanout.py --hex rtl/common`; run from build/sim)");
            $finish;
        end
        repeat (10) @(negedge clk);
        rst = 1'b0;
        repeat (100) @(negedge clk);

        p = 0;
        while (stream[p] !== 16'hFFFF && !timeout) begin
            if (stream[p] === 16'bx) begin
                $display("FAIL: tb_gpu_fuzz sim/fuzz_stream.hex has no end marker");
                $finish;
            end
            if (stream[p][15:12] == 4'h0) begin
                frame_byte(stream[p][7:0]);
                n_sent = n_sent + 1;
            end else case (stream[p][15:12])
                4'hA: if (stream[p][11:8] == 4'h1) close_cut(stream[p][3:0]);     // A10K: K bits, then CS rises
                      else case (stream[p][3:0])
                    4'h0: begin
                        open_frame;                           // waits for READY first
                        if (stream[p+1] === 16'hA001) n_empty = n_empty + 1;
                    end
                    4'h1: close_frame;
                    4'h3: poll_replies;
                    default: ;
                endcase
                4'hB: begin                                  // B0NN: a UART byte, never inside a frame
                    if (stream[p-1][15:12] != 4'hB) begin
                        t_rw = $time;                          // wait for READY first, as for a frame
                        while (ready !== 1'b1 && !timeout) begin
                            poll_replies;
                            #($urandom_range(100, 3000));
                            if ($time - t_rw > 50_000_000) begin
                                $display("tb_gpu_fuzz: HANG: READY stayed low for 50 ms before UART bytes");
                                timeout = 1'b1;
                            end
                        end
                    end
                    uart_byte(stream[p][7:0]);
                    n_sent = n_sent + 1;
                end
                4'h1: begin
                    n_fence = n_fence + 1;
                    fence_goal = fence_goal + stream[p][11:0];
                    fence_hang(fence_goal, labels[n_fence - 1]);
                    if (!timeout) begin
                        if (n_got != fence_goal)
                            if (reply_err == 0) begin
                                $display("tb_gpu_fuzz: %0d reply bytes after the fence, expected %0d (round: %0s)", n_got, fence_goal, labels[n_fence - 1]);
                                reply_err = reply_err + 1;
                            end
                        check_replies(fence_goal);
                    end
                end
                default: ;
            endcase
            p = p + 1;
        end
        close_frame;

        // everything done: replies all read, nothing left over, memories equal
        #200_000;
        poll_replies; poll_replies;
        n_exp = 0;
        while (replies[n_exp] !== 8'bx) n_exp = n_exp + 1;
        check_replies(n_exp < n_got ? n_exp : n_got);
        if (n_got != n_exp && reply_err == 0) begin
            $display("tb_gpu_fuzz: %0d reply bytes read, %0d expected", n_got, n_exp);
            reply_err = reply_err + 1;
        end
        if (fr_miso[0][2] !== 1'b0 || fr_miso[1] !== 8'd0) begin
            $display("tb_gpu_fuzz: the last poll still shows a reply waiting (status %h, count %0d)", fr_miso[0], fr_miso[1]);
            reply_err = reply_err + 1;
        end
        for (i = 0; i < FBN; i = i + 1) begin
            if (fb0[i] !== exp_fb0[i]) begin if (first_bad < 0) begin first_bad = i; first_buf = 0; end n_fb_err = n_fb_err + 1; end
            if (fb1[i] !== exp_fb1[i]) begin if (first_bad < 0) begin first_bad = i; first_buf = 1; end n_fb_err = n_fb_err + 1; end
        end
        for (i = 0; i < 256; i = i + 1)
            if (pal[i] !== exp_pal[i]) begin
                if (n_pal_err == 0) $display("tb_gpu_fuzz: palette entry %0d is %h, expected %h", i, pal[i], exp_pal[i]);
                n_pal_err = n_pal_err + 1;
            end
        if (first_bad >= 0)
            $display("tb_gpu_fuzz: %0d framebuffer bytes wrong at the end; first in buffer %0d at address %0d (x=%0d y=%0d)",
                     n_fb_err, first_buf, first_bad, first_bad % 320, first_bad / 320);

        // ---- error flags: GET_INFO bytes, overflow on purpose, sticky, cleared by CLEAR_ERRORS ----
        if (!timeout) begin
            clear_errors(8'h0F);
            expect_flags("after CLEAR_ERRORS 0F", 4'hF, 4'h0, 1'b0);
            // unknown opcode, wrong length, and a packet cut by the next frame: three flags, all sticky
            sb[0] = 8'h7E; sb[1] = 8'h01; sb[2] = 8'h00; sb[3] = 8'h03; sb[4] = 8'h00;
            send_frame(5);                                          // BAD_OP, then PLOT with length 0: BAD_LEN
            sb[0] = 8'h03; sb[1] = 8'h05; sb[2] = 8'h01;
            send_frame(3);                                          // a PLOT cut short ...
            expect_flags("BAD_OP, BAD_LEN, TRUNC", 4'hF, 4'hE, 1'b0);   // ... TRUNC shows once the next frame begins
            expect_flags("flags stay set when read", 4'hF, 4'hE, 1'b0);
            clear_errors(8'h04);
            expect_flags("CLEAR_ERRORS 04 clears BAD_OP only", 4'hF, 4'hA, 1'b0);
            clear_errors(8'hF0);
            expect_flags("CLEAR_ERRORS F0 clears nothing", 4'hF, 4'hA, 1'b0);
            // overflow: a CLEAR keeps the engine busy 1.5 ms while 700 bytes of NOPs go in without looking at READY
            allow_ovf = 1'b1;
            sb[0] = 8'h02; sb[1] = 8'h01; sb[2] = 8'h05;
            for (i = 3; i < 703; i = i + 1) sb[i] = 8'h00;
            half = 80.0;
            forced_half = 80;
            send_frame(703);
            forced_half = 0;
            #3_000_000;
            expect_flags("after an overflow", 4'h1, 4'h1, 1'b1);
            expect_flags("OVERFLOW is sticky", 4'h1, 4'h1, 1'b1);
            clear_errors(8'h0E);
            expect_flags("CLEAR_ERRORS 0E leaves OVERFLOW", 4'h1, 4'h1, 1'b1);
            clear_errors(8'h01);
            allow_ovf = 1'b0;
            expect_flags("CLEAR_ERRORS 01 clears OVERFLOW", 4'h1, 4'h0, 1'b0);
            clear_errors(8'h0F);
            expect_flags("all clear again", 4'hF, 4'h0, 1'b0);
        end

        if (overflow_seen)
            $display("FAIL: tb_gpu_fuzz (o_overflow was high outside the overflow test: the FIFO dropped bytes, or CLEAR_ERRORS 01 did not clear it)");
        else if (timeout)
            $display("FAIL: tb_gpu_fuzz (HANG or lost reply, see above)");
        else if (x_we != 0 || bad_wr != 0 || front_wr != 0)
            $display("FAIL: tb_gpu_fuzz (bad framebuffer/palette writes or %0d writes to the front buffer during a FLIP, see above)", front_wr);
        else if (tx_low != 0)
            $display("FAIL: tb_gpu_fuzz (a reply went to the UART, see above)");
        else if (spi_err != 0 || miso_bad != 0)
            $display("FAIL: tb_gpu_fuzz (%0d bad status or count bytes, %0d MISO changes while SCK was high, see above)", spi_err, miso_bad);
        else if (reply_err != 0)
            $display("FAIL: tb_gpu_fuzz (replies wrong, see above)");
        else if (n_fb_err != 0 || n_pal_err != 0)
            $display("FAIL: tb_gpu_fuzz (%0d framebuffer bytes and %0d palette entries differ from the model after the fuzz stream)", n_fb_err, n_pal_err);
        else if (info_err != 0 || flag_err != 0)
            $display("FAIL: tb_gpu_fuzz (GET_INFO bytes or error flags wrong, see above)");
        else
            $display("PASS: tb_gpu_fuzz (%0d bytes in %0d frames and %0d on the UART, %0d cut inside a byte, %0d empty, %0d polls, %0d fences, %0d reply bytes match, framebuffers and palette match, error flags checked)",
                     n_sent, n_frames, n_uart, n_cuts, n_empty, n_polls, n_fence, n_exp);
        $finish;
    end

    initial begin : watchdog
        #3_000_000_000;                                   // 3 s simulated
        $display("FAIL: tb_gpu_fuzz watchdog timeout after 3 s (sent %0d bytes)", n_sent);
        $finish;
    end

    // ---------------- file readers ----------------
    task read_list(input [8*64-1:0] name, input integer which);
        integer fd, n, rc;
        reg [15:0] v;
        reg [8*200-1:0] line;
        begin
            fd = $fopen(name, "r");
            if (fd != 0) begin
                rc = $fgets(line, fd);
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
                while (rc > 0 && n < 1024) begin
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
endmodule
`default_nettype wire
