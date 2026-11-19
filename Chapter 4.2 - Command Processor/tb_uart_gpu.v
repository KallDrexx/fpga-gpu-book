// tb_uart_gpu.v -- self-checking testbench for uart_gpu (Chapter 4.2).
//
// The testbench is the host and the framebuffer. It sends sim/cmd_stream.hex
// into i_rx as real 8N1 serial at 2.5 Mbaud (20 clocks per bit at 50 MHz),
// decodes every reply byte on o_tx, and keeps its own framebuffer and palette
// RAMs on the DUT's draw ports (the same ports as fb_scanout's clk_sys side:
// writes on a rising edge, reads one clock later). At the end it compares
//
//   * every reply byte with sim/cmd_replies.hex (the FB_CRC results),
//   * all 76,800 framebuffer bytes with sim/cmd_fb.hex,
//   * all 256 palette entries with sim/cmd_pal.hex.
//
// The framebuffer starts as Chapter 4.1's test image (rtl/common/fb_test.hex)
// and the palette as pal_test.hex. The expected files come from
// `.venv/bin/python sim/py/ref_cmd.py sim`, which feeds the same stream to
// host/gpu_model.py.
//
// Stream entries (4 hex digits): 00NN = send byte NN; 1NNN = fence, wait
// until NNN (hex) reply bytes have arrived in all; 2000 = send without looking
// at o_ready; 2001 = wait for o_ready before starting each byte; FFFF = end.
//
// Also checked: o_overflow never rises; no write outside the 76,800 bytes; no
// X on the write enables; no reply bytes beyond the expected ones. If an
// FB_CRC reply is wrong, the testbench computes the CRC of its own copy of the
// framebuffer to tell you whether the pixels or the CRC unit are at fault.
//
// Run from build/sim (make test / make sim-tb_uart_gpu); files are read from
// ../../sim/ and ../../rtl/common/. Takes about 15 seconds.
`timescale 1ns/1ps
`default_nettype none

