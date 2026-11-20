// tb_seq_divider.v -- self-checking testbench for seq_divider (Chapter 6.2).
//
// Five instances of the divider, each with its own parameters and vector file (sim/div_<cfg>.hex,
// written by sim/py/ref_divider.py):
//   wide   NUM_W=36 DEN_W=34 FRAC=16 Q_W=34   sized like a Chapter 6.3 gradient divide
//   recip  NUM_W=2  DEN_W=34 FRAC=32 Q_W=34   a reciprocal 1/den
//   vtx    NUM_W=26 DEN_W=16 FRAC=4  Q_W=20   sized like a Chapter 6.5 perspective divide
//   tiny   NUM_W=6  DEN_W=5  FRAC=2  Q_W=7    every (num, den) pair
//   int    NUM_W=5  DEN_W=5  FRAC=0  Q_W=8    every (num, den) pair, plain integers
//
// Each file starts with a header line {NUM_W, DEN_W, FRAC, Q_W, count}; the testbench checks the
// widths against its parameters and that it read exactly `count` vectors, so no counts live here.
//
// Per instance:
//   * latency = clocks from the edge that takes i_start to the edge after which o_done is seen.
//     Any latency from 1 to MAX_LAT is accepted, and it may differ from one division to the next.
//   * between i_start and o_done, o_busy must be high on every clock and o_done low; on the done clock
//     o_busy must be low (so a new start may be applied in that very clock: back to back).
//   * o_done is high for one clock per division and never otherwise; o_busy is low when idle.
//   * on the done clock o_quot and o_ovf must equal the model; between two done pulses they must hold.
//   * while busy, i_start is pulsed with junk operands (when the vector's flag allows): it must have no effect.
//   * idle clocks before a start (0..15, recorded in the file) carry junk operands with i_start low.
//
// Run from build/sim/:   vvp -n tb_seq_divider.vvp      (about 20 s; +dir=PATH/ for another hex folder)
// Prints a few problems, then one PASS or FAIL line.
`timescale 1ns/1ps
`default_nettype none

