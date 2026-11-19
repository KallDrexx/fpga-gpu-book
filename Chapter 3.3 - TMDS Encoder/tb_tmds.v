// tb_tmds -- self-checking testbench for Step 3.3 (TMDS encoder)
//
// Designs under test (see "Testbench requirements" in Chapter 3.3), both
// found by -y rtl/common:
//   tmds_encoder  rtl/common/tmds_encoder.v
//   tmds_decoder  rtl/common/tmds_decoder.v   (combinational)
//
// The testbench carries its own model of the DVI 1.0 encoder (the function
// spec_encode below, written straight from the spec's flowchart). Every
// symbol the encoder produces is compared with it, so an encoder/decoder
// pair that round-trips but is not DVI-compliant still fails.
//
// Part 1, decoder alone: every valid TMDS symbol, that is the model's code
//   for each of the 256 bytes in each of the 9 disparity states -8..+8,
//   plus the four control symbols, must decode back to its byte (o_de = 1)
//   or its control value (o_de = 0).
//
// Part 2, encoder, then decoder, on one long stream (100,000 pixel clocks):
//   * an exhaustive section: for each disparity state s in -8..+8 and each
//     byte b: control symbols, one byte that takes the counter from 0 to s,
//     then b, then one more byte,
//   * a random section shaped like video: data periods of 1 to 700 pixels
//     (random bytes, flat colors, two-color stripes, gradients, and the
//     bytes that push the disparity hardest) between control periods of 1
//     to 160 clocks with random or sync-like C1/C0.
//   The encoder's latency is measured: the testbench tries 1 to 8 clocks
//   on the first 20,000 symbols and uses the one that matches the model
//   best. Then it checks
//   * every output symbol equals the model's symbol (bit for bit),
//   * every control period sends the right one of the four control symbols,
//   * the running disparity of the output (ones minus zeros, summed over the
//     data symbols since the last control symbol) stays within -8..+8,
//   * the decoder, fed the encoder's output, returns the original de, data
//     and C1/C0 for every clock,
//   * no output bit is X or Z after the pipeline has filled.
//
// Outputs are sampled on the falling clock edge, and new inputs are applied
// 1 ns later. Ends with one PASS or FAIL line. Run with +vcd
// to write tb_tmds.vcd.

`timescale 1ns/1ps
`default_nettype none