module tb_uart_gpu;
    localparam integer CLK_HZ  = 50_000_000;
    localparam integer BAUD    = 2_500_000;
    localparam integer BIT_NS  = 1_000_000_000 / BAUD;      // 400 ns = 20 clocks
    localparam integer FBN     = 76800;
    localparam         DIR     = "../../";

    reg clk = 1'b0;
    always #10 clk = ~clk;                                 // 50 MHz

    // ---------------- DUT ----------------
    reg         rst = 1'b1;
    reg         rx  = 1'b1;
    wire        tx, ready, overflow;
    wire        fb_we, pal_we;
    wire [16:0] fb_addr;
    wire [7:0]  fb_wdata, pal_addr;
    wire [23:0] pal_wdata;
    reg  [7:0]  fb_rdata = 8'd0;

    uart_gpu #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut (
        .i_clk(clk), .i_rst(rst), .i_rx(rx), .o_tx(tx),
        .o_ready(ready), .o_overflow(overflow),
        .o_fb_we(fb_we), .o_fb_addr(fb_addr), .o_fb_wdata(fb_wdata), .i_fb_rdata(fb_rdata),
        .o_pal_we(pal_we), .o_pal_addr(pal_addr), .o_pal_wdata(pal_wdata)
    );

    // ---------------- the framebuffer and palette the DUT draws into ----------------
    reg [7:0]  fb  [0:FBN-1];
    reg [23:0] pal [0:255];
    integer    n_fb_wr = 0, n_pal_wr = 0, bad_wr = 0, x_we = 0;
    always @(posedge clk) begin
        if (!rst && (fb_we === 1'bx || pal_we === 1'bx)) begin
            if (x_we == 0) $display("tb_uart_gpu: o_fb_we or o_pal_we is X at %0t", $time);
            x_we = x_we + 1;
        end
        if (fb_we === 1'b1) begin
            if (fb_addr >= FBN) begin
                if (bad_wr < 3) $display("tb_uart_gpu: framebuffer write to address %0d (past 76,799)", fb_addr);
                bad_wr = bad_wr + 1;
            end else
                fb[fb_addr] <= fb_wdata;
            fb_rdata <= fb_wdata;                          // write-first, like framebuffer.v
            n_fb_wr = n_fb_wr + 1;
        end else
            fb_rdata <= (fb_addr < FBN) ? fb[fb_addr] : 8'hxx;
        if (pal_we === 1'b1) begin
            pal[pal_addr] <= pal_wdata;
            n_pal_wr = n_pal_wr + 1;
        end
    end

    // ---------------- expected data ----------------
    reg [15:0] stream  [0:65535];
    reg [7:0]  replies [0:4095];
    reg [7:0]  exp_fb  [0:FBN-1];
    reg [23:0] exp_pal [0:255];
    integer    n_exp = 0;                                  // expected reply bytes

    // ---------------- reply receiver (8N1 on o_tx) ----------------
    reg [7:0] got [0:4095];
    integer   n_got = 0, rx_err = 0, k;
    reg [7:0] b;
    always begin
        @(negedge tx);
        #(BIT_NS / 2);
        if (tx !== 1'b0) begin
            if (rx_err < 3) $display("tb_uart_gpu: o_tx start bit too short at %0t", $time);
            rx_err = rx_err + 1;
        end else begin
            for (k = 0; k < 8; k = k + 1) begin
                #(BIT_NS);
                b[k] = tx;
            end
            #(BIT_NS);
            if (tx !== 1'b1) begin
                if (rx_err < 3) $display("tb_uart_gpu: o_tx stop bit missing at %0t", $time);
                rx_err = rx_err + 1;
            end
            if (n_got < 4096) got[n_got] = b;
            n_got = n_got + 1;
            #(BIT_NS / 4);                                  // into the stop bit's second half
        end
    end

    // ---------------- sender ----------------
    integer n_sent = 0, ready_low = 0;
    reg     honor = 1'b1;
    always @(posedge clk) if (!ready) ready_low = ready_low + 1;

    reg overflow_seen = 1'b0;
    always @(posedge clk)
        if (!rst && overflow === 1'b1 && !overflow_seen) begin
            $display("tb_uart_gpu: o_overflow rose after %0d bytes sent (honoring o_ready: %0d)", n_sent, honor);
            overflow_seen = 1'b1;
        end

    task send_byte(input [7:0] d);
        integer j;
        begin
            if (honor) begin
                @(negedge clk);
                while (ready !== 1'b1) @(negedge clk);
            end
            rx = 1'b0; #(BIT_NS);
            for (j = 0; j < 8; j = j + 1) begin rx = d[j]; #(BIT_NS); end
            rx = 1'b1; #(BIT_NS);
            n_sent = n_sent + 1;
        end
    endtask

    // ---------------- CRC of the testbench's own framebuffer (diagnosis only) ----------------
    function [31:0] fb_crc32(input integer dummy);
        integer a, j;
        reg [31:0] c;
        begin
            c = 32'hFFFFFFFF;
            for (a = 0; a < FBN; a = a + 1) begin
                c = c ^ {24'd0, fb[a]};
                for (j = 0; j < 8; j = j + 1)
                    c = c[0] ? (c >> 1) ^ 32'hEDB88320 : (c >> 1);
            end
            fb_crc32 = c ^ 32'hFFFFFFFF;
        end
    endfunction

    // Compares reply bytes [n_checked, upto) with the expected ones, 4 at a
    // time. Called at each fence, when the testbench's framebuffer holds the
    // state the last of those FB_CRCs saw, so a wrong last reply can be
    // blamed on the pixels or on the CRC unit.
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
                        $display("tb_uart_gpu: FB_CRC reply %0d is %h (bytes %h %h %h %h in the order sent), expected %h",
                                 r / 4 + 1, have, got[r], got[r+1], got[r+2], got[r+3], want);
                        if (r + 4 >= upto) begin
                            mine = fb_crc32(0);
                            if (mine === want)
                                $display("tb_uart_gpu:   the framebuffer is right at this point (its CRC is %h): check the CRC unit (polynomial, reflection, init, final XOR, reply byte order, which bytes it reads)", mine);
                            else
                                $display("tb_uart_gpu:   the framebuffer is already wrong at this point (its CRC is %h): check the commands before this FB_CRC", mine);
                        end
                    end
                    reply_err = reply_err + 1;
                end
            end
            n_checked = upto;
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

    // ---------------- main sequence ----------------
    integer i, p, n_fb_err = 0, n_pal_err = 0, first_bad = -1, t0;
    reg     timeout = 1'b0;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_uart_gpu.vcd");
            $dumpvars(0, tb_uart_gpu);
        end
        for (i = 0; i < 65536; i = i + 1) stream[i] = 16'bx;
        for (i = 0; i < 4096; i = i + 1) replies[i] = 8'bx;
        $readmemh({DIR, "rtl/common/fb_test.hex"}, fb);
        $readmemh({DIR, "rtl/common/pal_test.hex"}, pal);
        read_list({DIR, "sim/cmd_stream.hex"}, 0);
        read_list({DIR, "sim/cmd_replies.hex"}, 1);
        $readmemh({DIR, "sim/cmd_fb.hex"}, exp_fb);
        $readmemh({DIR, "sim/cmd_pal.hex"}, exp_pal);
        if (fb[FBN-1] === 8'bx || pal[255] === 24'bx || stream[0] === 16'bx ||
            exp_fb[FBN-1] === 8'bx || exp_pal[255] === 24'bx) begin
            $display("FAIL: tb_uart_gpu cannot read its files (run `.venv/bin/python sim/py/ref_cmd.py sim` and Chapter 4.1's `ref_scanout.py --hex rtl/common`; run from build/sim)");
            $finish;
        end

        repeat (10) @(negedge clk);
        rst = 1'b0;
        repeat (100) @(negedge clk);

        p = 0;
        while (stream[p] !== 16'hFFFF && !timeout) begin
            if (stream[p] === 16'bx) begin
                $display("FAIL: tb_uart_gpu sim/cmd_stream.hex has no end marker");
                $finish;
            end
            case (stream[p][15:12])
                4'h0: send_byte(stream[p][7:0]);
                4'h1: begin                                // fence
                    n_fence = n_fence + 1;
                    t0 = $time;
                    while (n_got < stream[p][11:0] && !timeout) begin
                        @(negedge clk);
                        if ($time - t0 > 20_000_000) begin  // 20 ms and no reply
                            $display("tb_uart_gpu: fence %0d waited 20 ms: %0d reply bytes so far, expected %0d (an FB_CRC not answered?)",
                                     n_fence, n_got, stream[p][11:0]);
                            timeout = 1'b1;
                        end
                    end
                    if (!timeout) check_replies(stream[p][11:0]);
                end
                4'h2: honor = stream[p][0];
                default: ;
            endcase
            p = p + 1;
        end

        // Anything still to come (extra replies) gets 200 us.
        #200_000;
        n_exp = 0;
        while (replies[n_exp] !== 8'bx) n_exp = n_exp + 1;
        check_replies(n_exp);

        for (i = 0; i < FBN; i = i + 1)
            if (fb[i] !== exp_fb[i]) begin
                if (first_bad < 0) first_bad = i;
                n_fb_err = n_fb_err + 1;
            end
        for (i = 0; i < 256; i = i + 1)
            if (pal[i] !== exp_pal[i]) begin
                if (n_pal_err == 0)
                    $display("tb_uart_gpu: palette entry %0d is %h, expected %h (SET_PALETTE payload is index, R, G, B; entry is {R,G,B})",
                             i, pal[i], exp_pal[i]);
                n_pal_err = n_pal_err + 1;
            end
        if (first_bad >= 0)
            $display("tb_uart_gpu: %0d framebuffer bytes wrong; first at address %0d (x=%0d y=%0d): %h, expected %h",
                     n_fb_err, first_bad, first_bad % 320, first_bad / 320, fb[first_bad], exp_fb[first_bad]);

        if (overflow_seen)
            $display("FAIL: tb_uart_gpu (o_overflow rose: the FIFO dropped bytes)");
        else if (timeout)
            $display("FAIL: tb_uart_gpu (a reply never came, see above)");
        else if (x_we != 0 || bad_wr != 0)
            $display("FAIL: tb_uart_gpu (bad framebuffer/palette writes, see above)");
        else if (rx_err != 0)
            $display("FAIL: tb_uart_gpu (%0d malformed bytes on o_tx)", rx_err);
        else if (n_got != n_exp || reply_err != 0)
            $display("FAIL: tb_uart_gpu (replies: got %0d bytes, expected %0d, %0d of %0d FB_CRC replies wrong)",
                     n_got, n_exp, reply_err, n_exp / 4);
        else if (n_fb_err != 0 || n_pal_err != 0)
            $display("FAIL: tb_uart_gpu (%0d framebuffer bytes and %0d palette entries differ from cmd_fb.hex / cmd_pal.hex)",
                     n_fb_err, n_pal_err);
        else
            $display("PASS: tb_uart_gpu (%0d bytes sent, %0d FB_CRC replies match, %0d pixel writes, %0d palette writes, o_ready low %0d clocks)",
                     n_sent, n_exp / 4, n_fb_wr, n_pal_wr, ready_low);
        $finish;
    end

    initial begin : watchdog
        #200_000_000;                                     // 200 ms simulated
        $display("FAIL: tb_uart_gpu watchdog timeout (sent %0d bytes; stuck waiting for o_ready?)", n_sent);
        $finish;
    end
endmodule
`default_nettype wire
