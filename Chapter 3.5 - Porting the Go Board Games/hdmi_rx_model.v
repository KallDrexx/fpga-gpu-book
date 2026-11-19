// hdmi_rx_model -- a DVI receiver for testbenches (sim only, Chapter 3.5).
//
// Supplied by the book. Copy to sim/hdmi_rx_model.v. It is Chapter 3.4's
// tb_hdmi receiver turned into a module, so any test can watch the four
// TMDS pairs of an Arty top the way a monitor does and get the pixel
// stream back:
//
//     hdmi_rx_model u_rx (.i_clk_p(clk_p), .i_clk_n(clk_n),
//                         .i_d_p(d_p), .i_d_n(d_n),
//                         .o_clk(pclk), .o_locked(locked), .o_ok(ok),
//                         .o_de(de), .o_hsync(hs), .o_vsync(vs), .o_ctl(ctl),
//                         .o_red(r), .o_grn(g), .o_blu(b));
//
// How it works:
//   * Bit timing. It waits for the clock pair to fall four times in a row
//     at the T_PIX period (40 ns), then, after every falling edge of the
//     clock pair, samples all four lanes in the middle of each of the ten
//     bit times. The clock lane must carry 1111100000 (bit 0 first).
//   * Framing. It tries all ten bit offsets on channel 0 and locks to the
//     one that shows 32 control symbols in a row, as a monitor finds the
//     symbol boundaries during blanking. o_locked then goes high.
//   * Decoding. Every data symbol must be exactly the DVI 1.0 code of the
//     byte it decodes to, at the running disparity the receiver tracks
//     from the last control symbol; the three channels must agree on de.
//   * Output. One rising edge of o_clk per pixel clock, about 1.5 ns
//     before the next word starts, with the outputs already stable: sample
//     them on posedge o_clk (frame_dump.v works with BITS = 8). o_de is 1
//     for data symbols. o_hsync and o_vsync are channel 0's C0 and C1; they
//     hold their last control-period value during data, as in a monitor.
//     o_ctl is {channel 2 C1 C0, channel 1 C1 C0}, which a DVI source keeps
//     at 0. The colors are 0 during blanking.
//   * o_ok is 0 for a word that had X or Z bits, a damaged data symbol, or
//     channels that disagree on de. P/N pairs that are not complementary
//     and wrong clock words are counted (n_pn, n_clk_bad) but do not clear
//     o_ok. The counters (n_words, n_x, n_bad, n_dis, n_pn, n_clk_bad) are
//     integers a testbench can read as u_rx.n_bad, and clear_counts()
//     zeroes them.
//   * If the clock pair stops for more than 3 pixel clocks (an MMCM reset),
//     o_locked falls and the receiver starts over.
//
// It is a model of a receiver, not of a cable: it assumes the exact 40 ns
// period the book's MMCM model produces.

`timescale 1ns/1ps
`default_nettype none