module tb_tmds;
    localparam integer MAXN    = 100000;   // stimulus array size
    localparam integer LAT_WIN = 20000;    // symbols used to measure latency
    localparam integer MAX_LAT = 8;
    localparam integer BOUND   = 8;        // |running disparity| limit

    localparam [9:0] CTRL_00 = 10'b1101010100,   // 0x354
                     CTRL_01 = 10'b0010101011,   // 0x0AB
                     CTRL_10 = 10'b0101010100,   // 0x154
                     CTRL_11 = 10'b1010101011;   // 0x2AB

    integer errors = 0;
    integer seed   = 32'h3303;

    task err(input string msg);
        begin
            if (errors < 12) $display("ERROR: %0s", msg);
            errors = errors + 1;
        end
    endtask

    // ================================================== DVI 1.0 model
    // Encodes one pixel clock. cnt is the running disparity before the
    // symbol; spec_cnt holds it after. (Written from the DVI 1.0 flowchart,
    // Figure 3-5; deliberately not shared with the reader's code.)
    integer spec_cnt;
    function [9:0] spec_encode(input de, input [7:0] d, input [1:0] c,
                               input integer cnt);
        integer j, n1d, n1q, n0q;
        reg [8:0] qm;
        reg [9:0] q_out;
        begin
            if (!de) begin
                case (c)
                    2'b00: q_out = CTRL_00;
                    2'b01: q_out = CTRL_01;
                    2'b10: q_out = CTRL_10;
                    default: q_out = CTRL_11;
                endcase
                spec_cnt = 0;
            end else begin
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
                    q_out[9]   = ~qm[8];
                    q_out[8]   = qm[8];
                    q_out[7:0] = qm[8] ? qm[7:0] : ~qm[7:0];
                    if (qm[8] == 1'b0) spec_cnt = cnt + (n0q - n1q);
                    else               spec_cnt = cnt + (n1q - n0q);
                end else if ((cnt > 0 && n1q > n0q) || (cnt < 0 && n0q > n1q)) begin
                    q_out[9]   = 1'b1;
                    q_out[8]   = qm[8];
                    q_out[7:0] = ~qm[7:0];
                    spec_cnt   = cnt + 2 * qm[8] + (n0q - n1q);
                end else begin
                    q_out[9]   = 1'b0;
                    q_out[8]   = qm[8];
                    q_out[7:0] = qm[7:0];
                    spec_cnt   = cnt - 2 * (1 - qm[8]) + (n1q - n0q);
                end
            end
            spec_encode = q_out;
        end
    endfunction

    function integer ones10(input [9:0] v);
        integer j;
        begin
            ones10 = 0;
            for (j = 0; j < 10; j = j + 1) ones10 = ones10 + v[j];
        end
    endfunction

    function [8*12-1:0] bin10(input [9:0] v);   // "1101010100" (bit 9 first)
        integer j;
        begin
            bin10 = "";
            for (j = 9; j >= 0; j = j - 1)
                bin10 = {bin10[8*11-1:0], (v[j] === 1'b1) ? "1" :
                                          (v[j] === 1'b0) ? "0" : "x"};
        end
    endfunction

    // ======================================================= Part 1
    reg  [9:0] p1_sym;                   // starts X so the decoder evaluates
    wire       p1_de;
    wire [7:0] p1_data;
    wire [1:0] p1_ctrl;

    tmds_decoder u_dec_alone (
        .i_tmds (p1_sym),
        .o_de   (p1_de),
        .o_data (p1_data),
        .o_ctrl (p1_ctrl)
    );

    integer s, b, k;
    reg [9:0] sym;
    task run_part1;
        integer p1_err;
        begin
            p1_err = 0;
            for (k = 0; k < 4; k = k + 1) begin
                p1_sym = spec_encode(1'b0, 8'h00, k[1:0], 0);
                #1;
                if (p1_de !== 1'b0 || p1_ctrl !== k[1:0]) begin
                    p1_err = p1_err + 1;
                    if (p1_err <= 4)
                        err($sformatf("decoder: control symbol %s (C1C0=%b) decoded as de=%b ctrl=%b",
                                      bin10(p1_sym), k[1:0], p1_de, p1_ctrl));
                end
            end
            for (s = -BOUND; s <= BOUND; s = s + 2)
                for (b = 0; b < 256; b = b + 1) begin
                    p1_sym = spec_encode(1'b1, b[7:0], 2'b00, s);
                    #1;
                    if (p1_de !== 1'b1 || p1_data !== b[7:0]) begin
                        p1_err = p1_err + 1;
                        if (p1_err <= 4)
                            err($sformatf("decoder: symbol %s (byte 0x%02h at disparity %0d) decoded as de=%b data=0x%02h",
                                          bin10(p1_sym), b[7:0], s, p1_de, p1_data));
                    end
                end
            if (p1_err > 4) err($sformatf("decoder: %0d symbols decoded wrong in total", p1_err));
        end
    endtask

    // ======================================================= Part 2
    reg clk = 1'b0;
    always #20 clk = ~clk;                       // 25 MHz pixel clock

    reg        t_de   = 1'b0;
    reg  [7:0] t_data = 8'h00;
    reg  [1:0] t_ctrl = 2'b00;
    wire [9:0] e_tmds;
    wire       d_de;
    wire [7:0] d_data;
    wire [1:0] d_ctrl;

    tmds_encoder u_enc (
        .i_clk  (clk),
        .i_de   (t_de),
        .i_data (t_data),
        .i_ctrl (t_ctrl),
        .o_tmds (e_tmds)
    );

    // The decoder sees a copy of the encoder output, updated by the
    // testbench (starts X, so an always @* decoder is evaluated at once).
    reg  [9:0] dec_in;

    tmds_decoder u_dec (
        .i_tmds (dec_in),
        .o_de   (d_de),
        .o_data (d_data),
        .o_ctrl (d_ctrl)
    );

    // Stimulus (index n = applied on falling edge n).
    reg        st_de   [0:MAXN-1];
    reg  [7:0] st_data [0:MAXN-1];
    reg  [1:0] st_ctrl [0:MAXN-1];
    // Model output for stimulus n, and disparity before it.
    reg  [9:0] ex_sym  [0:MAXN-1];
    integer    ex_cnt  [0:MAXN-1];
    // Sampled on falling edge m (before stimulus m is applied).
    reg  [9:0] ob_sym  [0:MAXN-1];
    reg        ob_de   [0:MAXN-1];
    reg  [7:0] ob_data [0:MAXN-1];
    reg  [1:0] ob_ctrl [0:MAXN-1];
    integer    n_st = 0;

    task put(input de, input [7:0] d, input [1:0] c);
        begin
            st_de[n_st] = de; st_data[n_st] = d; st_ctrl[n_st] = c;
            n_st = n_st + 1;
        end
    endtask

    // Byte that takes the counter from 0 to s in one symbol (every state
    // in -8..+8 is one step from 0).
    function [7:0] byte_to_state(input integer s);
        integer v, found;
        reg [9:0] tmp;
        begin
            byte_to_state = 8'h00;
            found = 0;
            for (v = 0; v < 256; v = v + 1) begin
                tmp = spec_encode(1'b1, v[7:0], 2'b00, 0);
                if (!found && spec_cnt == s) begin
                    byte_to_state = v[7:0];
                    found = 1;
                end
            end
        end
    endfunction

    function [7:0] rnd8(input integer dummy);
        rnd8 = $random(seed);
    endfunction

    function integer urand(input integer lo, input integer hi);
        integer r;
        begin
            r = $random(seed);
            if (r < 0) r = -r;
            urand = lo + (r % (hi - lo + 1));
        end
    endfunction

    integer line, len, mode, i, n, m, lat, best_lat, best_mis, mis, rd, rd_max;
    reg [7:0] v0, v1, step, pre;
    reg [7:0] hard [0:7];
    reg [1:0] cc;

    task build_stimulus;
        begin
            hard[0] = 8'h00; hard[1] = 8'hFF; hard[2] = 8'hFE; hard[3] = 8'h01;
            hard[4] = 8'h7F; hard[5] = 8'h80; hard[6] = 8'h10; hard[7] = 8'hEF;

            for (i = 0; i < 16; i = i + 1) put(1'b0, 8'h00, 2'b00);

            // Exhaustive: every byte from every disparity state.
            for (s = -BOUND; s <= BOUND; s = s + 2) begin
                pre = byte_to_state(s);
                for (b = 0; b < 256; b = b + 1) begin
                    cc = b[1:0];
                    put(1'b0, rnd8(0), cc);
                    put(1'b0, rnd8(0), cc ^ 2'b11);
                    put(1'b1, pre, rnd8(0));
                    put(1'b1, b[7:0], rnd8(0));
                    put(1'b1, rnd8(0), rnd8(0));
                end
            end

            // Video-like random stream.
            line = 0;
            while (n_st < MAXN - 1000) begin
                len  = (urand(0, 9) == 0) ? urand(1, 4) : urand(1, 700);
                mode = urand(0, 5);
                v0 = rnd8(0); v1 = rnd8(0); step = urand(1, 3);
                for (i = 0; i < len; i = i + 1) begin
                    case (mode)
                        0, 1: put(1'b1, rnd8(0), rnd8(0));           // noise
                        2:    put(1'b1, v0, rnd8(0));                // flat color
                        3:    put(1'b1, i[0] ? v1 : v0, rnd8(0));    // stripes
                        4:    put(1'b1, v0 + i * step, rnd8(0));     // gradient
                        default: put(1'b1, hard[urand(0, 7)], rnd8(0));
                    endcase
                end
                len = (urand(0, 9) == 0) ? urand(1, 3) : urand(1, 160);
                mode = urand(0, 1);
                for (i = 0; i < len; i = i + 1) begin
                    if (mode == 0) put(1'b0, rnd8(0), rnd8(0));
                    else           put(1'b0, rnd8(0), {line[3], (i >= 16 && i < 112)});
                end
                line = line + 1;
            end
            for (i = 0; i < 16 + MAX_LAT; i = i + 1) put(1'b0, 8'h00, 2'b00);

            // Model outputs.
            rd = 0;
            for (n = 0; n < n_st; n = n + 1) begin
                ex_cnt[n] = rd;
                ex_sym[n] = spec_encode(st_de[n], st_data[n], st_ctrl[n], rd);
                rd = spec_cnt;
            end
        end
    endtask

    task run_stream;
        begin
            for (n = 0; n < n_st; n = n + 1) begin
                @(negedge clk);
                ob_sym[n]  = e_tmds;
                dec_in     = e_tmds;
                #1;
                ob_de[n]   = d_de;
                ob_data[n] = d_data;
                ob_ctrl[n] = d_ctrl;
                t_de   = st_de[n];
                t_data = st_data[n];
                t_ctrl = st_ctrl[n];
            end
        end
    endtask

    integer n_mis_ctrl, n_mis_data, n_rt, n_x, n_rd;
    task check_stream;
        begin
            // Measure latency: stimulus n should appear at sample n + lat.
            best_lat = 0; best_mis = n_st + 1;
            for (lat = 1; lat <= MAX_LAT; lat = lat + 1) begin
                mis = 0;
                for (n = 0; n + lat < n_st && n < LAT_WIN; n = n + 1)
                    if (ob_sym[n + lat] !== ex_sym[n]) mis = mis + 1;
                if (mis < best_mis) begin best_mis = mis; best_lat = lat; end
            end
            lat = best_lat;
            $display("tb_tmds: %0d pixel clocks, encoder latency measured as %0d clock%0s (%0d mismatching symbols)",
                     n_st, lat, lat == 1 ? "" : "s", best_mis);

            n_mis_ctrl = 0; n_mis_data = 0; n_rt = 0; n_x = 0; n_rd = 0;
            rd = 0; rd_max = 0;
            for (n = 0; n + lat < n_st; n = n + 1) begin
                m = n + lat;
                // X/Z
                if (^ob_sym[m] === 1'bx) begin
                    n_x = n_x + 1;
                    if (n_x <= 2)
                        err($sformatf("encoder: output %s has X or Z bits (stimulus %0d)",
                                      bin10(ob_sym[m]), n));
                end
                // Bit-for-bit against the DVI model.
                if (ob_sym[m] !== ex_sym[n]) begin
                    if (!st_de[n]) begin
                        n_mis_ctrl = n_mis_ctrl + 1;
                        if (n_mis_ctrl <= 3)
                            err($sformatf("encoder: control period C1C0=%b: sent %s, DVI control symbol is %s",
                                          st_ctrl[n], bin10(ob_sym[m]), bin10(ex_sym[n])));
                    end else begin
                        n_mis_data = n_mis_data + 1;
                        if (n_mis_data <= 4)
                            err($sformatf("encoder: byte 0x%02h at running disparity %0d: sent %s, DVI 1.0 gives %s",
                                          st_data[n], ex_cnt[n], bin10(ob_sym[m]), bin10(ex_sym[n])));
                    end
                end
                // Running disparity of what was actually sent.
                if (!st_de[n]) rd = 0;
                else           rd = rd + 2 * ones10(ob_sym[m]) - 10;
                if (rd > rd_max) rd_max = rd;
                if (-rd > rd_max) rd_max = -rd;
                if (rd > BOUND || rd < -BOUND) begin
                    n_rd = n_rd + 1;
                    if (n_rd <= 2)
                        err($sformatf("encoder: running disparity reached %0d after byte 0x%02h (limit +/-%0d)",
                                      rd, st_data[n], BOUND));
                end
                // Round trip through the decoder.
                if (ob_de[m] !== st_de[n] ||
                    (st_de[n] && ob_data[m] !== st_data[n]) ||
                    (!st_de[n] && ob_ctrl[m] !== st_ctrl[n])) begin
                    n_rt = n_rt + 1;
                    if (n_rt <= 3) begin
                        if (st_de[n])
                            err($sformatf("round trip: sent byte 0x%02h, decoder returned de=%b data=0x%02h (symbol %s)",
                                          st_data[n], ob_de[m], ob_data[m], bin10(ob_sym[m])));
                        else
                            err($sformatf("round trip: sent control C1C0=%b, decoder returned de=%b ctrl=%b (symbol %s)",
                                          st_ctrl[n], ob_de[m], ob_ctrl[m], bin10(ob_sym[m])));
                    end
                end
            end
            if (n_mis_ctrl + n_mis_data > 0)
                err($sformatf("encoder: %0d data and %0d control symbols differ from DVI 1.0",
                              n_mis_data, n_mis_ctrl));
            if (n_rd > 0)
                err($sformatf("encoder: running disparity out of bounds %0d times (max |disparity| %0d)",
                              n_rd, rd_max));
            if (n_rt > 0)
                err($sformatf("round trip: %0d of %0d clocks did not decode to the input", n_rt, n_st - lat));
        end
    endtask

    // ======================================================= main
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_tmds.vcd");
            $dumpvars(0, tb_tmds);
        end
        run_part1;
        build_stimulus;
        run_stream;
        check_stream;
        if (errors == 0)
            $display("PASS tb_tmds: 256 bytes x 9 disparity states + %0d-clock stream match DVI 1.0, latency %0d, |disparity| <= %0d, round trip OK",
                     n_st, lat, rd_max);
        else
            $display("FAIL tb_tmds: %0d error(s)", errors);
        $finish;
    end

    initial begin
        #20_000_000;   // 20 ms of simulated time (the stream needs 4 ms)
        $display("FAIL tb_tmds: timeout");
        $finish;
    end
endmodule

`default_nettype wire