module div_unit_check #(
    parameter NAME = "wide", NUM_W = 36, DEN_W = 34, FRAC = 16, Q_W = 34
) (
    input  wire i_clk,
    output reg  o_fin = 0,
    output integer o_err,
    output integer o_nvec,
    output integer o_lmin,
    output integer o_lmax
);
    localparam WORD = 8 + NUM_W + DEN_W + Q_W + 1;
    localparam MAX_LAT = 200;

    reg                     start = 0;
    reg  signed [NUM_W-1:0] num = 0;
    reg  signed [DEN_W-1:0] den = 0;
    wire                    busy, done, ovf;
    wire signed [Q_W-1:0]   quot;

    seq_divider #(.NUM_W(NUM_W), .DEN_W(DEN_W), .FRAC(FRAC), .Q_W(Q_W)) dut (
        .i_clk(i_clk), .i_start(start), .i_num(num), .i_den(den),
        .o_busy(busy), .o_done(done), .o_quot(quot), .o_ovf(ovf));

    reg [WORD-1:0] w;
    reg [63:0]     hdr;
    reg [1023:0]   dir, fname;
    integer        fd, cnt;
    integer        seed = 12345;
    integer        v, g, c, nerr, lmin, lmax;
    reg            have_prev, finished_vec;
    reg [Q_W-1:0]  prev_q;
    reg            prev_o;

    reg  [3:0]            f_gap;
    reg                   f_junk;
    reg  [NUM_W-1:0]      f_num;
    reg  [DEN_W-1:0]      f_den;
    reg  [Q_W-1:0]        f_q;
    reg                   f_ovf;

    task unpack;                                      // split the vector word into its fields
        begin
            f_gap = w[WORD-1:WORD-4]; f_junk = w[WORD-8];
            f_num = w[Q_W+DEN_W+NUM_W : Q_W+DEN_W+1];
            f_den = w[Q_W+DEN_W : Q_W+1];
            f_q   = w[Q_W:1];
            f_ovf = w[0];
        end
    endtask

    task err(input [511:0] msg);
        begin
            nerr = nerr + 1;
            if (nerr <= 5) $display("  [%0s] error, vector %0d: %0s", NAME, v, msg);
        end
    endtask

    task junk_idle;
        begin
            start = 0; num = {$random(seed), $random(seed)}; den = {$random(seed), $random(seed)};
        end
    endtask

    task check_hold;                                  // the previous result must still be on the outputs
        begin
            if (have_prev && (quot !== prev_q || ovf !== prev_o)) err("o_quot/o_ovf changed between done pulses");
        end
    endtask

    initial begin
        nerr = 0; lmin = 1 << 30; lmax = 0; have_prev = 0; o_err = 0; o_nvec = 0; o_lmin = 0; o_lmax = 0;
        cnt = 0; v = -1;
        if (!$value$plusargs("dir=%s", dir)) dir = "../../sim/";
        $sformat(fname, "%0sdiv_%0s.hex", dir, NAME);
        fd = $fopen(fname, "r");
        if (fd == 0 || $fscanf(fd, "%h\n", hdr) != 1) begin
            $display("  [%0s] cannot read %0s (run sim/py/ref_divider.py sim)", NAME, fname);
            nerr = 1000;
        end else if (hdr[63:32] !== {NUM_W[7:0], DEN_W[7:0], FRAC[7:0], Q_W[7:0]} || hdr[31:0] == 0) begin
            $display("  [%0s] header %h does not match NUM_W=%0d DEN_W=%0d FRAC=%0d Q_W=%0d (regenerate the vectors)",
                     NAME, hdr, NUM_W, DEN_W, FRAC, Q_W);
            nerr = 1000;
        end
        if (nerr == 0) begin
            // power-up: idle, o_busy and o_done must be low
            repeat (4) begin
                @(negedge i_clk); junk_idle;
                if (busy !== 1'b0 || done !== 1'b0) err("o_busy/o_done not low at power-up");
            end
            v = 0;
            while (nerr < 5 && $fscanf(fd, "%h\n", w) == 1) begin
                unpack;
                // idle clocks
                for (g = 0; g < f_gap; g = g + 1) begin
                    junk_idle;
                    @(negedge i_clk);
                    if (busy !== 1'b0 || done !== 1'b0) err("o_busy or o_done high while idle");
                    check_hold;
                end
                // apply the start for one clock
                start = 1; num = f_num; den = f_den;
                if (busy !== 1'b0) err("o_busy high when about to start");
                @(negedge i_clk);
                c = 1; finished_vec = 0;
                while (!finished_vec) begin
                    if (done === 1'b1) begin
                        finished_vec = 1;
                        if (busy !== 1'b0) err("o_busy high on the done clock");
                        if (c < lmin) lmin = c;
                        if (c > lmax) lmax = c;
                        if (quot !== $signed(f_q) || ovf !== f_ovf) begin
                            err("wrong result");
                            if (nerr <= 5) $display("    num=%0d den=%0d expected q=%0d ovf=%0d got q=%0d ovf=%b",
                                $signed(f_num), $signed(f_den), $signed(f_q), f_ovf, quot, ovf);
                        end
                        have_prev = 1; prev_q = f_q; prev_o = f_ovf;
                    end else begin
                        if (done !== 1'b0) err("o_done is X");
                        if (busy !== 1'b1) err("o_busy not high while dividing");
                        if (c >= MAX_LAT) begin
                            err("no o_done in time"); finished_vec = 1;
                        end else begin
                            check_hold;
                            // junk start while busy (it must be ignored)
                            start = f_junk && ($random(seed) & 1);
                            num = {$random(seed), $random(seed)}; den = {$random(seed), $random(seed)};
                            @(negedge i_clk);
                            c = c + 1;
                        end
                    end
                end
                v = v + 1;
            end
            if (nerr == 0 && v != hdr[31:0]) begin
                $display("  [%0s] read %0d vectors, header says %0d (file truncated?)", NAME, v, hdr[31:0]);
                nerr = nerr + 1;
            end
            junk_idle;
            repeat (MAX_LAT) begin
                @(negedge i_clk);
                if (nerr == 0 && (done !== 1'b0 || busy !== 1'b0)) err("activity after the last division");
            end
        end
        if (fd != 0) $fclose(fd);
        o_err = nerr; o_nvec = v; o_lmin = lmin; o_lmax = lmax; o_fin = 1;
    end
endmodule

module tb_seq_divider;
    reg i_clk = 0;
    always #10 i_clk = ~i_clk;                        // 50 MHz; inputs driven and outputs sampled on the falling edge

    wire    f0, f1, f2, f3, f4;
    integer e0, e1, e2, e3, e4, n0, n1, n2, n3, n4;
    integer a0, a1, a2, a3, a4, b0, b1, b2, b3, b4;

    div_unit_check #(.NAME("wide"),  .NUM_W(36), .DEN_W(34), .FRAC(16), .Q_W(34))
        u_wide  (.i_clk(i_clk), .o_fin(f0), .o_err(e0), .o_nvec(n0), .o_lmin(a0), .o_lmax(b0));
    div_unit_check #(.NAME("recip"), .NUM_W(2),  .DEN_W(34), .FRAC(32), .Q_W(34))
        u_recip (.i_clk(i_clk), .o_fin(f1), .o_err(e1), .o_nvec(n1), .o_lmin(a1), .o_lmax(b1));
    div_unit_check #(.NAME("vtx"),   .NUM_W(26), .DEN_W(16), .FRAC(4),  .Q_W(20))
        u_vtx   (.i_clk(i_clk), .o_fin(f2), .o_err(e2), .o_nvec(n2), .o_lmin(a2), .o_lmax(b2));
    div_unit_check #(.NAME("tiny"),  .NUM_W(6),  .DEN_W(5),  .FRAC(2),  .Q_W(7))
        u_tiny  (.i_clk(i_clk), .o_fin(f3), .o_err(e3), .o_nvec(n3), .o_lmin(a3), .o_lmax(b3));
    div_unit_check #(.NAME("int"),   .NUM_W(5),  .DEN_W(5),  .FRAC(0),  .Q_W(8))
        u_int   (.i_clk(i_clk), .o_fin(f4), .o_err(e4), .o_nvec(n4), .o_lmin(a4), .o_lmax(b4));

    // Watchdog: a hang must FAIL.
    initial begin
        #800_000_000;
        $display("FAIL: watchdog (testbench did not finish)");
        $finish;
    end

    initial begin
        wait (f0 && f1 && f2 && f3 && f4);
        if (e0 + e1 + e2 + e3 + e4 == 0)
            $display("PASS: tb_seq_divider (wide %0d, recip %0d, vtx %0d, tiny %0d, int %0d divisions; latency wide %0d-%0d, recip %0d-%0d, vtx %0d-%0d, tiny %0d-%0d, int %0d-%0d)",
                     n0, n1, n2, n3, n4, a0, b0, a1, b1, a2, b2, a3, b3, a4, b4);
        else
            $display("FAIL: tb_seq_divider errors wide %0d, recip %0d, vtx %0d, tiny %0d, int %0d", e0, e1, e2, e3, e4);
        $finish;
    end
endmodule
`default_nettype wire
