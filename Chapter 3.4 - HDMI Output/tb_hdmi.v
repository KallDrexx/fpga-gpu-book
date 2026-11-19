// tb_hdmi -- self-checking testbench for Step 3.4 (HDMI/DVI color bars)
//
// Design under test: projects/3.4-hdmi-bars/top_artyz7.v (see "Testbench
// requirements" in Chapter 3.4), with your clk_gen, vga_timing, color_bars,
// tmds_encoder and serializers below it. The Xilinx primitives come from the
// book's models in sim/: MMCME2_BASE, BUFG (Chapter 3.2), OSERDESE2, OBUFDS
// (this chapter).
//
// The testbench drives the 125 MHz board clock and watches only the four
// TMDS pairs, like a monitor would:
//   1. Bit timing. It waits for the clock pair to toggle with a clean 40 ns
//      period, then samples every lane in the middle of each 4 ns bit.
//   2. Words. The clock pair must send 1111100000 (bit 0 first) in every
//      word: five 0s, then five 1s. A word starts at its falling edge.
//      The data lanes are framed the same way. (If your data words start a
//      few bits later than the clock's falling edge, which a monitor
//      tolerates, the test finds that from the control symbols, says so,
//      and carries on.)
//   3. Symbols. Each data word is decoded as TMDS and checked bit for bit
//      against the DVI 1.0 encoding of the byte it carries. All three
//      channels must agree on de. N and P must always be complements.
//   4. Video. Two whole frames are compared clock for clock with the
//      expected timing and the eight color bars, 8'hFF or 8'h00 per channel:
//      de, hsync and vsync from channel 0's C0/C1, CTL bits 0 on channels 1
//      and 2, red on channel 2, green on 1, blue on 0. Any fixed latency is
//      accepted: the frames are lined up on the first vsync falling edge.
//      To keep the run short, the top's vertical timing is overridden to a
//      15-line frame (V_VISIBLE 6, V_FRONT 3, V_SYNC 2, V_BACK 4); lines
//      keep their real 640x480 timing (800 clocks, 640 visible). A full
//      800x525 frame would take several minutes to simulate at 250 Mbit/s.
//   5. Reset. btn[0] is pressed for 200 ns. The clock pair must stop, then
//      start again, and the next 3,000 pixel clocks must again be correct
//      video (the frame position is found again, since vga_timing pauses
//      while its clock is stopped).
//
// Runs in about 20 seconds. Ends with one PASS or FAIL line. +vcd writes
// tb_hdmi.vcd (about 1 ms at 250 Mbit/s, so it is large).

`timescale 1ns/1ps
`default_nettype none