module hdmi_rx_model #(
    parameter real T_PIX = 40.0          // pixel clock period, ns
) (
    input  wire       i_clk_p,
    input  wire       i_clk_n,
    input  wire [2:0] i_d_p,
    input  wire [2:0] i_d_n,
    output reg        o_clk    = 1'b0,
    output reg        o_locked = 1'b0,
    output reg        o_ok     = 1'b0,
    output reg        o_de     = 1'b0,
    output reg        o_hsync  = 1'b1,
    output reg        o_vsync  = 1'b1,
    output reg  [3:0] o_ctl    = 4'd0,
    output reg  [7:0] o_red    = 8'd0,
    output reg  [7:0] o_grn    = 8'd0,
    output reg  [7:0] o_blu    = 8'd0
);
    localparam real T_BIT = T_PIX / 10.0;
    localparam [9:0] CLK_WORD = 10'b1111100000;
    localparam [9:0] CTRL_00 = 10'b1101010100, CTRL_01 = 10'b0010101011,
                     CTRL_10 = 10'b0101010100, CTRL_11 = 10'b1010101011;

    integer n_words = 0, n_x = 0, n_bad = 0, n_dis = 0, n_pn = 0, n_clk_bad = 0;
    integer offset = 0;                     // data framing, bits after the clock's fall

    task clear_counts;
        begin
            n_words = 0; n_x = 0; n_bad = 0; n_dis = 0; n_pn = 0; n_clk_bad = 0;
        end
    endtask

    // ------------------------------------------------ DVI 1.0 reference
    integer spec_cnt;
    function [9:0] spec_encode(input [7:0] d, input integer cnt);
        integer j, n1d, n1q, n0q;
        reg [8:0] qm;
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
                spec_encode = {~qm[8], qm[8], qm[8] ? qm[7:0] : ~qm[7:0]};
                spec_cnt = qm[8] ? cnt + (n1q - n0q) : cnt + (n0q - n1q);
            end else if ((cnt > 0 && n1q > n0q) || (cnt < 0 && n0q > n1q)) begin
                spec_encode = {1'b1, qm[8], ~qm[7:0]};
                spec_cnt = cnt + 2 * qm[8] + (n0q - n1q);
            end else begin
                spec_encode = {1'b0, qm[8], qm[7:0]};
                spec_cnt = cnt - 2 * (1 - qm[8]) + (n1q - n0q);
            end
        end
    endfunction

    function [2:0] ctrl_of(input [9:0] s);      // {valid, C1, C0}
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

    // ------------------------------------------------ clock pair tracking
    real    t_fall = -1.0, t_fall_prev = -1.0;
    integer good_falls = 0;
    reg     cp_q = 1'b0;
    event   ev_word;
    always @(i_clk_p) begin
        if (i_clk_p !== 1'b0 && i_clk_p !== 1'b1)
            good_falls = 0;
        else if (i_clk_p === 1'b0 && cp_q === 1'b1) begin
            t_fall_prev = t_fall;
            t_fall      = $realtime;
            if (t_fall_prev >= 0.0 && t_fall - t_fall_prev > T_PIX - 0.01 &&
                t_fall - t_fall_prev < T_PIX + 0.01)
                good_falls = good_falls + 1;
            else
                good_falls = 0;
            if (good_falls >= 4) -> ev_word;
        end
        cp_q = i_clk_p;
    end

    // Lost clock: start over.
    reg     aligned = 1'b0;
    integer run [0:9];
    integer cnt [0:2];
    reg     have_cnt [0:2];
    integer i;
    task unlock;
        begin
            aligned = 1'b0; o_locked = 1'b0; o_ok = 1'b0;
            for (i = 0; i < 10; i = i + 1) run[i] = 0;
            for (i = 0; i < 3; i = i + 1) begin cnt[i] = 0; have_cnt[i] = 1'b0; end
        end
    endtask
    initial unlock;
    always begin
        #(T_PIX);
        if (aligned && $realtime - t_fall > 3.0 * T_PIX) unlock;
    end

    // ------------------------------------------------ one word per clock fall
    reg [9:0] prev [0:2];
    reg [9:0] w [0:2];
    reg [9:0] wc, s;
    reg [19:0] two;
    reg [2:0] c;
    reg       dv [0:2];
    reg [7:0] dd [0:2];
    reg [1:0] cc [0:2];
    reg       bad;
    integer   b, ch, o;
    always @(ev_word) begin
        for (b = 0; b < 10; b = b + 1) begin
            #(b == 0 ? T_BIT / 2.0 : T_BIT);
            if (b == 0) o_clk = 1'b0;
            wc[b] = i_clk_p;
            w[0][b] = i_d_p[0]; w[1][b] = i_d_p[1]; w[2][b] = i_d_p[2];
            if ({i_clk_n, i_d_n} !== ~{i_clk_p, i_d_p}) n_pn = n_pn + 1;
        end

        if (!aligned) begin
            // Find the bit offset at which channel 0 shows a run of control symbols.
            for (o = 0; o < 10; o = o + 1) begin
                two = {w[0], prev[0]};
                s = two >> o;
                if (ctrl_of(s) != 3'b000) run[o] = run[o] + 1;
                else run[o] = 0;
                if (!aligned && run[o] >= 32) begin
                    aligned = 1'b1;
                    offset  = o;
                end
            end
            if (aligned) o_locked = 1'b1;
        end else begin
            n_words = n_words + 1;
            if (wc !== CLK_WORD) n_clk_bad = n_clk_bad + 1;
            bad = 1'b0;
            for (ch = 0; ch < 3; ch = ch + 1) begin
                two = {w[ch], prev[ch]};
                s = two >> offset;
                dv[ch] = 1'b0; dd[ch] = 8'd0; cc[ch] = 2'b00;
                if (^s === 1'bx) begin
                    bad = 1'b1; n_x = n_x + 1; have_cnt[ch] = 1'b0;
                end else begin
                    c = ctrl_of(s);
                    if (c[2]) begin
                        cc[ch] = c[1:0]; cnt[ch] = 0; have_cnt[ch] = 1'b1;
                    end else begin
                        dv[ch] = 1'b1; dd[ch] = tmds_data(s);
                        if (have_cnt[ch]) begin
                            if (spec_encode(dd[ch], cnt[ch]) !== s) begin
                                bad = 1'b1; n_bad = n_bad + 1; have_cnt[ch] = 1'b0;
                            end else
                                cnt[ch] = spec_cnt;
                        end
                    end
                end
            end
            if (dv[0] !== dv[1] || dv[0] !== dv[2]) begin
                bad = 1'b1; n_dis = n_dis + 1;
            end
            o_ok  = !bad;
            o_de  = dv[0];
            if (!dv[0]) begin
                o_hsync = cc[0][0];
                o_vsync = cc[0][1];
            end
            o_ctl = {cc[2], cc[1]};
            o_red = dd[2]; o_grn = dd[1]; o_blu = dd[0];
            #(T_BIT / 8.0) o_clk = 1'b1;
        end
        prev[0] = w[0]; prev[1] = w[1]; prev[2] = w[2];
    end
endmodule

`default_nettype wire