module tb_hdmi;
    // 640-pixel lines as in 640x480 (800 clocks each), but a short frame of
    // 15 lines so that whole frames simulate quickly: the top's vertical
    // timing parameters are overridden. Board builds use the defaults.
    localparam integer H_VIS = 640, H_FP = 16, H_SW = 96, H_TOT = 800;
    localparam integer V_VIS = 6, V_FP = 3, V_SW = 2, V_BP = 4;
    localparam integer V_TOT  = V_VIS + V_FP + V_SW + V_BP;   // 15 lines
    localparam integer NFRAME = H_TOT * V_TOT;          // 12,000
    localparam integer NCHK   = 2 * NFRAME;             // two frames checked
    localparam integer NREC   = NCHK + 64;              // words recorded
    localparam integer NRST   = 3000;                   // words after reset
    localparam integer MAXREC = NREC;

    localparam [9:0] CLK_WORD = 10'b1111100000;
    localparam [9:0] CTRL_00 = 10'b1101010100,   // 0x354
                     CTRL_01 = 10'b0010101011,   // 0x0AB
                     CTRL_10 = 10'b0101010100,   // 0x154
                     CTRL_11 = 10'b1010101011;   // 0x2AB

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
            $display("FAIL: tb_hdmi, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
            $finish;
        end
    endtask

    // ===================================================== DUT
    reg        clk = 1'b0;
    always #4 clk = ~clk;                     // 125 MHz board clock
    reg  [3:0] btn = 4'b0000;
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
        .led           (led),
        .hdmi_tx_clk_p (tx_clk_p),
        .hdmi_tx_clk_n (tx_clk_n),
        .hdmi_tx_d_p   (tx_d_p),
        .hdmi_tx_d_n   (tx_d_n)
    );

    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_hdmi.vcd");
            $dumpvars(0, tb_hdmi);
        end
    end

    // ===================================================== DVI 1.0 model
    // (Same algorithm as tb_tmds; kept separate from the reader's code.)
    integer spec_cnt;
    function [9:0] spec_encode(input [7:0] d, input integer cnt);
        integer j, n1d, n1q, n0q;
        reg [8:0] qm;
        reg [9:0] q;
        begin
            n1d = 0;
            for (j = 0; j < 8; j = j + 1) n1d = n1d + d[j];
            qm[0] = d[0];
            if (n1d > 4 || (n1d == 4 && d[0] == 1'b0)) begin
                for (j = 1; j < 8; j = j + 1) qm[j] = ~(qm[j-1] ^ d[j]);
                qm[8] = 1'b0;
            end else begin
                for (j = 1; j < 8; j = j + 1) qm[j] = qm[j-1] ^ d[j];
                qm[8] = 1'b1;
            end
            n1q = 0;
            for (j = 0; j < 8; j = j + 1) n1q = n1q + qm[j];
            n0q = 8 - n1q;
            if (cnt == 0 || n1q == n0q) begin
                q = {~qm[8], qm[8], qm[8] ? qm[7:0] : ~qm[7:0]};
                spec_cnt = qm[8] ? cnt + (n1q - n0q) : cnt + (n0q - n1q);
            end else if ((cnt > 0 && n1q > n0q) || (cnt < 0 && n0q > n1q)) begin
                q = {1'b1, qm[8], ~qm[7:0]};
                spec_cnt = cnt + 2 * qm[8] + (n0q - n1q);
            end else begin
                q = {1'b0, qm[8], qm[7:0]};
                spec_cnt = cnt - 2 * (1 - qm[8]) + (n1q - n0q);
            end
            spec_encode = q;
        end
    endfunction

    // Control symbol -> {valid, C1, C0}
    function [2:0] ctrl_of(input [9:0] s);
        case (s)
            CTRL_00: ctrl_of = 3'b100;
            CTRL_01: ctrl_of = 3'b101;
            CTRL_10: ctrl_of = 3'b110;
            CTRL_11: ctrl_of = 3'b111;
            default: ctrl_of = 3'b000;
        endcase
    endfunction

    function [7:0] tmds_data(input [9:0] s);
        reg [7:0] q;
        integer j;
        begin
            q = s[9] ? ~s[7:0] : s[7:0];
            tmds_data[0] = q[0];
            for (j = 1; j < 8; j = j + 1)
                tmds_data[j] = s[8] ? (q[j] ^ q[j-1]) : ~(q[j] ^ q[j-1]);
        end
    endfunction

    function [9:0] rev10(input [9:0] v);
        integer j;
        for (j = 0; j < 10; j = j + 1) rev10[j] = v[9 - j];
    endfunction

    function [8*12-1:0] bin10(input [9:0] v);   // printed bit 9 first
        integer j;
        begin
            bin10 = "";
            for (j = 9; j >= 0; j = j - 1)
                bin10 = {bin10[8*11-1:0], (v[j] === 1'b1) ? "1" : (v[j] === 1'b0) ? "0" : "x"};
        end
    endfunction

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
    // Bars: white, yellow, cyan, green, magenta, red, blue, black.
    function [23:0] exp_rgb(input integer p);
        integer bar;
        begin
            bar = (p % H_TOT) / 80;
            exp_rgb = {bar[1] ? 8'h00 : 8'hFF, bar[2] ? 8'h00 : 8'hFF, bar[0] ? 8'h00 : 8'hFF};
        end
    endfunction

    // ===================================================== serial capture
    // Falling-edge tracker for the clock pair: interval between the last two
    // clean 1 -> 0 transitions, and how many clean 40 ns periods in a row.
    real    t_fall = -1.0, t_fall_prev = -1.0;
    integer good_falls = 0;
    reg     cp_q = 1'b0;
    integer n_cp_edges = 0;
    always @(tx_clk_p) begin
        n_cp_edges = n_cp_edges + 1;
        if (tx_clk_p !== 1'b0 && tx_clk_p !== 1'b1) good_falls = 0;
        else if (tx_clk_p === 1'b0 && cp_q === 1'b1) begin
            t_fall_prev = t_fall;
            t_fall      = $realtime;
            if (t_fall_prev >= 0.0 && t_fall - t_fall_prev > 39.99 && t_fall - t_fall_prev < 40.01)
                good_falls = good_falls + 1;
            else
                good_falls = 0;
        end
        cp_q = tx_clk_p;
    end

    // Recorded words, bit 0 = first bit on the wire, framed at the clock
    // pair's falling edge. Lane 3 is the clock pair, 0-2 the data channels.
    reg [9:0] rw0 [0:MAXREC-1];
    reg [9:0] rw1 [0:MAXREC-1];
    reg [9:0] rw2 [0:MAXREC-1];
    reg [9:0] rwc [0:MAXREC-1];
    integer   n_diff = 0;            // P/N not complementary

    // Wait until the clock pair has had 4 clean 40 ns periods, then return
    // the time of a word boundary (a clock falling edge) in the future.
    real t_word0;
    task find_clock(input real timeout_ns, input string when);
        real deadline;
        reg [8*20-1:0] pat;
        integer j, n_xs;
        begin
            deadline = $realtime + timeout_ns;
            good_falls = 0;
            while (good_falls < 4 && $realtime < deadline) #4;
            if (good_falls < 4) begin
                pat = "";
                n_xs = 0;
                for (j = 0; j < 20; j = j + 1) begin
                    #2;
                    pat = {pat[8*19-1:0], (tx_clk_p === 1'b1) ? "1" : (tx_clk_p === 1'b0) ? "0" : "x"};
                    if (tx_clk_p !== 1'b0 && tx_clk_p !== 1'b1) n_xs = n_xs + 1;
                end
                $display("tb_hdmi: clock pair sampled every 2 ns: %0s", pat);
                if (n_xs > 0 && n_xs < 20)
                    finish_fail($sformatf("%0s: the clock pair has X bits between valid ones. Bits 8 and 9 of every word come from the slave OSERDESE2: its SHIFTOUT1/SHIFTOUT2 must drive the master's SHIFTIN1/SHIFTIN2, and the slave needs SERDES_MODE \"SLAVE\"",
                                          when));
                else if (tx_clk_p !== 1'b0 && tx_clk_p !== 1'b1)
                    finish_fail($sformatf("%0s: the clock pair is %b after %0.0f us. The OSERDESE2 model's OQ is X until RST has been high while CLK and CLKDIV run, after a clock-ratio or parameter error (see OSERDESE2 messages above), and always on an OSERDESE2 with SERDES_MODE \"SLAVE\" (the pin must be driven by the MASTER)",
                                          when, tx_clk_p, timeout_ns / 1000.0));
                else if (t_fall_prev >= 0.0 && $realtime - t_fall < 200.0)
                    finish_fail($sformatf("%0s: the clock pair falls every %0.1f ns; it must send 1111100000 every 40 ns pixel clock (a 25 MHz square wave)",
                                          when, t_fall - t_fall_prev));
                else
                    finish_fail($sformatf("%0s: the clock pair is not toggling (stuck at %b) after %0.0f us",
                                          when, tx_clk_p, timeout_ns / 1000.0));
            end
            t_word0 = t_fall;
            while (t_word0 + 2.0 <= $realtime) t_word0 = t_word0 + 40.0;
        end
    endtask

    task record(input integer nwords);
        integer w, b;
        real t;
        reg [9:0] v0, v1, v2, vc;
        begin
            for (w = 0; w < nwords; w = w + 1) begin
                for (b = 0; b < 10; b = b + 1) begin
                    t = t_word0 + 40.0 * w + 4.0 * b + 2.0;
                    #(t - $realtime);
                    vc[b] = tx_clk_p; v0[b] = tx_d_p[0]; v1[b] = tx_d_p[1]; v2[b] = tx_d_p[2];
                    if ({tx_clk_n, tx_d_n} !== ~{tx_clk_p, tx_d_p}) begin
                        n_diff = n_diff + 1;
                        if (n_diff <= 2)
                            err($sformatf("at %0t: P/N pairs are not complementary: clk p/n=%b/%b, d_p=%b d_n=%b (each pair needs one OBUFDS)",
                                          $time, tx_clk_p, tx_clk_n, tx_d_p, tx_d_n));
                    end
                end
                rwc[w] = vc; rw0[w] = v0; rw1[w] = v1; rw2[w] = v2;
            end
        end
    endtask

    // Word n of a data lane, starting `off` bits after the clock's word
    // boundary.
    function [9:0] lane_word(input integer ch, input integer n, input integer off);
        reg [19:0] two;
        begin
            case (ch)
                0: two = {rw0[n + 1], rw0[n]};
                1: two = {rw1[n + 1], rw1[n]};
                default: two = {rw2[n + 1], rw2[n]};
            endcase
            lane_word = two >> off;
        end
    endfunction

    // ===================================================== analysis
    // Decoded words (per word index): de, {vs, hs} from channel 0, CTL bits
    // of channels 1/2, colors. ok = 0 marks an undecodable word.
    reg        o_ok  [0:MAXREC-1];
    reg        o_de  [0:MAXREC-1];
    reg  [1:0] o_c0  [0:MAXREC-1];
    reg  [3:0] o_c12 [0:MAXREC-1];
    reg [23:0] o_rgb [0:MAXREC-1];

    integer off;          // data framing offset in bits

    task check_clock_words(input integer nwords, input string when);
        integer n, bad;
        begin
            bad = 0;
            for (n = 0; n < nwords; n = n + 1)
                if (rwc[n] !== CLK_WORD) begin
                    bad = bad + 1;
                    if (bad <= 2)
                        err($sformatf("%0s: clock pair word %0d is %s (printed bit 9 first); expected %s every word: five 0s on the wire, then five 1s",
                                      when, n, bin10(rwc[n]), bin10(CLK_WORD)));
                end
            if (bad > 2) err($sformatf("%0s: %0d of %0d clock pair words were wrong", when, bad, nwords));
            if (bad > 0 && ^rwc[0] !== 1'bx && $countones(rwc[0]) == 3)
                err($sformatf("%0s: the clock pair sends only three 1s per word: bits 8 and 9 are lost. They go on D3 and D4 of the slave OSERDESE2 (its D1 and D2 are unused)", when));
        end
    endtask

    // How many of channel 0's first words are valid DVI 1.0 symbols (a
    // control symbol, or exactly the DVI symbol for the byte it decodes to)
    // when framed `o` bits after the clock's word boundary, optionally with
    // the bit order reversed.
    task lane_quality(input integer nwords, input integer o, input integer rv,
                      output integer good, output integer nctl);
        integer n, cnt, have;
        reg [9:0] w;
        begin
            good = 0; nctl = 0; cnt = 0; have = 0;
            for (n = 0; n < nwords - 1 && n < 4000; n = n + 1) begin
                w = lane_word(0, n, o);
                if (rv) w = rev10(w);
                if (ctrl_of(w) != 0) begin
                    good = good + 1; nctl = nctl + 1; cnt = 0; have = 1;
                end else if (have && spec_encode(tmds_data(w), cnt) === w) begin
                    good = good + 1; cnt = spec_cnt;
                end else
                    have = 0;
            end
        end
    endtask

    // Pick the framing at which channel 0 is valid TMDS.
    task find_offset(input integer nwords, input string when);
        integer o, rv, good, nctl, best, best_rv, best_good, best_ctl;
        begin
            best = 0; best_rv = 0; best_good = -1; best_ctl = 0;
            for (rv = 0; rv < 2; rv = rv + 1)
                for (o = 0; o < 10; o = o + 1) begin
                    lane_quality(nwords, o, rv, good, nctl);
                    if (good > best_good) begin
                        best_good = good; best = o; best_rv = rv; best_ctl = nctl;
                    end
                end
            if (best_ctl < 100)
                finish_fail($sformatf("%0s: no TMDS control symbols on channel 0 at any bit offset (words %s %s %s). Check the serializer wiring and that the encoders run",
                                      when, bin10(rw0[100]), bin10(rw0[101]), bin10(rw0[102])));
            if (best_rv)
                finish_fail($sformatf("%0s: channel 0 is valid TMDS only with each symbol's bits reversed: send bit 0 first. It goes on D1 of the master OSERDESE2, bits 8 and 9 on the slave's D3 and D4",
                                      when));
            off = best;
            if (off != 0)
                $display("tb_hdmi: note: data words start %0d bits after the clock pair's falling edge (the book sends 1111100000 so they line up); monitors accept this", off);
        end
    endtask

    task decode(input integer nwords, input string when);
        integer n, ch, cnt [0:2], have_cnt [0:2], n_bad, n_dis, n_x;
        reg [9:0] s, e;
        reg [2:0] c;
        reg       dv [0:2];
        reg [7:0] dd [0:2];
        reg [1:0] cc [0:2];
        begin
            n_bad = 0; n_dis = 0; n_x = 0;
            for (ch = 0; ch < 3; ch = ch + 1) begin cnt[ch] = 0; have_cnt[ch] = 0; end
            for (n = 0; n < nwords - 1; n = n + 1) begin
                o_ok[n] = 1'b1;
                for (ch = 0; ch < 3; ch = ch + 1) begin
                    s = lane_word(ch, n, off);
                    if (^s === 1'bx) begin
                        o_ok[n] = 1'b0;
                        n_x = n_x + 1;
                        if (n_x <= 2) begin
                            if (^s[7:0] !== 1'bx)
                                err($sformatf("%0s: channel %0d word %0d is %s: bits 8 and 9 are X. They come from the slave OSERDESE2; connect its SHIFTOUT1/SHIFTOUT2 to the master's SHIFTIN1/SHIFTIN2",
                                              when, ch, n, bin10(s)));
                            else
                                err($sformatf("%0s: channel %0d word %0d is %s (X bits)", when, ch, n, bin10(s)));
                        end
                        dv[ch] = 1'b0; dd[ch] = 8'h00; cc[ch] = 2'b00;
                    end else begin
                        c = ctrl_of(s);
                        if (c[2]) begin
                            dv[ch] = 1'b0; cc[ch] = c[1:0]; dd[ch] = 8'h00;
                            cnt[ch] = 0; have_cnt[ch] = 1;
                        end else begin
                            dv[ch] = 1'b1; cc[ch] = 2'b00; dd[ch] = tmds_data(s);
                            if (have_cnt[ch]) begin
                                e = spec_encode(dd[ch], cnt[ch]);
                                if (e !== s) begin
                                    n_bad = n_bad + 1;
                                    if (n_bad <= 3)
                                        err($sformatf("%0s: channel %0d word %0d is %s, which is not the DVI 1.0 symbol for any byte at running disparity %0d (byte 0x%02h would be %s). A data symbol arrived damaged",
                                                      when, ch, n, bin10(s), cnt[ch], dd[ch], bin10(e)));
                                    have_cnt[ch] = 0;
                                end else
                                    cnt[ch] = spec_cnt;
                            end
                        end
                    end
                end
                if (dv[0] !== dv[1] || dv[0] !== dv[2]) begin
                    o_ok[n] = 1'b0;
                    n_dis = n_dis + 1;
                    if (n_dis <= 3)
                        err($sformatf("%0s: word %0d: channels disagree on de (ch0 %s, ch1 %s, ch2 %s): data and control symbols must change on all three channels in the same pixel clock",
                                      when, n, dv[0] ? "data" : "control", dv[1] ? "data" : "control", dv[2] ? "data" : "control"));
                end
                o_de[n]  = dv[0];
                o_c0[n]  = cc[0];
                o_c12[n] = {cc[2], cc[1]};
                o_rgb[n] = {dd[2], dd[1], dd[0]};
            end
            if (n_bad > 3) err($sformatf("%0s: %0d damaged data symbols in total", when, n_bad));
            if (n_dis > 3) err($sformatf("%0s: channels disagreed on de in %0d words", when, n_dis));
            if (n_x > 2)   err($sformatf("%0s: %0d words had X bits", when, n_x));
        end
    endtask

    // Compare words [0, nwords) with the expected video at position
    // (n + k) mod NFRAME. Returns the mismatch count; prints if verbose.
    integer n_mis_de, n_mis_sync, n_mis_ctl, n_mis_rgb;
    task compare(input integer nwords, input integer k, input integer verbose, output integer mis);
        integer n, p, x, y;
        reg [23:0] er, gr;
        begin
            mis = 0;
            n_mis_de = 0; n_mis_sync = 0; n_mis_ctl = 0; n_mis_rgb = 0;
            for (n = 0; n < nwords; n = n + 1) begin
                p = (n + k) % NFRAME;
                x = p % H_TOT; y = p / H_TOT;
                if (!o_ok[n]) begin
                    mis = mis + 1;
                end else if (o_de[n] !== exp_de(p)) begin
                    mis = mis + 1; n_mis_de = n_mis_de + 1;
                    if (verbose && n_mis_de <= 3)
                        err($sformatf("video: pixel clock at x=%0d y=%0d: de=%b, expected %b", x, y, o_de[n], exp_de(p)));
                end else if (!o_de[n]) begin
                    if (o_c0[n] !== {exp_vs(p), exp_hs(p)}) begin
                        mis = mis + 1; n_mis_sync = n_mis_sync + 1;
                        if (verbose && n_mis_sync <= 3)
                            err($sformatf("video: blanking at x=%0d y=%0d: channel 0 sends {C1,C0} = {vsync,hsync} = %b, expected %b",
                                          x, y, o_c0[n], {exp_vs(p), exp_hs(p)}));
                    end
                    if (o_c12[n] !== 4'b0000) begin
                        mis = mis + 1; n_mis_ctl = n_mis_ctl + 1;
                        if (verbose && n_mis_ctl <= 3)
                            err($sformatf("video: blanking at x=%0d y=%0d: CTL bits on channel 2/1 are %b/%b; they must be 00 (only channel 0 carries the syncs)",
                                          x, y, o_c12[n][3:2], o_c12[n][1:0]));
                    end
                end else begin
                    er = exp_rgb(p); gr = o_rgb[n];
                    if (gr !== er) begin
                        mis = mis + 1; n_mis_rgb = n_mis_rgb + 1;
                        if (verbose && n_mis_rgb <= 3)
                            err($sformatf("video: pixel x=%0d y=%0d (bar %0d): RGB %02h %02h %02h, expected %02h %02h %02h%0s",
                                          x, y, x / 80, gr[23:16], gr[15:8], gr[7:0], er[23:16], er[15:8], er[7:0],
                                          (gr == {er[7:0], er[15:8], er[23:16]}) ? " (red and blue swapped?)" :
                                          (gr == 24'hE0E0E0 || gr[23:16] == 8'hE0 || gr[15:8] == 8'hE0 || gr[7:0] == 8'hE0) ?
                                              " (3'b111 must expand to 8'hFF: repeat the bits)" : ""));
                    end
                end
            end
        end
    endtask

    // If colors mismatch, see whether they are a constant number of pixels
    // early or late relative to de (a latency mismatch).
    task diagnose_shift(input integer nwords, input integer k);
        integer s, n, p, q, mis, best_s, best_mis, cnt;
        begin
            best_s = 0; best_mis = -1;
            for (s = -6; s <= 6; s = s + 1) begin
                mis = 0; cnt = 0;
                for (n = 1000; n < nwords - 1000 && cnt < 40000; n = n + 1) begin
                    p = (n + k) % NFRAME;
                    q = (n + k - s + NFRAME) % NFRAME;
                    if (exp_de(p) && o_ok[n]) begin
                        cnt = cnt + 1;
                        if (o_rgb[n] !== (exp_de(q) ? exp_rgb(q) : 24'h0)) mis = mis + 1;
                    end
                end
                if (best_mis < 0 || mis < best_mis) begin best_mis = mis; best_s = s; end
            end
            if (best_s != 0 && best_mis == 0)
                err($sformatf("video: the colors are %0d pixel clock%0s %0s than de and the syncs. Every signal needs the same number of register stages before the encoders",
                              best_s > 0 ? best_s : -best_s, (best_s == 1 || best_s == -1) ? "" : "s",
                              best_s > 0 ? "later" : "earlier"));
        end
    endtask

    // Line up on the first vsync falling edge seen in blanking.
    function integer find_vsync_k(input integer nwords);
        integer n, last_vs, found;
        begin
            find_vsync_k = -1; last_vs = -1; found = 0;
            for (n = 0; n < nwords && !found; n = n + 1)
                if (o_ok[n] && !o_de[n]) begin
                    if (last_vs == 1 && o_c0[n][1] == 1'b0) begin
                        find_vsync_k = ((V_VIS + V_FP) * H_TOT - n % NFRAME + NFRAME) % NFRAME;
                        found = 1;
                    end
                    last_vs = o_c0[n][1];
                end
        end
    endfunction

    // Count how often each control bit changes during blanking, to name
    // the usual sync wiring mistakes.
    task diagnose_syncs(input integer nwords, input string when);
        integer n, t0, t1, t2, l0, l1, l2, first;
        begin
            t0 = 0; t1 = 0; t2 = 0; first = 1; l0 = 0; l1 = 0; l2 = 0;
            for (n = 0; n < nwords; n = n + 1)
                if (o_ok[n] && !o_de[n]) begin
                    if (!first) begin
                        if (o_c0[n][0] != l0) t0 = t0 + 1;
                        if (o_c0[n][1] != l1) t1 = t1 + 1;
                        if (o_c12[n] != l2)   t2 = t2 + 1;
                    end
                    first = 0;
                    l0 = o_c0[n][0]; l1 = o_c0[n][1]; l2 = o_c12[n];
                end
            if (t2 > 0 && t0 + t1 == 0)
                err($sformatf("%0s: the syncs appear on the CTL bits of channel 1 or 2. They belong on channel 0 (blue), and channels 1 and 2 send C1C0 = 00 (check the encoder i_ctrl inputs and which serializer drives which pair)", when));
            else if (t0 + t1 == 0)
                err($sformatf("%0s: channel 0's C1/C0 never change: no sync pulses reach channel 0 (blue)", when));
            else if (t1 > t0)
                err($sformatf("%0s: channel 0's C1 changes more often than C0: hsync and vsync are swapped. Channel 0's i_ctrl is {vsync, hsync}", when));
        end
    endtask

    // After reset the frame position is unknown: find the hsync falling
    // edge for x, then try every line for y.
    task find_any_k(input integer nwords, output integer best_k);
        integer n, last_hs, x0, y, mis, best, k;
        begin
            best_k = -1; last_hs = -1; x0 = -1;
            for (n = 0; n < nwords && x0 < 0; n = n + 1)
                if (o_ok[n] && !o_de[n]) begin
                    if (last_hs == 1 && o_c0[n][0] == 1'b0) x0 = n;
                    last_hs = o_c0[n][0];
                end
            if (x0 >= 0) begin
                best = nwords + 1; best_k = -1;
                for (y = 0; y < V_TOT; y = y + 1) begin
                    k = (y * H_TOT + H_VIS + H_FP - x0 % NFRAME + NFRAME) % NFRAME;
                    compare(nwords, k, 0, mis);
                    if (mis < best) begin best = mis; best_k = k; end
                end
            end
        end
    endtask

    // ===================================================== main
    integer k, mis, edges0;
    initial begin
        $display("tb_hdmi: waiting for the clock pair (MMCM lock, then OSERDESE2 reset release)");
        find_clock(100000.0, "start-up");
        $display("tb_hdmi: clock pair running at %0.3f us; recording %0d pixel clocks", $realtime / 1000.0, NREC);
        record(NREC);

        // ---- frame checks
        check_clock_words(NREC, "frame");
        find_offset(NREC, "frame");
        decode(NREC, "frame");
        diagnose_syncs(NREC - 1, "video");
        k = find_vsync_k(NREC - 1);
        if (k < 0) begin
            finish_fail($sformatf("video: no vsync falling edge on channel 0 (C1) in %0d lines. The test sets the top's V_VISIBLE=%0d, V_FRONT=%0d, V_SYNC=%0d, V_BACK=%0d: pass them on to vga_timing",
                                  NREC / H_TOT, V_VIS, V_FP, V_SW, V_BP));
        end
        compare(NCHK, k, 1, mis);
        if (n_mis_rgb > 0) diagnose_shift(NCHK, k);
        if (mis > 0)
            err($sformatf("video: %0d of %0d pixel clocks wrong (de %0d, syncs %0d, CTL %0d, color %0d)",
                          mis, NCHK, n_mis_de, n_mis_sync, n_mis_ctl, n_mis_rgb));
        else
            $display("tb_hdmi: two frames OK: color bars in 640-pixel lines, syncs and blanking as expected");

        // ---- reset and recovery
        btn[0] = 1'b1;
        #100;
        edges0 = n_cp_edges;
        #100;
        btn[0] = 1'b0;
        if (n_cp_edges != edges0)
            err("reset: the clock pair kept toggling while btn[0] was held; btn[0] must reset the MMCM (clk_gen i_rst), which stops its clocks");
        find_clock(100000.0, "after reset");
        record(NRST);
        check_clock_words(NRST, "after reset");
        find_offset(NRST, "after reset");
        decode(NRST, "after reset");
        find_any_k(NRST - 1, k);
        if (k < 0)
            err("after reset: no hsync pulse on channel 0");
        else begin
            compare(NRST - 1, k, 1, mis);
            if (mis > 0)
                err($sformatf("after reset: %0d of %0d pixel clocks wrong", mis, NRST - 1));
            else
                $display("tb_hdmi: after btn[0] reset: video OK again");
        end

        if (n_diff > 2) err($sformatf("P/N not complementary in %0d bit samples", n_diff));
        if (errors == 0) $display("PASS: tb_hdmi (two frames and recovery after reset decode to the expected color bars)");
        else             $display("FAIL: tb_hdmi, %0d error%0s (first ones above)", errors, errors == 1 ? "" : "s");
        $finish;
    end

    // Watchdog: the run needs about 1.1 ms of simulated time.
    initial begin
        #3_000_000;
        $display("FAIL: tb_hdmi, watchdog timeout at %0t", $time);
        $finish;
    end
endmodule

`default_nettype wire
